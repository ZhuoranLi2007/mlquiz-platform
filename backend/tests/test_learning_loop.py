from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.app.models import entities as E
from backend.app.services import learning_evidence as ev, learning_loop as loop, targeted_retest, attempt_service, model_provider
from backend.app.graphs import classroom_workflow
from backend.tests.test_course_acceptance import db, prepare


def history_row(number=1, *, kp='kp.a', correct=(True,True), start='2026-11-18T08:00:00+00:00', kind='formal'):
    items=[{'question_id':f'q{number}-{i}', 'main_kp_id':kp,'correct':c,
            'independent_evidence':True,'evidence_status':'scored' if c is not None else 'ungraded'} for i,c in enumerate(correct)]
    return {'cycle_id':f'c{number}','round':number,'status':'passed','kp_ids':[kp],
        'submitted_at':start,'started_at':start,'completed_at':start,'items':items,
        'rule_version':ev.RULE_VERSION,'retest_kind':kind,
        'evidence_manifest':{r['question_id']:{'new_to_student':True,'family_key':r['question_id'],
             'assessment_angle':f'angle-{i}'} for i,r in enumerate(items)}}


def test_pending_or_uncovered_round_preserves_result_but_new_wrong_evidence_downgrades():
    first=history_row()
    pending={'round':2,'kp_ids':['kp.a'],'items':[],'submitted_at':''}
    result=ev.summarize([first,pending],{'kp.a':True})[0]
    assert result['state']=='achieved' and result['achieved_at']==first['completed_at']
    assert result['task_state']=='pending'
    last=history_row(3,correct=(False,True))
    result=ev.summarize([first,pending,last],{'kp.a':True})[0]
    assert result['state']=='needs_review' and result['achieved_at']==first['completed_at']


@pytest.mark.parametrize('change', ['single','pending','repeat','hint','same_angle','same_family','legacy','practice','auxiliary'])
def test_insufficient_evidence_never_achieves(change):
    row=history_row()
    if change=='single':row['items']=row['items'][:1]
    elif change=='pending':row['items'][0].update(correct=None,evidence_status='ungraded')
    elif change=='repeat':row['evidence_manifest']['q1-0']['new_to_student']=False
    elif change=='hint':row['items'][0]['independent_evidence']=False
    elif change=='same_angle':row['evidence_manifest']['q1-1']['assessment_angle']='angle-0'
    elif change=='same_family':row['evidence_manifest']['q1-1']['family_key']='q1-0'
    elif change=='legacy':row['rule_version']='legacy'
    elif change=='practice':row['retest_kind']='practice'
    else:
        for item in row['items']:item.update(main_kp_id='kp.b',aux_kp_ids=['kp.a'])
    assert next(r for r in ev.summarize([row],{'kp.a':True}) if r['kp_id']=='kp.a')['state']=='needs_review'


def test_delayed_confirmation_requires_start_after_24_hours_and_survives_refresh():
    first=history_row()
    too_early=history_row(2,start='2026-11-19T07:59:59+00:00')
    assert ev.summarize([first,too_early])[0]['state']=='achieved'
    later=history_row(3,start='2026-11-19T08:00:00+00:00')
    decision=ev.summarize([first,too_early,later])[0]
    assert decision['state']=='delayed_confirmed'
    assert decision['achieved_at']==first['completed_at']
    assert ev.summarize([first,too_early,later])[0]==decision


def test_scheduler_rotates_uncovered_targets_before_repeating_failed_topics():
    topics=[f'kp.{i}' for i in range(7)]
    history=[];visited=set()
    for n in range(4):
        targets,remaining=ev.select_targets(topics,history,{k:True for k in topics})
        assert len(targets)<=2
        visited.update(targets)
        history.append({'round':n+1,'kp_ids':targets,'items':[],'submitted_at':'today','retest_kind':'formal'})
    assert visited==set(topics)


