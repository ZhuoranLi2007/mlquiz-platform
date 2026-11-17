import json
import pytest
from sqlalchemy import select
from backend.tests.test_classroom_diagnosis import db
from backend.tests.test_supervision_round02 import candidate
from backend.tests.test_course_acceptance import prepare
from backend.app.models import entities as E
from backend.app.services import automatic_retest as retest, model_provider
from backend.app.services.skill_runtime import run_skill


def test_similar_candidate_pair_keeps_one_instead_of_rejecting_both(db,monkeypatch):
    first=candidate(db)
    second=E.Question(row_id='second',question_id='second',question_version=1,
        origin='ai',question_type='true_false',stem=first.stem+'请判断。',
        answer_payload={'value':True},main_kp_id='kp.loss',scope_class='in_scope',
        source_refs=first.source_refs,active=False,score=2)
    db.add(second);db.commit()
    monkeypatch.setattr(model_provider,'chat',lambda *a,**k:{'content':json.dumps({
        'answer_payload':{'value':True},'explanation':'平方误差定义。',
        'unambiguous':True,'kp_matches':True}),'call_id':'TEST_DOUBLE_NOT_LIVE'})
    out=retest.validate_and_publish(db,candidate_ids=[first.question_id,second.question_id],
        kp_ids=['kp.loss'],actor_user_id='teacher_local',student_id='stu-1',
        classroom_id='class-1',source_attempt_id='attempt-s1-v1',diagnosis_id='',thread_id='test',required_count=1)
    assert out['candidate_question_ids']==[first.question_id]
    assert out['source_counts']=={'ai_generated':1,'bank_selected':0,'original_reused':0}
    assert second.active is False


def test_duplicate_candidate_id_cannot_count_as_multiple_questions(db):
    q=candidate(db)
    with pytest.raises(ValueError,match='仅有1道'):
        retest.validate_and_publish(db,candidate_ids=[q.question_id]*5,kp_ids=['kp.loss'],
            actor_user_id='teacher_local',student_id='stu-1',classroom_id='class-1',
            source_attempt_id='attempt-s1-v1',diagnosis_id='',thread_id='test',required_count=5)
    assert not db.execute(select(E.AssessmentRelease)).scalars().all()


@pytest.mark.parametrize('bad_field,bad_value',[('kp_id','kp.unrelated'),('question_type','short_answer')])
def test_generation_enforces_requested_knowledge_and_type(db,monkeypatch,bad_field,bad_value):
    prepare(db,monkeypatch)
    provider=model_provider.chat
    def wrong_scope(*a,**kw):
        result=provider(*a,**kw)
        content=json.loads(result['content'])
        for q in content['questions']: q[bad_field]=bad_value
        return {**result,'content':json.dumps(content)}
    monkeypatch.setattr(model_provider,'chat',wrong_scope)
    before=db.query(E.Question).count()
    result=run_skill(db,'generate_questions',{'kp_ids':['kp.linear'],'question_types':['true_false'],'count':2})
    assert result['status']=='no_valid_candidates'
    assert not result['result']['candidates'] and db.query(E.Question).count()==before


def test_generation_records_call_and_run_on_full_candidate(db,monkeypatch):
    prepare(db,monkeypatch)
    result=run_skill(db,'generate_questions',{'kp_ids':['kp.linear'],'question_types':['true_false'],'count':1})
    qid=result['result']['candidates'][0]['question_id']
    q=db.execute(select(E.Question).where(E.Question.question_id==qid)).scalar_one()
    trace=json.loads(q.notes)['generation']
    assert trace['call_id']=='controlled-provider-fixture' and trace['run_id']==result['run_id']
    assert trace['target_kp_ids']==['kp.linear'] and trace['source']=='ai_generated'
