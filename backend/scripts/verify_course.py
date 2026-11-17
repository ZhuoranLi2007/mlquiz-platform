"""Run a repeatable API smoke test against a temporary copy of the demo.

Never opens the user's live database and never calls a paid model. Run from
the project root: python backend/scripts/verify_course.py --output result.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='mlquiz-acceptance-') as temporary:
        os.environ.update(MLQ_DATA_DIR=temporary, MLQ_MODEL_PROVIDER='',
                          MLQ_MODEL_API_KEY='', MLQ_QUICK_LOGIN_ENABLED='true')
        from backend.scripts.bootstrap_demo import bootstrap
        restored = bootstrap()
        from backend.app.main import app
        from backend.app.models.base import SessionLocal, engine
        from backend.app.models import entities as E
        from backend.app.workers import worker
        from fastapi.testclient import TestClient
        from sqlalchemy import select, text
        result = {'mode': 'isolated_demo_api_no_live_model', 'checks': []}

        def request(client, method, url, **kwargs):
            response = getattr(client, method)(url, **kwargs)
            assert response.status_code == 200, (url, response.status_code, response.text[:500])
            return response.json()

        with TestClient(app) as teacher, TestClient(app) as student:
            if (ROOT / 'frontend/dist/index.html').is_file():
                response = teacher.get('/')
                assert response.status_code == 200 and '<html' in response.text
                result['checks'].append('production_frontend_served')
            result['checks'].append('snapshot_restored_and_accounts_generated')
            login = request(teacher, 'post', '/api/auth/quick-login', json={'user_id': 'teacher_demo'})
            teacher.headers['Authorization'] = 'Bearer '+login['session_token']
            classroom = request(teacher, 'get', '/api/teacher/official-classroom')
            assert classroom['student_count'] == 16
            cid = classroom['classroom_id']
            with SessionLocal() as db:
                learner = db.execute(select(E.Student).where(E.Student.display_code == 'STU001')).scalar_one()
                sid, uid = learner.student_id, learner.linked_user_id
                old = db.execute(select(E.Attempt).where(E.Attempt.student_id == sid,
                    E.Attempt.status == 'submitted', E.Attempt.assessment_version_id != '')).scalars().first()
                av = old.assessment_version_id
                item = db.execute(select(E.PaperItem).where(E.PaperItem.paper_id == old.paper_id)).scalars().first()
                iid = item.item_id
                result['snapshot_counts'] = {table: db.execute(text('SELECT count(*) FROM '+table)).scalar()
                    for table in ['students', 'assessments', 'attempts', 'questions', 'resources', 'chunks']}
                result['resource_platforms'] = dict(db.execute(text('SELECT platform,count(*) FROM resources GROUP BY platform')).all())
            for endpoint, params in [
                ('question-analysis', {'assessment_version_id': av, 'paper_item_id': iid}),
                ('student-attempt', {'assessment_version_id': av, 'student_id': sid}),
                ('student-history', {'classroom_id': cid, 'student_id': sid}),
                ('class-assessment', {'classroom_id': cid, 'assessment_version_id': av}),
            ]:
                payload = request(teacher, 'get', '/api/teacher/'+endpoint, params=params)
                assert payload
                result['checks'].append(endpoint)
            bank = request(teacher, 'get', '/api/teacher/question-bank', params={'availability': 'usable'})
            candidates = [q for q in bank['items'] if q['question_type'] == 'true_false'
                          and isinstance(q.get('answer_payload', {}).get('value'), bool)]
            assert len(candidates) >= 2
            selected = candidates[:2]
            queued = request(teacher, 'post', '/api/teacher/paper-drafts/compose/jobs', json={
                'title': '隔离验收小测', 'mode': 'manual',
                'selected_items': [{'question_id': q['question_id'], 'score': 5} for q in selected]})
            with SessionLocal() as db:
                job = db.get(E.Job, queued['job_id'])
                worker.process_job(db, job)
            job = request(teacher, 'get', '/api/teacher/jobs/'+queued['job_id'])
            assert job['status'] == 'succeeded', job
            release = request(teacher, 'post', '/api/teacher/releases', json={
                'title': '隔离验收小测', 'paper_id': job['result']['paper_id'],
                'classroom_id': cid, 'student_ids': [sid]})
            rid = release['recipients'][0]['recipient_id']
            login = request(student, 'post', '/api/auth/quick-login', json={'user_id': uid})
            student.headers['Authorization'] = 'Bearer '+login['session_token']
            state = request(student, 'post', '/api/student/assignments/'+rid+'/start')
            aid = state['attempt_id']
            answers = {q['question_id']: q['answer_payload'] for q in selected}
            for question in state['questions']:
                assert not question.get('answer_payload'), 'answer leaked before submit'
                request(student, 'put', '/api/student/attempts/'+aid+'/answers/'+question['question_id'],
                        json={'response': answers[question['question_id']]})
            submission = request(student, 'post', '/api/student/assignments/'+rid+'/submit', json={})
            assert submission
            review = request(student, 'get', '/api/student/attempts/'+aid+'/review')
            with SessionLocal() as db:
                attempt = db.get(E.Attempt, aid)
                assert attempt.status == 'submitted' and attempt.score == attempt.max_score == 10
            assert student.get('/api/teacher/jobs/'+queued['job_id']).status_code == 403
            assert student.get('/api/teacher/prompt-history/export').status_code == 403
            request(teacher, 'get', '/api/teacher/prompt-history/export')
            result['checks'] += ['queued_composition', 'teacher_publication', 'student_answer_save',
                'grading_persisted_10_of_10', 'post_submit_review', 'role_access_control', 'prompt_export']
        engine.dispose()
    result.update(status='passed', live_model_verified=False, browser_clicks_verified=False)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output+'\n', encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
