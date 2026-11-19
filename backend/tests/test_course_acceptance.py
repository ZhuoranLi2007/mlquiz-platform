"""Course requirement regressions. Only model responses are test doubles.
The actual registry, LangGraph, retrieval, validation, publication, scoring,
SQLite persistence and feedback workflows run together.
"""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.tests.test_classroom_diagnosis import db
from backend.tests.test_teacher_student_flow import flow, _login
from backend.app.models import entities as E
from backend.app.graphs import classroom_workflow as graph
from backend.app.services import attempt_service, release_service, model_provider, prompt_history
from backend.app.services.skill_runtime import run_skill
from backend.app.workers import worker

STEMS = [
    '线性预测器的输出可以写成输入特征的加权求和加上偏置项。',
    '固定输入向量时，改变权重通常会改变回归模型给出的预测值。',
    '训练数据的标签用于衡量监督回归任务中预测结果与真实结果之间的差距。',
    '平方损失对较大的预测偏差施加比小偏差更大的惩罚。',
    '当所有样本特征完全相同时，带截距的回归器仍可以输出非零常数。',
    '对参数求梯度可以给迭代优化过程提供下降方向的信息。',
    '评估泛化能力时，测试集应与用于拟合参数的数据相互分离。',
    '加入正则项会改变原来的优化目标，并可能得到不同的最优系数。',
    '偏置参数允许拟合的直线不通过坐标原点。',
    '若设计矩阵的列线性相关，普通最小二乘的系数解可能不唯一。',
    '对特征进行缩放可能影响梯度下降收敛的速度。',
    '带有多项式输入特征的预测函数仍然可以对其待学习参数保持线性。',
]


def prepare(db, monkeypatch):
    db.get(E.User, 'teacher_local').role = 'teacher'
    for n in (1, 2):
        uid = f'online-{n}'
        db.add(E.User(user_id=uid, display_name=uid, role='student'))
        db.get(E.Student, f'stu-{n}').linked_user_id = uid
    db.add(E.Chapter(chapter_id='ch1', code='1', title='线性模型'))
    for item in db.execute(select(E.PaperItem)).scalars():
        item.chapter_id = 'ch1'
        item.snapshot = {**item.snapshot, 'chapter_id': 'ch1'}
    db.add(E.Document(document_id='evidence-doc', rel_path='lesson.txt', source_category='course_slides'))
    db.add(E.DocumentVersion(document_version_id='evidence-v1', document_id='evidence-doc', rel_path='lesson.txt', sha256='fixture'))
    db.add(E.Chunk(chunk_id='evidence-chunk', document_id='evidence-doc', document_version_id='evidence-v1',
                   content_raw='线性模型。'+'\n'.join(STEMS), kp_ids=['kp.linear'],
                   source_refs=[{'document_id': 'evidence-doc', 'page': 1}]))
    db.add(E.CourseScopeUnit(unit_id='scope-linear', kp_id='kp.linear', name='线性模型', depth='推导', coverage='covered'))
    db.commit()
    monkeypatch.setattr(model_provider, 'chat_status', lambda: {'configured': True})

    def chat(_db, *, messages, skill_name='', **kwargs):
        if skill_name == 'generate_questions':
            content = {'questions': [{'stem': stem, 'question_type': 'true_false',
                'answer_payload': {'value': True}, 'kp_id': 'kp.linear',
                'source_chunk_ids': ['evidence-chunk']} for stem in STEMS]}
        elif skill_name == 'automatic_retest_verification':
            content = {'answer_payload': {'value': True}, 'explanation': '测试夹具的独立解答一致。',
                       'unambiguous': True, 'kp_matches': True}
        else:
            content = {}  # unsupported AI prose falls back to deterministic evidence
        return {'content': json.dumps(content, ensure_ascii=False), 'usage': {},
                'usage_missing': True, 'call_id': 'controlled-provider-fixture',
                'model': 'TEST_DOUBLE_NOT_LIVE', 'finish_reason': 'stop'}
    monkeypatch.setattr(model_provider, 'chat', chat)