def seed_bank(db, count=5):
    stems=['通过绘制学习曲线识别训练样本不足与偏差问题，判断增加观测是否有助于泛化。',
           '给定正则化参数变化后的权重，解释参数范数为什么随惩罚强度增长而缩小。',
           '梯度下降优化一个凸二次函数时，如何根据曲率选择能够收敛的步长？',
           '考虑带有异常观测的数据集，比较平方损失和绝对损失对极端残差的敏感程度。',
           '最小二乘设计矩阵的列线性相关，说明参数解的唯一性与预测值的关系。']
    for i in range(count):
        db.add(E.Question(row_id=f'bank-row-{i}',question_id=f'bank-{i}',question_version=1,
            stem=stems[i],question_type='true_false' if i%2==0 else 'single_choice',
            options=[] if i%2==0 else [{'key':'A','text':'选项甲'},{'key':'B','text':'选项乙'}],
            answer_payload={'value':True} if i%2==0 else {'key':'A'},
            answer_status='verified',review_status='verified',answer_origin='teacher',origin='manual',
            main_kp_id='kp.linear',chapter_id='ch1',scope_class='in_scope',active=True,
            question_family_id=f'bank-family-{i}',cognitive_level='理解' if i%2==0 else '应用',score=2))
    db.commit()


def test_self_service_bank_fallback_is_owned_resumable_and_can_pass(db,monkeypatch):
    prepare(db,monkeypatch);seed_bank(db)
    monkeypatch.setattr(model_provider,'chat_status',lambda:{'configured':False})
    monkeypatch.setattr(model_provider,'chat',lambda *a,**k:pytest.fail('fallback must not call model'))
    source=db.execute(select(E.Attempt).where(E.Attempt.student_id=='stu-1',E.Attempt.assessment_version_id=='av-1')).scalars().first()
    uid=db.get(E.Student,'stu-1').linked_user_id
    kwargs=dict(actor_user_id=uid,student_id='stu-1',attempt_id=source.attempt_id,prefer_bank=True)
    out=classroom_workflow.run_teacher_intervention(db,**kwargs)['outputs']['retest']
    assert out['status']=='published' and out['retest_kind']=='formal',out
    assert out['source_counts']['bank_selected']==5
    assert not db.execute(select(E.AssessmentRelease)).scalars().all()
    again=classroom_workflow.run_teacher_intervention(db,**kwargs)['outputs']['retest']
    assert again['reused'] and again['attempt_id']==out['attempt_id']
    attempt=db.get(E.Attempt,out['attempt_id'])
    assert attempt.user_id==uid and attempt.student_id=='stu-1'
    for q in bank_service_questions(db,attempt.paper_id):
        attempt_service.save_answer(db,attempt.attempt_id,q['question_id'],q['answer_payload'])
    attempt_service.submit_attempt(db,attempt.attempt_id)
    history=targeted_retest.list_cycles(db,source_attempt_id=source.attempt_id,student_id='stu-1')
    summary=loop.public_summary(history,{'kp.linear':True})
    assert summary['round_result']['status']=='passed'
    assert summary['concept_results'][0]['state']=='achieved'
    assert loop.self_assignments(db,'stu-1')[0]['targeted_retest']['passed']
    # Exhausted unseen bank still offers practice, with no false mastery upgrade.
    next_round=classroom_workflow.run_teacher_intervention(db,**kwargs)['outputs']['retest']
    assert next_round['retest_kind']=='practice'
    assert loop.public_summary(targeted_retest.list_cycles(db,source_attempt_id=source.attempt_id,student_id='stu-1'))['last_passed']


def bank_service_questions(db,paper_id):
    return [i.snapshot for i in db.execute(select(E.PaperItem).where(E.PaperItem.paper_id==paper_id)).scalars()]


