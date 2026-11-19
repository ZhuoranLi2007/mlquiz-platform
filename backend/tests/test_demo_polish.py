import json
from sqlalchemy import select
from backend.tests.test_classroom_diagnosis import db
from backend.tests.test_teacher_student_flow import flow, _login
from backend.tests.test_course_acceptance import prepare
from backend.app.models import entities as E
from backend.app.services import model_provider
from backend.app.services.skill_runtime import run_skill
from backend.app import config as cfg
from PIL import Image


def test_generated_teaching_explanation_and_sources_survive_review_storage(db,monkeypatch):
    prepare(db,monkeypatch)
    original=model_provider.chat
    def reply(*args,**kwargs):
        r=original(*args,**kwargs);v=json.loads(r['content'])
        for q in v['questions']:
            q.update(explanation='模型参数的线性组合给出预测值。',rubric={'points':[{'point':'依据参数线性关系判断','score':2}]})
        return {**r,'content':json.dumps(v)}
    monkeypatch.setattr(model_provider,'chat',reply)
    r=run_skill(db,'generate_questions',{'kp_ids':['kp.linear'],'question_types':['true_false'],'count':1})
    q=db.execute(select(E.Question).where(E.Question.question_id==r['result']['candidates'][0]['question_id'])).scalar_one()
    assert q.explanation=='模型参数的线性组合给出预测值。'
    assert q.rubric['points'][0]['score']==2
    assert q.source_refs[0]['document_id']=='evidence-doc' and q.source_refs[0]['page']==1
    assert not q.active and q.review_status=='pending'
    assert json.loads(q.notes)['generation']['verification_note']


def test_teacher_generation_job_reuses_request_and_keeps_owner(flow):
    client,local=flow;_login(client,'teacher','Teacher!2026')
    body={'kp_ids':['kp.loss'],'question_types':['single_choice'],'count':3}
    a=client.post('/api/teacher/ai-questions/jobs',json=body);b=client.post('/api/teacher/ai-questions/jobs',json=body)
    assert a.status_code==b.status_code==200
    assert a.json()['job_id']==b.json()['job_id'] and b.json()['reused']
    with local() as db:
        job=db.get(E.Job,a.json()['job_id'])
        assert job.user_id=='teacher' and job.job_type=='generate_questions'
        assert job.payload['blueprint']['kp_ids']==['kp.loss']


def test_student_cannot_queue_teacher_generation(flow):
    client,_=flow;_login(client,'student1','Student1!2026')
    assert client.post('/api/teacher/ai-questions/jobs',json={'kp_ids':['kp.loss']}).status_code==403


def test_resource_cover_uses_runtime_asset_directory(flow, monkeypatch, tmp_path):
    client,_=flow
    asset_dir=tmp_path/'assets'
    cover_dir=asset_dir/'covers'
    cover_dir.mkdir(parents=True)
    Image.new('RGB',(16,10),'#5690ce').save(cover_dir/'sample-video.jpg')
    monkeypatch.setattr(cfg,'ASSET_DIR',asset_dir)
    response=client.get('/api/resources/sample-video/cover')
    assert response.status_code==200
    assert response.headers['content-type']=='image/jpeg'
    assert response.content.startswith(b'\xff\xd8')
    assert client.get('/api/resources/no-cover/cover').status_code==404
