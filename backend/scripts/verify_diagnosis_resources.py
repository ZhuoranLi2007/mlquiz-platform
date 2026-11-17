"""Read actual diagnosis runs and compare API/DB; makes no model calls."""
import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import httpx
from backend.app import config as cfg
from backend.app.models.base import SessionLocal
from backend.app.services.learning_resources import material_catalog, recommend
from backend.app.services.prompt_history import export_history, redact


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--url', default='http://127.0.0.1:8003')
    args = ap.parse_args()
    db = sqlite3.connect(f'file:{cfg.DB_PATH.as_posix()}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    client = httpx.Client(base_url=args.url, timeout=30, trust_env=False)
    def login(user):
        r = client.post('/api/auth/quick-login', json={'user_id':user})
        r.raise_for_status()
        client.headers['Authorization'] = 'Bearer ' + r.json()['session_token']
    login('teacher_demo')
    scopes = {}
    for scope in ['student_attempt', 'class_assessment', 'student_history']:
        row = dict(db.execute("SELECT * FROM diagnoses WHERE scope_type=? AND skill_version='classroom-chapter-diagnosis-v6' ORDER BY created_at DESC LIMIT 1", (scope,)).fetchone())
        payload = json.loads(row['payload']); analysis = payload['ai_analysis']
        assert analysis['analysis_version'] == 3 and analysis['source'] == 'deepseek'
        assert analysis['validation']['accepted_count'] > 0
        call = dict(db.execute('SELECT * FROM model_calls WHERE call_id=?', (analysis['call_id'],)).fetchone())
        assert call['run_id'] == row['run_id']
        assert db.execute('SELECT response_text FROM prompt_records WHERE call_id=?', (call['call_id'],)).fetchone()[0]
        run = dict(db.execute('SELECT * FROM skill_runs WHERE run_id=?', (row['run_id'],)).fetchone())
        assert run['skill_name'] == 'diagnose_classroom' and run['status'] == 'succeeded'
        job = dict(db.execute('SELECT * FROM jobs WHERE job_id=?', (run['job_id'],)).fetchone())
        assert job['status'] == 'succeeded' and json.loads(job['result'])['diagnosis_id'] == row['diagnosis_id']
        rec = db.execute("SELECT run_id FROM skill_runs WHERE job_id=? AND skill_name='recommend_resources' AND status='succeeded'", (job['job_id'],)).fetchone()
        assert rec
        params = {k:row[k] for k in ['scope_type','student_id','classroom_id','assessment_version_id']}
        r = client.get('/api/teacher/diagnoses/latest', params=params);r.raise_for_status()
        api = r.json()
        assert api['diagnosis_id'] == row['diagnosis_id'] and api['result']['ai_analysis'] == analysis
        scopes[scope] = {'diagnosis_id':row['diagnosis_id'], 'job_id':job['job_id'], 'run_id':run['run_id'], 'call_id':call['call_id'], 'recommendation_run_id':rec[0], 'analysis':analysis, 'resources':payload['resources'], 'api_matches_database':True}
    login('stu001')
    student = client.get('/api/student/feedback/attempt_9d95c27c402e4be5');student.raise_for_status()
    student = student.json(); single = scopes['student_attempt']
    assert student['analysis']['summary'] == single['analysis']['summary']
    assert student['analysis']['learning_plan'] == single['analysis']['learning_plan']
    assert [r['analysis'] for r in student['analysis']['concepts']] == [r['analysis'] for r in single['analysis']['concept_hypotheses']]
    assert {r['url'] for r in student['resources']} == {r['url'] for r in single['resources']}
    catalog = material_catalog(); image_hashes = set(); image_requests = 0
    for kp_id, item in catalog.items():
        detail = client.get('/api/learning-materials/'+kp_id);detail.raise_for_status()
        assert detail.json()['name'] == item['name'] and detail.json()['pages']
        for page, expected in zip(detail.json()['pages'], item['pages'], strict=True):
            r = client.get(page['image_url']);r.raise_for_status()
            digest = hashlib.sha256(r.content).hexdigest()
            assert digest == expected['image_sha256']
            image_hashes.add(digest);image_requests += 1
        client.get('/learn/'+kp_id).raise_for_status()
    assert len(catalog) == 35 and len(image_hashes) == 80
    assert client.get('/api/learning-materials/unknown-kp').status_code == 404
    with SessionLocal() as session:
        resources = recommend(session, list(catalog), limit=12)
        assert set(catalog) <= {r['kp_id'] for r in resources}
        prompts = export_history(session)
    preservation = {}
    with tempfile.TemporaryDirectory() as tmp:
        basepath = Path(tmp)/'baseline.sqlite3'
        basepath.write_bytes(gzip.decompress((ROOT/'data/acceptance/mlquiz-ai-retest-reviewed.sqlite3.gz').read_bytes()))
        base = sqlite3.connect(basepath)
        original_calls = {r[0] for r in base.execute('SELECT call_id FROM prompt_records')}
        for table, key in [('questions','row_id'),('attempts','attempt_id'),('answers','answer_id'),('answer_scores','score_id')]:
            cols = [r[1] for r in base.execute('PRAGMA table_info('+table+')')]
            rows = base.execute('SELECT '+','.join(cols)+' FROM '+table).fetchall()
            changed = sum(tuple(db.execute('SELECT '+','.join(cols)+' FROM '+table+' WHERE '+key+'=?', (r[cols.index(key)],)).fetchone() or ()) != r for r in rows)
            if changed:
                base.close()
                raise AssertionError(f'{table}: {changed} original rows changed')
            preservation[table] = {'baseline_rows':len(rows), 'changed_or_missing':changed}
        base.close()
    calls = [r[0] for r in db.execute('SELECT call_id FROM prompt_records') if r[0] not in original_calls]
    assert len(calls) == 5
    failed = json.loads(db.execute("SELECT result FROM jobs WHERE job_id='job_677038ad97f54070'").fetchone()[0])['result']['ai_analysis']
    assert failed['source'] == 'evidence_rules' and failed.get('model_status') == 'invalid_output'
    result = {'checked_at':datetime.now(timezone.utc).isoformat(), 'status':'passed', 'method':'Read back browser-triggered real DeepSeek jobs; API/database assertions and HTTP resource checks. No new model calls in this script.', 'scopes':scopes, 'student_projection_matches':True, 'material_kps':len(catalog), 'unique_course_page_images':len(image_hashes), 'image_requests':image_requests, 'all_resource_hashes_match':True, 'preservation':preservation, 'new_model_call_ids':calls, 'new_model_calls':len(calls), 'total_prompt_records':len(calls)+len(original_calls), 'recorded_intermediate_failure':{'job_id':'job_677038ad97f54070','model_status':'invalid_output','reason':'Unescaped LaTeX backslash made model JSON invalid; retained original response and honest rule fallback; subsequent real calls passed after parser and prompt repair.'}, 'integrity_check':db.execute('PRAGMA integrity_check').fetchone()[0]}
    out = ROOT/'docs/verification'
    (out/'diagnosis-persistence-and-api.json').write_text(json.dumps(redact(result),ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'diagnosis-runtime-prompts.json').write_text(json.dumps(prompts,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['status','material_kps','unique_course_page_images','image_requests','preservation','new_model_calls','total_prompt_records']},ensure_ascii=False))


if __name__ == '__main__':
    main()