def test_partial_bank_is_practice_and_shared_queue_deduplicates_roles(db,monkeypatch):
    prepare(db,monkeypatch);seed_bank(db,2)
    source=db.execute(select(E.Attempt).where(E.Attempt.student_id=='stu-1',E.Attempt.assessment_version_id=='av-1')).scalars().first()
    uid=db.get(E.Student,'stu-1').linked_user_id
    student=SimpleNamespace(role='student',student_id='stu-1',user_id=uid)
    teacher=SimpleNamespace(role='teacher',student_id='',user_id='teacher_local')
    request={'attempt_id':source.attempt_id,'student_id':'stu-1','prefer_bank':True}
    a=loop.queue(db,student,request);b=loop.queue(db,teacher,request)
    assert a['job_id']==b['job_id'] and b['reused']
    result=classroom_workflow.run_teacher_intervention(db,actor_user_id=uid,**request)['outputs']['retest']
    assert result['retest_kind']=='practice' and len(result['candidate_question_ids'])==2


def test_source_must_belong_to_student(db,monkeypatch):
    prepare(db,monkeypatch)
    other=db.execute(select(E.Attempt).where(E.Attempt.student_id=='stu-2')).scalars().first()
    with pytest.raises(ValueError):loop.resolve_scopes(db,{'student_id':'stu-1','attempt_id':other.attempt_id})


def test_four_of_five_pass_is_not_all_concepts_achieved():
    a=history_row(kp='kp.a',correct=(True,True,True))
    b=history_row(kp='kp.b',correct=(True,False))
    for i in b['items']: i['question_id']='b-'+i['question_id']
    b['evidence_manifest']={'b-'+k:v for k,v in b['evidence_manifest'].items()}
    a.update(kp_ids=['kp.a','kp.b'],items=a['items']+b['items'],
             evidence_manifest={**a['evidence_manifest'],**b['evidence_manifest']},accuracy=.8)
    result=loop.public_summary([a])
    assert result['round_result']['status']=='passed'
    assert {r['kp_id']:r['state'] for r in result['concept_results']}=={'kp.a':'achieved','kp.b':'needs_review'}


@pytest.mark.parametrize('failure',['timeout','invalid_answer'])
def test_generation_failure_still_publishes_reliable_bank(db,monkeypatch,failure):
    from backend.app.graphs import workflows
    from backend.app.services import automatic_retest
    prepare(db,monkeypatch);seed_bank(db)
    if failure=='timeout':
        def unavailable(*args,**kwargs): raise TimeoutError('test timeout')
        monkeypatch.setattr(workflows,'run_generation',unavailable)
    else:
        def invalid(*args,**kwargs): raise ValueError('independent answer mismatch')
        monkeypatch.setattr(automatic_retest,'_validate_candidate',invalid)
    result=classroom_workflow.run_teacher_intervention(db,actor_user_id='online-1',
        student_id='stu-1',attempt_id='attempt-s1-v1')['outputs']['retest']
    assert result['retest_kind']=='formal' and result['source_counts']['bank_selected']==5
    assert result['source_counts']['ai_generated']==0


@pytest.mark.parametrize('count',[2,5])
def test_no_history_baseline_is_resumable_even_when_short_bank(db,monkeypatch,count):
    prepare(db,monkeypatch);seed_bank(db,count)
    db.add(E.User(user_id='new-online',role='student',display_name='NEW'))
    db.add(E.Student(student_id='new-student',external_key='NEW',display_code='NEW',linked_user_id='new-online'))
    db.commit()
    kw=dict(actor_user_id='new-online',student_id='new-student',kp_ids=['kp.linear'],prefer_bank=True)
    first=classroom_workflow.run_teacher_intervention(db,**kw)['outputs']['retest']
    assert first['retest_kind']==('baseline' if count==5 else 'practice')
    assert first['attempt_id']==first['source_attempt_id']
    again=classroom_workflow.run_teacher_intervention(db,**kw)['outputs']['retest']
    assert again['reused'] and again['attempt_id']==first['attempt_id']
    assert len(loop.history_for(db,'new-student',''))==1


