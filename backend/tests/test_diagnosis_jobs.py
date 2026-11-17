from sqlalchemy import select
from backend.tests.test_diagnose_classroom_skill import db
from backend.app.models import entities as E
from backend.app.workers import worker


def test_worker_persists_diagnosis_and_distinguishes_missing_ai(db):
    job=E.Job(job_id='diagnosis-job', job_type='teacher_diagnosis', user_id='teacher',
              attempts=1, payload={'scope_type':'student_attempt','student_id':'student-1',
                  'assessment_version_id':'version-fixed','include_ai':False})
    db.add(job);db.commit()
    worker.process_job(db,job)
    assert job.status=='succeeded', job.error
    diagnosis=db.get(E.Diagnosis,job.result['diagnosis_id'])
    assert diagnosis.run_id==job.result['run_id']
    assert diagnosis.payload['ai_analysis']['source']=='evidence_rules'
    assert db.get(E.SkillRun,diagnosis.run_id).job_id==job.job_id
    assert {r.skill_name for r in db.execute(select(E.SkillRun)).scalars()}=={'diagnose_classroom','recommend_resources'}


def test_missing_diagnosis_never_reports_success_or_automatically_repeats_paid_work(db,monkeypatch):
    from backend.app.services import skill_runtime
    monkeypatch.setattr(skill_runtime,'run_skill',lambda *a,**k:{'status':'failed','extra':{}})
    job=E.Job(job_id='missing-diagnosis',job_type='teacher_diagnosis',user_id='teacher',attempts=1,payload={})
    db.add(job);db.commit()
    worker.process_job(db,job)
    assert job.status=='failed'
    assert '诊断未保存' in job.error
    assert not job.result
