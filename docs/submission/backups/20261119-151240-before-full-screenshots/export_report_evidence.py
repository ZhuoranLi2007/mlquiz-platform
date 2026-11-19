"""Read-only, whitelisted evidence export for the rewritten report."""
from pathlib import Path
import sqlite3,json,hashlib
ROOT=Path(__file__).resolve().parents[2]
DB=ROOT/'data/final-acceptance/db/mlquiz.sqlite3'
def digest():return hashlib.sha256(DB.read_bytes()).hexdigest()
before=digest()
c=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
ids=['attempt_fe1348e54d8543a6','attempt_1da4e3b4d5754de2','attempt_fd2c632d136c40cc','attempt_19de8c85e41f4967']
attempts=[]
for aid in ids:
 a=dict(c.execute('select attempt_id,paper_id,student_id,assessment_version_id,status,grading_status,score,max_score,submitted_at,retest_of_attempt_id from attempts where attempt_id=?',(aid,)).fetchone())
 a['items']=[dict(r) for r in c.execute('select question_id,chapter_id,kp_id,correct,awarded_score,max_score,scoring_status,authority_status from answer_scores where attempt_id=? order by question_id',(aid,))]
 attempts.append(a)
jobs=[]
for jid in ['job_9d172ff8feaa457c','job_573fd31e7df346ea','job_269b87ec73164cc1','job_544a2d568f5f4c67']:
 r=dict(c.execute('select job_id,job_type,status,stage,result from jobs where job_id=?',(jid,)).fetchone())
 out=json.loads(r.pop('result'));r['result_keys']=list(out);r['stages']=out.get('stages',[])
 if not r['stages']:
  for v in out.values():
   if isinstance(v,dict) and v.get('stages'):r['stages']=v['stages'];break
 jobs.append(r)
run=dict(c.execute("select run_id,skill_name,thread_id,status,engine,model,started_at,output_payload from skill_runs where run_id='run_737643d9fe5143e7'").fetchone())
out=json.loads(run.pop('output_payload'))['result'];run['pipeline']=out['pipeline'];run['rejected']=out['rejected']
counts=[dict(r) for r in c.execute("select skill_name,status,engine,count(*) count from skill_runs where started_at like '2026-11-19%' group by skill_name,status,engine order by skill_name")]
cycle=dict(c.execute("select * from targeted_retest_cycles where cycle_id='retestcycle_217f711c16574a0c'").fetchone())
allowed=['cycle_id','source_attempt_id','retest_attempt_id','kp_ids','source_counts','retest_kind','rule_version','workflow_run_id','round_number']
cycle={k:v for k,v in cycle.items() if k in allowed}
c.close();after=digest();assert before==after,'Database changed during readback'
evidence={'date':'2026-11-19','method':'sqlite URI mode=ro; selected non-secret fields; no model calls or data mutations',
 'source_db':str(DB.relative_to(ROOT)),'db_sha256':before,'db_unchanged':True,'attempts':attempts,'jobs':jobs,'generation_run':run,'daily_skill_runs':counts,'cycle':cycle}
(Path(__file__).parent/'report_evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'attempts':len(attempts),'jobs':len(jobs),'db_unchanged':True,'output':'report_evidence.json'}))