@pytest.mark.parametrize('scope', ['student_attempt', 'student_history', 'class_assessment'])
def test_three_scopes_generate_publish_answer_verify_and_update(db, monkeypatch, scope):
    prepare(db, monkeypatch)
    result = graph.run_teacher_intervention(db, actor_user_id='teacher_local',
        student_id='' if scope == 'class_assessment' else 'stu-1',
        classroom_id='class-1', assessment_version_id='' if scope == 'student_history' else 'av-1',
        scope_type=scope)
    assert result['status'] == 'succeeded', result
    published = result['outputs']['retest']
    assert published['status'] == 'published', result
    assert len(published['candidate_question_ids']) == 5
    assert result['outputs']['recommendations']['result']['recommendations']
    expected_recipients = 2 if scope == 'class_assessment' else 1
    releases = [r['release'] for r in published['items']]
    release = {'recipients':[p for r in releases for p in r['recipients']]}
    assert len(release['recipients']) == expected_recipients
    cycles = db.execute(select(E.TargetedRetestCycle).where(
        E.TargetedRetestCycle.release_id.in_([r['release_id'] for r in releases]))).scalars().all()
    assert len(cycles) == expected_recipients
    for recipient in release['recipients']:
        sid = recipient['student_id']
        user = db.get(E.Student, sid).linked_user_id
        started = release_service.start_assignment(db, recipient['recipient_id'], student_id=sid, user_id=user)
        aid = started['attempt_id']
        assert db.get(E.Attempt, aid).retest_of_attempt_id
        for q in started['questions']:
            assert not q.get('answer_payload'), 'exam API leaked answer'
            attempt_service.save_answer(db, aid, q['question_id'], {'value': True})
        submitted = release_service.submit_assignment(db, recipient['recipient_id'], student_id=sid)
        expected_pass = sid == 'stu-1'  # stu-2 also needs kp.loss; fixture only generates kp.linear.
        assert submitted['targeted_retest']['passed'] is expected_pass
        if not expected_pass:
            assert submitted['targeted_retest']['status'] == 'practice_completed'
        workflow = submitted['classroom_workflow']
        assert workflow['status'] == 'succeeded', workflow
        assert workflow['outputs']['verification']['result']['passed'] is expected_pass
        assert workflow['outputs']['retest_comparison']['comparable_knowledge_points']
        assert db.execute(select(E.Diagnosis).where(E.Diagnosis.student_id == sid)).scalars().first()
    assert all(c.retest_attempt_id for c in cycles)
    assert len({c.retest_attempt_id for c in cycles}) == expected_recipients
    assert db.execute(select(E.SkillRun).where(E.SkillRun.skill_name == 'verify_learning',
        E.SkillRun.status == 'succeeded')).scalars().all()


def test_missing_model_never_claims_generated_or_published(db, monkeypatch):
    prepare(db, monkeypatch)
    monkeypatch.setattr(model_provider, 'chat_status', lambda: {'configured': False})
    monkeypatch.setattr(model_provider, 'chat', lambda *a, **k: pytest.fail('must not call provider'))
    result = graph.run_teacher_intervention(db, actor_user_id='teacher_local',
        student_id='stu-1', assessment_version_id='av-1')
    assert result['status'] == 'partial'
    assert result['outputs']['retest']['status'] != 'published'
    assert not db.execute(select(E.AssessmentRelease)).scalars().all()


def test_normalize_class_diagnosis_shape():
    payload = {'high_frequency_errors': [{'knowledge_point': {'kp_id': 'kp.x'},
                'chapter': {'chapter_id': 'ch1'}}]}
    assert graph.diagnosis_items(payload)[0]['kp_id'] == 'kp.x'
    assert graph.diagnosis_items(payload)[0]['chapter_id'] == 'ch1'


