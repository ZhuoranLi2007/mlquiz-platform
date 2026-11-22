"""Shared scheduling, fallback and publication for self/teacher retests.

Student requests create private owned attempts, never teacher publications.
The graph and APIs use the same scheduler and evidence projection.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from sqlalchemy import select, text

from backend.app.models import entities as E
from backend.app.services import (answer_pipeline, attempt_service, bank_service,
    adaptive_learning, paper_builder_service,
    learning_evidence as evidence, learning_resources, model_provider,
    release_service, targeted_retest)


def _fingerprint(stem):
    return re.sub(r'\W+', '', str(stem)).lower()


def family(q):
    return q.question_family_id or hashlib.sha256(_fingerprint(q.stem).encode()).hexdigest()


def _scope_key(student_id, source_id):
    return f'{student_id}:{source_id or "baseline"}'


def resolve_scopes(db, request):
    """Resolve the actual source papers before reserving any generation job."""
    scope = request.get('scope_type', 'student_attempt')
    sid = request.get('student_id', '')
    source_id = request.get('attempt_id', '')
    av = request.get('assessment_version_id', '')
    classroom_id = request.get('classroom_id', '')
    if scope == 'class_assessment':
        version = db.get(E.AssessmentVersion, av)
        assessment = db.get(E.Assessment, version.assessment_id) if version else None
        if not assessment or assessment.classroom_id != classroom_id:
            raise ValueError('所选试卷不属于当前课堂')
        students = list(db.execute(select(E.Student).join(E.ClassroomStudent,
            E.ClassroomStudent.student_id == E.Student.student_id).where(
                E.ClassroomStudent.classroom_id == classroom_id,
                E.ClassroomStudent.status == 'active', E.Student.active.is_(True))).scalars())
    else:
        student = db.get(E.Student, sid)
        if not student or not student.active:
            raise ValueError('学生不存在或已停用')
        students = [student]
    result = []
    for student in students:
        account = db.get(E.User, student.linked_user_id) if student.linked_user_id else None
        if not account or not account.active or account.role != 'student':
            continue
        if source_id:
            source = db.get(E.Attempt, source_id)
            if (not source or source.student_id != student.student_id or source.status != 'submitted'
                    or not source.is_effective or source.attendance_status == 'absent'):
                raise ValueError('复测原卷必须是该学生的有效已提交答卷')
            if source.retest_of_attempt_id:
                source = db.get(E.Attempt, source.retest_of_attempt_id)
        else:
            stmt = select(E.Attempt).where(E.Attempt.student_id == student.student_id,
                E.Attempt.status == 'submitted', E.Attempt.is_effective.is_(True),
                E.Attempt.attendance_status == 'attended', E.Attempt.mode != 'retest')
            if av:
                stmt = stmt.where(E.Attempt.assessment_version_id == av)
            if scope == 'student_history':
                stmt = stmt.join(E.AssessmentVersion, E.Attempt.assessment_version_id == E.AssessmentVersion.assessment_version_id).join(
                    E.Assessment, E.AssessmentVersion.assessment_id == E.Assessment.assessment_id).where(E.Assessment.classroom_id == classroom_id)
                if request.get('date_from'):
                    stmt = stmt.where(E.Attempt.submitted_at >= request['date_from'])
                if request.get('date_to'):
                    stmt = stmt.where(E.Attempt.submitted_at < request['date_to'] + 'T23:59:59.999999')
            source = db.execute(stmt.order_by(E.Attempt.submitted_at.desc())).scalars().first()
        membership = db.execute(select(E.ClassroomStudent).where(
            E.ClassroomStudent.student_id == student.student_id,
            E.ClassroomStudent.status == 'active')).scalars().first()
        source_version = db.get(E.AssessmentVersion, source.assessment_version_id) if source and source.assessment_version_id else None
        source_assessment = db.get(E.Assessment, source_version.assessment_id) if source_version else None
        result.append({'student_id': student.student_id, 'user_id': account.user_id,
            'source_id': source.attempt_id if source else '',
            'assessment_version_id': source.assessment_version_id if source else av,
            'classroom_id': (source_assessment.classroom_id if source_assessment else classroom_id or
                             (membership.classroom_id if membership else '')),
            'key': _scope_key(student.student_id, source.attempt_id if source else '')})
    if not result:
        raise ValueError('没有可接收复测的学生账号')
    return result


def history_for(db, sid, source_id):
    if source_id:
        return targeted_retest.list_cycles(db, source_attempt_id=source_id, student_id=sid)
    # An unfinished baseline is resumable even before any source exam exists.
    rows = db.execute(select(E.TargetedRetestCycle).where(
        E.TargetedRetestCycle.student_id == sid,
        E.TargetedRetestCycle.source_attempt_id == E.TargetedRetestCycle.retest_attempt_id
    ).order_by(E.TargetedRetestCycle.created_at)).scalars().all()
    return [targeted_retest.cycle_detail(db, r) for r in rows]


def active_cycle(history):
    return next((r for r in reversed(history) if r['status'] in {'published', 'opened'}), None)


def _baseline(items):
    result = {}
    for item in items:
        kp = evidence.primary_kp(item)
        if kp:
            result[kp] = result.get(kp, False) or (item.get('evidence_status') == 'scored' and item.get('correct') is False)
    return result


def public_summary(history, baseline=None):
    concepts = evidence.summarize(history, baseline)
    latest = history[-1] if history else None
    final = next((r for r in reversed(history) if r['status'] in targeted_retest.FINAL_STATUSES), None)
    passed = next((r for r in reversed(history) if r['status'] == 'passed'), None)
    active = active_cycle(history)
    pending = any(r['status'] == 'scoring_pending' for r in history)
    def reference(row):
        return {k: row.get(k) for k in ('cycle_id', 'round', 'status', 'release_id', 'retest_attempt_id',
            'accuracy', 'correct_count', 'question_count', 'completed_at', 'published_at', 'source_counts', 'retest_kind')} if row else None
    return {'rule_version': evidence.RULE_VERSION,
            'task_state': active['status'] if active else 'scoring_pending' if pending else 'ready',
            'round_result': reference(final), 'last_passed': reference(passed),
            'latest_task': reference(active or latest), 'concept_results': concepts,
            'available_actions': (['continue','retry_generation'] if active.get('retest_kind')=='practice' else ['continue']) if active else ['refresh', 'practice'] if pending else ['start'],
            'remaining_kp_ids': [r['kp_id'] for r in concepts if r['state'] not in {'achieved', 'delayed_confirmed'}]}


def attach_single(db, payload):
    history = payload.get('retest_history') or []
    baseline = _baseline(payload.get('items') or [])
    summary = public_summary(history, baseline)
    for row in summary['concept_results']:
        kp = db.get(E.KnowledgePoint, row['kp_id'])
        row['name'] = kp.name if kp else '课程知识点'
    payload['learning_loop'] = summary
    # Refresh all current concepts, including concepts first answered wrongly
    # during a retest. Resource presence does not depend on the old wrong list.
    wanted = list(dict.fromkeys([*summary['remaining_kp_ids'], *baseline,
        *(kp for r in history for kp in r.get('kp_ids', []))]))
    payload['resources'] = learning_resources.recommend(db, wanted, limit=100)
    plan = payload.setdefault('retest_plan', {})
    plan.update(ready=True, item_count=5, available_actions=summary['available_actions'],
                reason='可自主复测；已有任务可继续，待评分时可先练习。')
    return payload


def attach_scope(db, payload, *, student_id='', assessment_version_id='', classroom_id='', source_ids=None):
    stmt = select(E.TargetedRetestCycle)
    if student_id:
        stmt = stmt.where(E.TargetedRetestCycle.student_id == student_id)
    if assessment_version_id:
        stmt = stmt.where(E.TargetedRetestCycle.source_assessment_version_id == assessment_version_id)
    if source_ids is not None:
        stmt = stmt.where(E.TargetedRetestCycle.source_attempt_id.in_(source_ids))
    if classroom_id:
        stmt = stmt.join(E.ClassroomStudent, E.ClassroomStudent.student_id == E.TargetedRetestCycle.student_id).where(
            E.ClassroomStudent.classroom_id == classroom_id, E.ClassroomStudent.status == 'active')
    grouped = {}
    for cycle in db.execute(stmt.order_by(E.TargetedRetestCycle.created_at, E.TargetedRetestCycle.round_no)).scalars().unique():
        grouped.setdefault((cycle.student_id, cycle.source_attempt_id), []).append(targeted_retest.cycle_detail(db, cycle))
    rows = []
    for (sid, source), history in grouped.items():
        student = db.get(E.Student, sid)
        attempt = db.get(E.Attempt, source)
        paper = db.get(E.Paper, attempt.paper_id) if attempt else None
        # The same original-paper evidence must seed every scope, including
        # legacy rounds that cannot independently establish mastery.
        baseline = {}
        if attempt and attempt.status == 'submitted':
            from backend.app.services import classroom_diagnosis
            original = classroom_diagnosis.student_attempt(db, sid,
                attempt.assessment_version_id, attempt_id=source)
            baseline = _baseline(original.get('items') or [])
        summary = public_summary(history, baseline)
        for concept in summary['concept_results']:
            kp = db.get(E.KnowledgePoint, concept['kp_id'])
            concept['name'] = kp.name if kp else '课程知识点'
        rows.append({'student_id': sid, 'student_code': student.display_code if student else sid,
            'source_attempt_id': source, 'source_title': paper.title if paper else '课程自测',
            **summary})
    counts = {'pending': 0, 'scoring_pending': 0, 'passed': 0, 'not_passed': 0, 'practice': 0}
    # Count students, not cycles/papers. Any pending task takes precedence;
    # otherwise any failing source does, so a passed paper cannot mask another.
    by_student = {}
    for row in rows:
        state = row['task_state']
        outcome = (row.get('round_result') or {}).get('status')
        bucket = ('pending' if state in {'published','opened'} else 'scoring_pending' if state == 'scoring_pending'
                  else outcome if outcome in {'passed','not_passed'} else 'practice')
        old = by_student.get(row['student_id'])
        order = ['pending', 'scoring_pending', 'not_passed', 'practice', 'passed']
        if old is None or order.index(bucket) < order.index(old):
            by_student[row['student_id']] = bucket
    for bucket in by_student.values():
        counts[bucket] += 1
    payload['learning_loop_summary'] = {'counts': counts, 'rows': rows, 'student_count': len(by_student)}
    current_targets = list(dict.fromkeys([*(payload.get('weak_kp_ids') or []),
        *(kp for row in rows for kp in row['remaining_kp_ids'])]))
    if current_targets:
        payload['resources'] = learning_resources.recommend(db, current_targets, limit=100)
    payload.setdefault('retest_plan', {}).update(ready=True, item_count=5)
    return payload


def plan_scope(db, scope, requested=None, practice=False, retry_generation=False):
    from backend.app.services import classroom_diagnosis
    source = db.get(E.Attempt, scope['source_id']) if scope['source_id'] else None
    payload = classroom_diagnosis.student_attempt(db, scope['student_id'], source.assessment_version_id,
        attempt_id=source.attempt_id) if source else {'items': [], 'resources': []}
    history = history_for(db, scope['student_id'], scope['source_id'])
    existing = active_cycle(history)
    if retry_generation and existing and existing.get('retest_kind')=='practice': existing=None
    baseline = _baseline(payload.get('items') or [])
    available = list(dict.fromkeys([*(payload.get('weak_kp_ids') or []), *(k for k,v in baseline.items() if v),
                                  *(kp for row in history for kp in row.get('kp_ids', []))]))
    if requested:
        available = list(dict.fromkeys([*available, *(kp for kp in requested if kp in baseline)]))
    if not available:
        available = list(baseline)
    if not available:
        if requested:
            available = [k for k in requested if db.get(E.KnowledgePoint, k)]
        elif scope.get('assessment_version_id'):
            version = db.get(E.AssessmentVersion, scope['assessment_version_id'])
            if version:
                available = list(dict.fromkeys(evidence.primary_kp(i.snapshot or {}) for i in db.execute(
                    select(E.PaperItem).where(E.PaperItem.paper_id == version.paper_id)).scalars()))
    targets, remaining = evidence.select_targets(available, history, baseline, requested)
    if source and requested and any(kp not in available for kp in requested):
        raise ValueError('所选知识点缺少当前原卷或关联复测证据')
    if requested:
        targets = list(dict.fromkeys(kp for kp in requested if kp in available))[:2]
        states = {row['kp_id']: row for row in evidence.summarize(history, baseline)}
        remaining = [kp for kp in available if kp not in targets
                     and states.get(kp, {}).get('state') not in {'achieved', 'delayed_confirmed'}]
    decisions = {row["kp_id"]: row for row in evidence.summarize(history, baseline)}
    profiles = {kp: adaptive_learning.profile(decisions.get(kp, {})) for kp in targets}
    chapters = {i.get('chapter_id') or (i.get('chapter') or {}).get('chapter_id') for i in payload.get('items', [])
                if evidence.primary_kp(i) in targets}
    return {**scope, 'payload': payload, 'history': history, 'existing': existing, 'retry_generation':retry_generation,
            'targets': targets, 'remaining': remaining, 'adaptive_profiles': profiles,
            'chapters': [c for c in chapters if c],
            'kind': 'practice' if practice or any(r['status']=='scoring_pending' for r in history) else
                    'formal' if source else 'baseline',
            'round': max((r['round'] for r in history), default=0)+1}


def _prior_questions(db, sid):
    rows = db.execute(select(E.PaperItem).join(E.Attempt, E.PaperItem.paper_id == E.Attempt.paper_id).where(
        E.Attempt.student_id == sid)).scalars().all()
    ids = {r.question_id for r in rows}
    families = {str((r.snapshot or {}).get('question_family_id') or '') for r in rows} - {''}
    stems = [_fingerprint((r.snapshot or {}).get('stem') or '') for r in rows]
    student = db.get(E.Student, sid)
    if student and student.linked_user_id:
        ids |= attempt_service._seen_ids(db, student.linked_user_id)
    return ids, families, [s for s in stems if s]


def prepare_questions(db, plan, candidate_ids, thread_id):
    from backend.app.services.automatic_retest import _validate_candidate
    ids, families, stems = _prior_questions(db, plan['student_id'])
    targets = plan['targets']
    quotas = dict(zip(targets, [5] if len(targets)==1 else [3,2]))
    all_stems = list(db.execute(select(E.Question.question_id,E.Question.stem).where(
        E.Question.question_id.not_in(candidate_ids or ['']))).all())
    choices = {k: [] for k in targets}
    rejected = []
    def is_new(q):
        return q.question_id not in ids and family(q) not in families and not any(
            SequenceMatcher(None, _fingerprint(q.stem), stem).ratio() >= .78 for stem in stems)
    for qid in dict.fromkeys(candidate_ids):
        try:
            q = answer_pipeline.latest_question(db, qid)
            if q and not q.chapter_id and len(plan['chapters'])==1:
                q.chapter_id=plan['chapters'][0]
            q, check, call_id = _validate_candidate(db, qid=qid, prior_stems=all_stems,
                chapter_ids=plan['chapters'], kp_ids=targets, thread_id=thread_id)
            choices[q.main_kp_id].append((q, 'ai_generated', is_new(q), check, call_id))
            all_stems.append((q.question_id,q.stem))
        except Exception as exc:
            rejected.append({'question_id': qid, 'reason': model_provider.redact(str(exc)) if hasattr(model_provider,'redact') else type(exc).__name__})
    # The fallback needs no model call: only already reliable objective keys.
    bank = db.execute(select(E.Question).where(E.Question.main_kp_id.in_(targets),
        E.Question.active.is_(True), E.Question.review_status.in_(['verified','auto_checked']),
        E.Question.answer_status.in_(['verified','ai_auto_checked']), E.Question.scope_class=='in_scope',
        E.Question.question_type.in_(['single_choice','multi_choice','true_false'])
    ).order_by(E.Question.question_id, E.Question.question_version.desc())).scalars().all()
    seen = set(candidate_ids)
    for q in bank:
        if q.question_id in seen or (plan['chapters'] and q.chapter_id not in plan['chapters']):
            continue
        seen.add(q.question_id)
        if not paper_builder_service.quiz_gate(q)[0]:
            continue
        if attempt_service.snapshot_score_authority({'origin':q.origin,'answer_origin':q.answer_origin,
                'answer_status':q.answer_status}) not in {'paper_snapshot','teacher_key','ai_auto_checked'}:
            continue
        choices[q.main_kp_id].append((q, 'bank_selected', is_new(q), {}, ''))
    selected, selected_families, selected_stems = [], set(), []
    def angle(row):
        q, _, _, check, _ = row
        return check.get('assessment_angle') or f'{q.question_type}:{q.cognitive_level or "未标注"}'
    for kp in targets:
        # Prefer distinct assessment angles, then fill the target quota.
        rows = choices[kp]
        angles = set()
        for _ in range(quotas[kp]):
            suitable = [r for r in rows if r[2] and family(r[0]) not in selected_families
                and not any(SequenceMatcher(None,_fingerprint(r[0].stem),s).ratio()>=.78 for s in selected_stems)]
            if not suitable:
                break
            preference = plan.get('adaptive_profiles', {}).get(kp, {})
            bands = {'easy': 0, 'medium': 1, 'hard': 2}
            target_band = bands.get(paper_builder_service.difficulty_band(preference.get('difficulty', '中等')), 1)
            row = min(suitable, key=lambda r: (
                angle(r) in angles,
                abs(bands.get(paper_builder_service.difficulty_band(r[0].difficulty), 1) - target_band),
                r[0].cognitive_level != preference.get('cognitive_level', '应用'),
                r[1] != 'ai_generated'))
            selected.append(row); selected_families.add(family(row[0])); angles.add(angle(row))
            selected_stems.append(_fingerprint(row[0].stem))
    formal = len(selected)==5 and all(sum(r[0].main_kp_id==k for r in selected)==n for k,n in quotas.items())
    if not formal or plan['kind']=='practice':
        plan['kind']='practice'
        for kp in targets:
            for row in choices[kp]:
                if len(selected)>=5:
                    break
                if row[0].question_id not in {r[0].question_id for r in selected}:
                    selected.append(row)
    manifest, counts = {}, {'ai_generated':0,'bank_selected':0,'original_reused':0}
    for q, source, new, check, call_id in selected:
        counts[source if new else 'original_reused']+=1
        manifest[q.question_id]={'kp_id':q.main_kp_id,'new_to_student':new,
            'family_key':family(q), 'assessment_angle':angle((q,source,new,check,call_id)), 'source':source,
            'adaptive_stage': plan.get('adaptive_profiles', {}).get(q.main_kp_id, {}).get('stage'),
            'actual_difficulty': q.difficulty,
            'target_difficulty': plan.get('adaptive_profiles', {}).get(q.main_kp_id, {}).get('difficulty')}
        if source=='ai_generated':
            q.answer_status='ai_auto_checked';q.review_status='auto_checked';q.answer_origin='deepseek_generated'
            q.explanation=check['explanation'];q.active=True
            try:notes=json.loads(q.notes or '{}')
            except (ValueError,TypeError):notes={}
            q.notes=json.dumps({**notes,'validation':evidence.RULE_VERSION,'call_id':call_id,
                'independent_solution':check,'checked_at':E.utcnow()},ensure_ascii=False)
    return [r[0] for r in selected], manifest, counts, rejected


def publish(db, plan, questions, manifest, counts, *, actor_user_id, thread_id):
    if not questions:
        return {'status':'no_questions','reason':'暂缺答案可靠的题目，可先阅读课程资料或重试新题。',
                'student_id':plan['student_id'],'available_actions':['retry','study'], 'target_kp_ids':plan['targets']}
    # Model calls may commit. Recheck the authoritative active task under a
    # writer lock immediately before the atomic paper/attempt/cycle publication.
    db.commit(); db.execute(text('BEGIN IMMEDIATE'))
    prior = list(db.execute(select(E.TargetedRetestCycle).where(
        E.TargetedRetestCycle.student_id==plan['student_id'],
        E.TargetedRetestCycle.source_attempt_id==plan['source_id'] if plan['source_id'] else
        E.TargetedRetestCycle.source_attempt_id==E.TargetedRetestCycle.retest_attempt_id)).scalars())
    for row in prior:
        targeted_retest.refresh_cycle(db,row)
        if row.status in {'published','opened'} and not (plan.get('retry_generation') and row.retest_kind=='practice'):
            db.commit()
            return resume_result(targeted_retest.cycle_detail(db,row), plan['student_id'])
    number=max((r.round_no for r in prior),default=0)+1
    title=f'第{number}轮 · '+('巩固练习' if plan['kind']=='practice' else '基线自测' if plan['kind']=='baseline' else '专项复测')
    paper=bank_service.create_paper_from_questions(db,questions,title=title,mode='retest',
        source='learning_loop',user_id=actor_user_id,scores=[2.0]*len(questions),
        blueprint={'source':evidence.RULE_VERSION,'source_counts':counts,'kp_ids':plan['targets'],
                   'adaptive_profiles': plan.get('adaptive_profiles', {})},commit=False)
    actor=db.get(E.User,actor_user_id)
    release=None;attempt_id=''
    if actor and actor.role=='teacher' and plan['source_id']:
        release=release_service.publish_release(db,teacher_user_id=actor_user_id,student_ids=[plan['student_id']],
            title=title,paper_id=paper['paper_id'],classroom_id=plan['classroom_id'],release_type='retest',
            source_attempt_id=plan['source_id'],workflow_run_id=thread_id,
            idempotency_key=f"loop:{plan['key']}:{number}",commit=False)
    else:
        if not actor or (actor.role=='student' and actor.user_id!=plan['user_id']):
            raise PermissionError('只能为本人创建自主复测')
        version=release_service.ensure_version_for_paper(db,paper['paper_id'],plan['classroom_id'],commit=False)
        started=attempt_service.create_attempt(db,paper['paper_id'],plan['user_id'],mode='retest',
            student_id=plan['student_id'],assessment_version_id=version.assessment_version_id,commit=False)
        attempt_id=started['attempt_id']
        if not plan['source_id']:
            plan['source_id']=attempt_id
            plan['assessment_version_id']=version.assessment_version_id
            # This real first self-test is the baseline, not a fabricated old exam.
            plan['kind']='baseline' if len(questions)==5 else 'practice'
        else:
            db.get(E.Attempt,attempt_id).retest_of_attempt_id=plan['source_id']
    cycle=targeted_retest.create_cycle(db,student_id=plan['student_id'],source_attempt_id=plan['source_id'],
        source_assessment_version_id=plan['assessment_version_id'],diagnosis_id='',
        release_id=release['release_id'] if release else '',workflow_run_id=thread_id,round_no=number,
        question_ids=[q.question_id for q in questions],chapter_ids=plan['chapters'],kp_ids=plan['targets'],
        concept_names=[db.get(E.KnowledgePoint,k).name for k in plan['targets']],untested_kp_ids=plan['remaining'])
    cycle.initiator_user_id=actor_user_id;cycle.retest_kind=plan['kind'];cycle.rule_version=evidence.RULE_VERSION
    cycle.evidence_manifest=manifest;cycle.source_counts=counts;cycle.retest_attempt_id=attempt_id
    cycle.delayed_from={r['kp_id']:r.get('achieved_at') for r in evidence.summarize(plan['history'])
        if r['kp_id'] in plan['targets'] and r.get('achieved_at') and r['state'] in {'achieved','delayed_confirmed'}}
    db.commit()
    return {'status':'published','student_id':plan['student_id'],'cycle_id':cycle.cycle_id,'round':number,
        'source_attempt_id':plan['source_id'],'attempt_id':attempt_id,'release':release,
        'candidate_question_ids':[q.question_id for q in questions], 'source_counts':counts,
        'question_source':'mixed' if counts['ai_generated'] and counts['bank_selected'] else
                          'ai_generated' if counts['ai_generated'] else 'bank_selected',
        'retest_kind':cycle.retest_kind,'target_kp_ids':plan['targets'],'remaining_kp_ids':plan['remaining']}


def resume_result(row, sid):
    return {'status':'published','reused':True,'student_id':sid,'cycle_id':row['cycle_id'],
        'round':row['round'],'attempt_id':row.get('retest_attempt_id'),
        'release':{'release_id':row['release_id']} if row.get('release_id') else None,
        'retest_kind':row.get('retest_kind','formal'),'target_kp_ids':row.get('kp_ids',[])}


def queue(db, principal, request):
    from backend.app.workers.worker import create_job
    request=dict(request)
    if principal.role=='student':
        request.update(student_id=principal.student_id,scope_type='student_attempt')
    scopes=resolve_scopes(db,request)
    keys={s['key'] for s in scopes}
    db.commit();db.execute(text('BEGIN IMMEDIATE'))
    active=list(db.execute(select(E.Job).where(E.Job.job_type=='learning_retest',
        E.Job.status.in_(['queued','running']))).scalars())
    occupied={k:job for job in active for k in (job.payload or {}).get('_scope_keys',[])}
    remaining=keys-set(occupied)
    if not remaining:
        job=occupied[next(iter(keys))];db.commit()
        return {'job_id':job.job_id,'status':job.status,'reused':True}
    request['_scopes']=[s for s in scopes if s['key'] in remaining]
    request['_scope_keys']=sorted(remaining)
    return create_job(db,'learning_retest',request,user_id=principal.user_id)


def self_assignments(db, student_id):
    rows=db.execute(select(E.TargetedRetestCycle).where(E.TargetedRetestCycle.student_id==student_id,
        E.TargetedRetestCycle.release_id=='').order_by(E.TargetedRetestCycle.created_at.desc())).scalars().all()
    result=[]
    for row in rows:
        attempt=db.get(E.Attempt,row.retest_attempt_id) if row.retest_attempt_id else None
        if not attempt:continue
        detail=targeted_retest.cycle_detail(db,row,include_items=False)
        paper=db.get(E.Paper,attempt.paper_id)
        result.append({'recipient_id':'','release_id':row.cycle_id,'self_service':True,
            'attempt_id':attempt.attempt_id,'student_id':student_id,'title':paper.title if paper else '自主复测',
            'status':'submitted' if attempt.status=='submitted' else 'opened', 'release_status':'published',
            'release_type':'retest','item_count':row.question_count,'estimated_minutes':row.question_count*2,
            'published_at':row.published_at,'submitted_at':attempt.submitted_at,'score':attempt.score,
            'max_score':attempt.max_score,'source_attempt_id':row.source_attempt_id,'targeted_retest':detail})
    return result
