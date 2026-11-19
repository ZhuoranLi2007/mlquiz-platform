"""Read back the v3 isolated browser acceptance; writes redacted evidence only.

Run with MLQ_DATA_DIR pointing to the restored learning-loop-review snapshot.
The two submissions must already have been made in the browser. This script
does not fabricate scores, issue retests, or call a model.
"""
import argparse
import json
import sys
from pathlib import Path
import httpx
from sqlalchemy import select

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from backend.app.models.base import SessionLocal
from backend.app.models import entities as E
from backend.app.services import prompt_history, learning_resources


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8018')
    parser.add_argument('--output',type=Path,default=ROOT/'docs/evidence/learning-loop-v3')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    clients={};report={'checked_at':E.utcnow(),'students':[],'isolation':{},'reproducible':True}
    try:
        for name in ['teacher_demo','stu001','stu002']:
            client=httpx.Client(base_url=args.url,timeout=45)
            response=client.post('/api/auth/quick-login',json={'user_id':name});response.raise_for_status()
            client.headers['Authorization']='Bearer '+response.json()['session_token']
            clients[name]=client
        teacher=clients['teacher_demo']
        with SessionLocal() as db:
            for user,root in [('stu001','attempt_d660ce810c684cfd'),('stu002','attempt_7bc74a1a495e4925')]:
                source=db.get(E.Attempt,root)
                version=db.get(E.AssessmentVersion,source.assessment_version_id)
                classroom=db.get(E.Assessment,version.assessment_id).classroom_id
                feedback=clients[user].get('/api/student/feedback/'+root);feedback.raise_for_status()
                loop=feedback.json()['learning_loop']
                assert loop['round_result']['status']=='passed'
                assert loop['round_result']['correct_count']==(5 if user=='stu001' else 4)
                expected={'kp.regularization.l1l2':'achieved' if user=='stu001' else 'needs_review',
                          'kp.eval.bias_var':'achieved'}
                def decisions(row): return {k['kp_id']:k['state'] for k in row['concept_results']}
                assert decisions(loop)==expected
                checks={}
                for scope,params in [
                    ('student-attempt',{'student_id':source.student_id,'assessment_version_id':version.assessment_version_id,'attempt_id':root}),
                    ('student-history',{'student_id':source.student_id,'classroom_id':classroom}),
                    ('class-assessment',{'classroom_id':classroom,'assessment_version_id':version.assessment_version_id})]:
                    r=teacher.get('/api/teacher/'+scope,params=params);r.raise_for_status();data=r.json()
                    matching=data.get('learning_loop') or next(x for x in data['learning_loop_summary']['rows'] if x['source_attempt_id']==root)
                    assert decisions(matching)==expected
                    assert matching['round_result']['cycle_id']==loop['round_result']['cycle_id']
                    checks[scope]={'consistent':True,'task_state':matching['task_state'],'round_result':matching['round_result']}
                report['students'].append({'user':user,'source_attempt_id':root,'decision':expected,
                    'loop':loop,'teacher_scopes':checks})
            denied=clients['stu002'].get('/api/student/attempts/attempt_c0da6e6920724ab6/review').status_code
            denied_job=clients['stu002'].get('/api/student/retests/jobs/job_910f481e50a54d8c').status_code
            assert denied==403 and denied_job==404
            report['isolation']={'other_student_attempt':denied,'other_student_job':denied_job}
            pages=[p for row in learning_resources.material_catalog().values() for p in row['pages']]
            assert all(learning_resources.page_image(p) for p in pages)
            report['course_pages']={'knowledge_points':len(learning_resources.material_catalog()),'page_references':len(pages),'all_available':True}
            prompts=prompt_history.export_history(db)
            first=db.get(E.Job,'job_910f481e50a54d8c').created_at
            prompts['items']=[p for p in prompts['items'] if p['created_at']>=first]
            prompts['count']=len(prompts['items'])
            assert any(p['skill']=='generate_questions' and p['status']=='ok' for p in prompts['items'])
            assert any(p['skill']=='automatic_retest_verification' and p['status']=='ok' for p in prompts['items'])
            report['real_model_calls']=prompts['count']
            (args.output/'runtime-prompts.json').write_text(json.dumps(prompt_history.redact(prompts),ensure_ascii=False,indent=2),encoding='utf-8')
        (args.output/'api-readback.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'students':2,'teacher_scopes_per_student':3,'isolation':report['isolation'],
            'course_pages':report['course_pages'],'real_model_calls':report['real_model_calls']},ensure_ascii=False))
    finally:
        for client in clients.values():client.close()


if __name__=='__main__':main()