def test_teacher_queue_is_reusable_runs_real_composition_and_is_private(flow):
    client, local = flow
    _login(client, 'teacher', 'Teacher!2026')
    payload = {'title': '后台组卷', 'mode': 'manual', 'instructions': '',
               'selected_items': [{'question_id': 'q-1', 'score': 5}], 'requirements': {}}
    first = client.post('/api/teacher/paper-drafts/compose/jobs', json=payload)
    assert first.status_code == 200, first.text
    jid = first.json()['job_id']
    again = client.post('/api/teacher/paper-drafts/compose/jobs', json=payload)
    assert again.json()['job_id'] == jid and again.json()['reused']
    with local() as db:
        job = worker.claim_job(db)
        assert job.job_id == jid
        worker.process_job(db, job)
    result = client.get('/api/teacher/jobs/'+jid).json()
    assert result['status'] == 'succeeded', result
    assert result['result']['item_count'] == 1
    assert client.get('/api/teacher/prompt-history/export').json()['count'] == 0
    client.post('/api/auth/logout')
    _login(client, 'student1', 'Student1!2026')
    assert client.get('/api/teacher/jobs/'+jid).status_code == 403
    assert client.get('/api/teacher/prompt-history/export').status_code == 403


def test_model_adapter_records_actual_prompt_and_redacts_secret(db, monkeypatch):
    import openai
    monkeypatch.setattr(model_provider.cfg, 'MODEL_PROVIDER', 'test')
    monkeypatch.setattr(model_provider.cfg, 'MODEL_CHAT_MODEL', 'test-double')
    monkeypatch.setattr(model_provider.cfg, 'MODEL_API_KEY', 'test-secret-never-export')
    response = SimpleNamespace(model='test-double', usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer": 42}'), finish_reason='stop')])
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **request: response))))
    model_provider.chat(db, messages=[{'role': 'user', 'content': 'test-secret-never-export explain gradient'}], skill_name='test')
    exported = prompt_history.export_history(db)
    assert exported['count'] == 1
    assert 'explain gradient' in json.dumps(exported)
    assert 'test-secret-never-export' not in json.dumps(exported)
    assert exported['items'][0]['response'] == '{"answer": 42}'


def test_round_caps_targets_without_hiding_remaining_concepts(db, monkeypatch):
    from backend.app.services.learning_evidence import select_targets
    targets = [f'kp.{i}' for i in range(7)]
    selected, remaining = select_targets(targets, [], {k:True for k in targets})
    assert selected == targets[:2]
    assert remaining == targets[2:]


@pytest.mark.parametrize('content', [
    {'questions': None},
    {'questions': [{'stem': 42, 'options': 'bad', 'answer_payload': True}]},
    {'questions': [{'stem': '错误选项类型测试题', 'question_type': 'single_choice',
                    'options': ['A', 'B'], 'answer_payload': {'key': 'A'}}]},
])
def test_bad_model_structure_never_crashes_or_enters_bank(db, monkeypatch, content):
    prepare(db, monkeypatch)
    before = len(db.execute(select(E.Question)).scalars().all())
    monkeypatch.setattr(model_provider, 'chat', lambda *a, **k: {
        'content': json.dumps(content), 'usage': {}, 'usage_missing': True})
    result = run_skill(db, 'generate_questions', {'blueprint': {'kp_ids': ['kp.linear'], 'count': 3}})
    assert result['status'] != 'failed', result
    assert not result['result']['candidates']
    assert len(db.execute(select(E.Question)).scalars().all()) == before


