"""Read back the browser-created acceptance records and compare live API to SQLite.

This is an evidence checker, not a replacement for browser execution.
Requires the explicitly labelled 2026-11-16 acceptance data and a running API.
"""
import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import httpx
from backend.app import config as cfg
from backend.app.services.prompt_history import redact


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://127.0.0.1:8001')
    args = ap.parse_args()
    db = sqlite3.connect(f'file:{cfg.DB_PATH.as_posix()}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    rows = lambda sql, params=(): [dict(x) for x in db.execute(sql, params)]
    base = rows("SELECT * FROM papers WHERE title='验收20261116-基础双知识点-A'")[0]
    attempts = rows('SELECT attempt_id,student_id,assessment_version_id,retest_of_attempt_id,status FROM attempts WHERE paper_id=?', (base['paper_id'],))
    cycles = rows('SELECT * FROM targeted_retest_cycles WHERE source_attempt_id IN (?,?) ORDER BY student_id,round_no', tuple(a['attempt_id'] for a in attempts))
    assert len(cycles) == 4
    assert len({r['retest_attempt_id'] for r in cycles}) == 4
    client = httpx.Client(base_url=args.url, timeout=30, trust_env=False)
    login = client.post('/api/auth/quick-login', json={'user_id':'teacher_demo'})
    login.raise_for_status()
    token = login.json().get('session_token')
    if token: client.headers['Authorization'] = 'Bearer '+token
    api_results = {}
    all_attempts = attempts + [rows('SELECT attempt_id,student_id,assessment_version_id,retest_of_attempt_id,status FROM attempts WHERE attempt_id=?',(r['retest_attempt_id'],))[0] for r in cycles]
    for attempt in all_attempts:
        aid = attempt['attempt_id']
        scores = rows('SELECT question_id,correct,awarded_score,max_score,scoring_status,kp_id FROM answer_scores WHERE attempt_id=?',(aid,))
        attempt['scores'] = scores
        attempt['score_total'] = sum(r['awarded_score'] for r in scores)
        attempt['max_total'] = sum(r['max_score'] for r in scores)
        r = client.get('/api/teacher/student-attempt',params={'student_id':attempt['student_id'],'assessment_version_id':attempt['assessment_version_id'],'attempt_id':aid})
        r.raise_for_status(); data=r.json(); api_results[aid] = data
        summary = data['score_summary']
        assert summary['awarded_score'] == attempt['score_total'], (aid, summary)
        assert summary['max_score'] == attempt['max_total']
    for c in cycles:
        attempt = next(a for a in all_attempts if a['attempt_id']==c['retest_attempt_id'])
        source = next(a for a in attempts if a['attempt_id']==c['source_attempt_id'])
        assert attempt['student_id'] == source['student_id'] == c['student_id']
        assert attempt['retest_of_attempt_id'] == source['attempt_id']
        assert c['accuracy'] == sum(x['correct']==1 for x in attempt['scores'])/5
        assert c['status'] == ('passed' if c['accuracy'] >= .8 else 'not_passed')
    sid = rows("SELECT student_id FROM students WHERE display_code='STU002'")[0]['student_id']
    cls = rows('SELECT classroom_id FROM assessments WHERE assessment_id=(SELECT assessment_id FROM assessment_versions WHERE assessment_version_id=?)',(attempts[0]['assessment_version_id'],))[0]['classroom_id']
    endpoints = {
        'student_history':('/api/teacher/student-history',dict(student_id=sid,classroom_id=cls,date_from='2026-11-16',date_to='2026-11-16')),
        'class_assessment':('/api/teacher/class-assessment',dict(classroom_id=cls,assessment_version_id=attempts[0]['assessment_version_id'])),
        'question_analysis':('/api/teacher/question-analysis',dict(assessment_version_id=attempts[0]['assessment_version_id'],question_id='seed_human_tf_overfit')),
    }
    for key,(url,params) in endpoints.items():
        r=client.get(url,params=params);r.raise_for_status();api_results[key]=r.json()
    assert api_results['student_history']['statistics']['participated_assessments']==3
    assert api_results['student_history']['statistics']['average_score_rate']==.5
    skill_runs=rows("SELECT run_id,skill_name,thread_id,status,engine,input_payload,output_payload FROM skill_runs WHERE started_at>='2026-11-16'")
    for c in cycles:
        assert any(s['skill_name']=='verify_learning' and s['status']=='succeeded' and c['retest_attempt_id'] in s['input_payload'] for s in skill_runs)
    # Compare to the unchanged shipped snapshot, so this check is reproducible
    # from the delivery package without distributing a private live backup.
    original_dir = tempfile.TemporaryDirectory(prefix='mlquiz-original-check-')
    original_path = Path(original_dir.name)/'original.sqlite3'
    original_path.write_bytes(gzip.decompress((ROOT/'data/demo/mlquiz-course-demo.sqlite3.gz').read_bytes()))
    original = sqlite3.connect(original_path)
    preservation={}
    for table,key in [('attempts','attempt_id'),('answers','answer_id'),('answer_scores','score_id')]:
        cols=[x[1] for x in original.execute('PRAGMA table_info('+table+')')]
        old=original.execute('SELECT '+','.join(cols)+' FROM '+table).fetchall()
        changed=0
        for row in old:
            current=db.execute('SELECT '+','.join(cols)+' FROM '+table+' WHERE '+key+'=?',(row[cols.index(key)],)).fetchone()
            changed += tuple(current or ()) != tuple(row)
        preservation[table]={'original_rows':len(old),'changed_or_missing':changed}
        assert changed==0, preservation
    original.close()
    original_dir.cleanup()
    output = {'kind':'readback_of_real_browser_records','checked_at':__import__('datetime').datetime.now().astimezone().isoformat(),
        'database_integrity':db.execute('PRAGMA integrity_check').fetchone()[0],
        'attempts':all_attempts,'cycles':cycles,'api':api_results,'skill_runs':skill_runs,
        'original_records_preserved':preservation,
        'model_calls':rows('SELECT call_id,run_id,skill_name,model,status,duration_ms FROM model_calls'),
        'counts':{t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['questions','attempts','resources','prompt_records','skill_runs']}}
    target=ROOT/'docs/verification/current-persistence-and-api.json'
    target.write_text(json.dumps(redact(output),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'result':'passed','attempts':len(all_attempts),'cycles':len(cycles),'preservation':preservation,'counts':output['counts']},ensure_ascii=False))


if __name__=='__main__': main()
