import json,sqlite3,httpx
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'docs/acceptance';r=json.loads((OUT/'run.json').read_text(encoding='utf-8'))
d=sqlite3.connect(ROOT/'data/final-acceptance/db/mlquiz.sqlite3');d.row_factory=sqlite3.Row
clients={}
for u in ['teacher_demo','stu001','stu002']:
 c=httpx.Client(base_url='http://127.0.0.1:8020',timeout=90);c.headers['Authorization']='Bearer '+c.post('/api/auth/quick-login',json={'user_id':u}).json()['session_token'];clients[u]=c
out={'attempts':[],'teacher_scopes':{},'database_integrity':d.execute('pragma integrity_check').fetchone()[0]}
for u in ['stu001','stu002']:
 aid=r['students'][u]['attempt_id'];a=dict(d.execute('select * from attempts where attempt_id=?',(aid,)).fetchone());f=clients[u].get('/api/student/feedback/'+aid);f.raise_for_status();data=f.json()
 assert a['status']=='submitted' and a['score']==75
 assert all(x.get('chapter_id') for x in data['items'])
 out['attempts'].append({'user':u,'attempt_id':aid,'db_score':a['score'],'api_score':data['score_summary'],'all_items_mapped':True})
 if u=='stu001':
  loop=data['learning_loop'];assert loop['round_result']['status']=='practice_completed';assert loop['round_result']['correct_count']==5
  for scope,params in [('student-attempt',{'student_id':a['student_id'],'assessment_version_id':a['assessment_version_id'],'attempt_id':aid}),('student-history',{'student_id':a['student_id']}),('class-assessment',{'assessment_version_id':a['assessment_version_id']})]:
   params['classroom_id']=d.execute('select a.classroom_id from assessments a join assessment_versions v on a.assessment_id=v.assessment_id where v.assessment_version_id=?',(a['assessment_version_id'],)).fetchone()[0]
   resp=clients['teacher_demo'].get('/api/teacher/'+scope,params=params);resp.raise_for_status();result=resp.json();match=result.get('learning_loop') or next(x for x in result['learning_loop_summary']['rows'] if x['source_attempt_id']==aid)
   assert match['round_result']==loop['round_result'];assert match['concept_results']==loop['concept_results'];out['teacher_scopes'][scope]={'consistent':True,'round_result':match['round_result']}
for key in ['retest_attempt_id','second_attempt_id']:
 a=dict(d.execute('select attempt_id,status,score,max_score from attempts where attempt_id=?',(r[key],)).fetchone());assert a['status']=='submitted';out[key]=a
out['isolation_status']=clients['stu002'].get('/api/student/feedback/'+r['students']['stu001']['attempt_id']).status_code
assert out['isolation_status']==403
imp=dict(d.execute("select * from import_runs where run_id='imp_a7a48b05805849df'").fetchone());out['import']={k:imp[k] for k in ['run_id','language','status','error']};out['import']['result']=json.loads(imp['result']);assert out['import']['result']['zh_questions']==8
out['chapter_audit_rows']=d.execute('select count(*) from chapter_mapping_audit').fetchone()[0]
(OUT/'readback.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print('Database, API, three diagnosis scopes, import and ownership verified')