def test_resource_checks_never_convert_http_success_into_content_proof(db,monkeypatch):
    from backend.app.services import learning_resources as lr
    db.add(E.Resource(resource_id='focused',title='线性模型图解',url='https://example.org/linear',
        kp_ids=['kp.linear'],verified=True,access_status='ok',content_status='full_text'))
    db.commit()
    monkeypatch.setattr(lr,'access_checks',lambda:{'https://example.org/linear':{
        'access_status':'reachable','content_readable':False,'checked_date':'2026-11-18'}})
    rows=lr.recommend(db,['kp.linear'])
    assert rows and rows[0]['content_readable'] is False
    monkeypatch.setattr(lr,'access_checks',lambda:{'https://example.org/linear':{'access_status':'blocked'}})
    assert all(r['url']!='https://example.org/linear' for r in lr.recommend(db,['kp.linear']))


def test_worker_restart_does_not_duplicate_published_cycle(db,monkeypatch):
    from backend.app.workers.worker import recover_stale
    prepare(db,monkeypatch);seed_bank(db)
    kwargs=dict(actor_user_id='online-1',student_id='stu-1',attempt_id='attempt-s1-v1',prefer_bank=True)
    published=classroom_workflow.run_teacher_intervention(db,**kwargs)['outputs']['retest']
    db.add(E.Job(job_id='interrupted-loop',job_type='learning_retest',status='running',
        claimed_at='2020-01-01T00:00:00+00:00',updated_at='2020-01-01T00:00:00+00:00',
        user_id='online-1',payload={'_scope_keys':['stu-1:attempt-s1-v1']}))
    db.commit()
    assert recover_stale(db)==1
    assert db.get(E.Job,'interrupted-loop').status=='failed'
    monkeypatch.setattr(model_provider,'chat',lambda *a,**kw:pytest.fail('resume must not generate'))
    resumed=classroom_workflow.run_teacher_intervention(db,**kwargs)['outputs']['retest']
    assert resumed['reused'] and resumed['attempt_id']==published['attempt_id']
    assert len(loop.history_for(db,'stu-1','attempt-s1-v1'))==1


def test_short_practice_can_retry_new_generation_without_hiding_saved_attempt(db,monkeypatch):
    prepare(db,monkeypatch);seed_bank(db,2)
    kwargs=dict(actor_user_id='online-1',student_id='stu-1',attempt_id='attempt-s1-v1',prefer_bank=True)
    first=classroom_workflow.run_teacher_intervention(db,**kwargs)['outputs']['retest']
    assert first['retest_kind']=='practice'
    state=loop.public_summary(loop.history_for(db,'stu-1','attempt-s1-v1'))
    assert 'retry_generation' in state['available_actions']
    retry=classroom_workflow.run_teacher_intervention(db,**kwargs,retry_generation=True)['outputs']['retest']
    assert retry['attempt_id']!=first['attempt_id']
    assert db.get(E.Attempt,first['attempt_id']).status=='in_progress'


def test_aggregate_and_single_scope_keep_same_original_evidence(db,monkeypatch):
    from backend.app.services import classroom_diagnosis
    prepare(db,monkeypatch);seed_bank(db,2)
    classroom_workflow.run_teacher_intervention(db,actor_user_id='online-1',student_id='stu-1',
        attempt_id='attempt-s1-v1',prefer_bank=True)
    single=classroom_diagnosis.student_attempt(db,'stu-1','av-1',attempt_id='attempt-s1-v1')['learning_loop']
    aggregate=loop.attach_scope(db,{},student_id='stu-1')['learning_loop_summary']['rows'][0]
    states=lambda row:{r['kp_id']:r['state'] for r in row['concept_results']}
    assert states(single)==states(aggregate)
    assert states(single)['kp.linear']=='needs_review'
