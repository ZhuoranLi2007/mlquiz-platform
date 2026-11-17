"""Read back the focused AI-retest browser run; no model calls are made here."""
import argparse
import gzip
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import httpx
from backend.app import config as cfg
from backend.app.services.prompt_history import redact,export_history
from backend.app.models.base import SessionLocal


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8002')
    args=parser.parse_args()
    db=sqlite3.connect(f'file:{cfg.DB_PATH.as_posix()}?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    job=dict(db.execute("SELECT * FROM jobs WHERE job_id='job_2ceaa6274e18410e'").fetchone())
    workflow=json.loads(job['result']); published=workflow['outputs']['retest']
    assert published['source_counts']=={'ai_generated':5,'bank_selected':0,'original_reused':0}
    attempt=dict(db.execute("SELECT * FROM attempts WHERE attempt_id='attempt_da4f517af23c4d18'").fetchone())
    cycle=dict(db.execute('SELECT * FROM targeted_retest_cycles WHERE retest_attempt_id=?',(attempt['attempt_id'],)).fetchone())
    assert cycle['round_no']==3 and cycle['accuracy']==.8 and cycle['status']=='passed'
    assert json.loads(cycle['remaining_kp_ids'])==['kp.regularization.l1l2']
    assert attempt['retest_of_attempt_id']==cycle['source_attempt_id']=='attempt_9d95c27c402e4be5'
    scores=[dict(r) for r in db.execute('SELECT * FROM answer_scores WHERE attempt_id=?',(attempt['attempt_id'],))]
    assert len(scores)==5 and sum(r['awarded_score'] for r in scores)==8
    questions=[]
    for qid in published['candidate_question_ids']:
        q=dict(db.execute('SELECT * FROM questions WHERE question_id=?',(qid,)).fetchone())
        notes=json.loads(q['notes']);trace=notes['generation']
        assert trace['call_id'] and notes['call_id'] and trace['call_id']!=notes['call_id']
        for cid in [trace['call_id'],notes['call_id']]:
            assert db.execute('SELECT 1 FROM prompt_records WHERE call_id=?',(cid,)).fetchone()
        assert q['origin']=='ai' and q['answer_status']=='ai_auto_checked'
        questions.append(q)
    client=httpx.Client(base_url=args.url,timeout=30,trust_env=False)
    login=client.post('/api/auth/quick-login',json={'user_id':'teacher_demo'});login.raise_for_status()
    token=login.json().get('session_token')
    if token: client.headers['Authorization']='Bearer '+token
    response=client.get('/api/teacher/student-attempt',params={
        'student_id':attempt['student_id'],'assessment_version_id':attempt['assessment_version_id'],'attempt_id':attempt['attempt_id']})
    response.raise_for_status();api=response.json()
    assert api['score_summary']['awarded_score']==8 and api['score_summary']['max_score']==10
    runs=[dict(r) for r in db.execute("SELECT * FROM skill_runs WHERE skill_name='verify_learning' AND input_payload LIKE ?",('%'+attempt['attempt_id']+'%',))]
    assert any(r['status']=='succeeded' for r in runs)
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'baseline.sqlite3'
        path.write_bytes(gzip.decompress((ROOT/'data/acceptance/mlquiz-verified.sqlite3.gz').read_bytes()))
        base=sqlite3.connect(path)
        original_calls={r[0] for r in base.execute('SELECT call_id FROM prompt_records')}
        assert all(not base.execute('SELECT 1 FROM questions WHERE question_id=?',(q['question_id'],)).fetchone() for q in questions)
        preservation={}
        for table,key in [('attempts','attempt_id'),('answers','answer_id'),('answer_scores','score_id')]:
            columns=[r[1] for r in base.execute('PRAGMA table_info('+table+')')]
            rows=base.execute('SELECT '+','.join(columns)+' FROM '+table).fetchall()
            changed=sum(tuple(db.execute('SELECT '+','.join(columns)+' FROM '+table+' WHERE '+key+'=?',(r[columns.index(key)],)).fetchone() or ())!=r for r in rows)
            assert changed==0
            preservation[table]={'baseline_rows':len(rows),'changed_or_missing':changed}
        base.close()
    with SessionLocal() as session: prompts=export_history(session)
    new_calls=[r for r, in db.execute('SELECT call_id FROM prompt_records') if r not in original_calls]
    evidence={'kind':'real_browser_and_model_with_api_database_readback','job_id':job['job_id'],
        'workflow':workflow,'attempt':attempt,'cycle':cycle,'scores':scores,'api':api,'questions':questions,
        'verification_runs':runs,'preservation':preservation,'new_model_call_ids':new_calls,
        'new_model_calls':len(new_calls),'total_prompt_records':len(original_calls)+len(new_calls),
        'integrity_check':db.execute('PRAGMA integrity_check').fetchone()[0]}
    (ROOT/'docs/verification/ai-retest-persistence-and-api.json').write_text(json.dumps(redact(evidence),ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'docs/verification/ai-retest-runtime-prompts.json').write_text(json.dumps(prompts,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'passed','score':'8/10','remaining_kp_ids':json.loads(cycle['remaining_kp_ids']),
        'new_model_calls':len(new_calls),'preservation':preservation},ensure_ascii=False))


if __name__=='__main__':main()