def test_old_cycle_indexes_migrate_without_losing_rows(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text
    from backend.app.models import base
    engine = create_engine('sqlite:///'+str(tmp_path/'old.db'))
    base.Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text('ALTER TABLE targeted_retest_cycles DROP COLUMN untested_kp_ids'))
        for col in ('release_id', 'retest_attempt_id'):
            c.execute(text(f'DROP INDEX ix_targeted_retest_cycles_{col}'))
            c.execute(text(f'CREATE UNIQUE INDEX ix_targeted_retest_cycles_{col} ON targeted_retest_cycles ({col})'))
        c.execute(text("INSERT INTO targeted_retest_cycles (cycle_id,student_id,source_attempt_id,source_assessment_version_id,diagnosis_id,release_id,retest_attempt_id,workflow_run_id,round_no,status,threshold,question_count,chapter_ids,kp_ids,concept_names,generated_question_ids,remaining_kp_ids,published_at,submitted_at,completed_at,created_at,updated_at) VALUES ('keep','s','a','','','r','','',1,'published',0.8,5,'[]','[]','[]','[]','[]','','','','','')"))
    monkeypatch.setattr(base, 'engine', engine)
    base.init_db()
    base.init_db()
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM targeted_retest_cycles WHERE cycle_id='keep'")).scalar_one() == 1
        indexes = {r[1]: r[2] for r in c.execute(text('PRAGMA index_list(targeted_retest_cycles)'))}
        assert indexes['ix_targeted_retest_cycles_release_id'] == 0
        assert indexes['uq_retest_release_student'] == 1
        assert c.execute(text("SELECT untested_kp_ids FROM targeted_retest_cycles WHERE cycle_id='keep'")).scalar_one() == '[]'
    engine.dispose()


def test_worker_missing_model_is_failed_without_retry(db, monkeypatch):
    monkeypatch.setattr(graph.workflows, 'run_generation', lambda *a, **k: {'status': 'model_not_configured'})
    job = E.Job(job_id='no-model', job_type='generate_questions', payload={}, attempts=1)
    db.add(job); db.commit()
    worker.process_job(db, job)
    assert job.status == 'failed'
    assert job.stage == 'pending_model_configuration'


def test_worker_stale_publication_never_blindly_replays(db):
    old = '2000-01-01T00:00:00+00:00'
    jobs = [E.Job(job_id='publish-crash', job_type='teacher_intervention', status='running',
                  claimed_at=old, updated_at=old, attempts=1),
            E.Job(job_id='alive', job_type='diagnosis', status='running', claimed_at=old,
                  updated_at=E.utcnow(), attempts=1),
            E.Job(job_id='stale-read', job_type='diagnosis', status='running', claimed_at=old,
                  updated_at=old, attempts=1)]
    db.add_all(jobs); db.commit()
    assert worker.recover_stale(db) == 2
    assert jobs[0].status == 'failed'
    assert jobs[1].status == 'running'
    assert jobs[2].status == 'queued'


def test_model_errors_do_not_persist_secrets(db, monkeypatch):
    import openai
    secret = 'test-secret-that-must-not-be-in-errors'
    monkeypatch.setattr(model_provider.cfg, 'MODEL_API_KEY', secret)
    monkeypatch.setattr(model_provider.cfg, 'MODEL_CHAT_MODEL', 'test-double')
    def fail(**kwargs):
        raise RuntimeError('provider echoed '+secret)
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fail))))
    with pytest.raises(model_provider.ModelUnavailable) as error:
        model_provider.chat(db, messages=[{'role': 'user', 'content': 'test'}])
    assert secret not in str(error.value)
    assert all(secret not in x.error for x in db.execute(select(E.ModelCall)).scalars())


@pytest.mark.parametrize('kind', ['teacher_compose', 'teacher_diagnosis'])
def test_concurrent_teacher_queue_creates_only_one_job(tmp_path, kind):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from backend.app.models.base import Base
    from backend.app.api.role_routes import _queue_teacher_job
    engine = create_engine('sqlite:///'+str(tmp_path/'concurrent.db'),
                           connect_args={'timeout': 10})
    Base.metadata.create_all(engine)
    barrier = Barrier(2)
    def enqueue():
        with Session(engine, expire_on_commit=False) as session:
            barrier.wait()
            return _queue_teacher_job(session, SimpleNamespace(user_id='teacher'),
                                      kind, {'title': 'same request'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: enqueue(), range(2)))
    assert first['job_id'] == second['job_id']
    with Session(engine) as session:
        assert len(session.execute(select(E.Job)).scalars().all()) == 1
    engine.dispose()
