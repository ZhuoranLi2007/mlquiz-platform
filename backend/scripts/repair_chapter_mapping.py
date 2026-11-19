"""Back up SQLite, conservatively repair chapter relations, retain audit evidence."""
import argparse,sqlite3,json,sys
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.app.services.chapter_mapping import infer
p=argparse.ArgumentParser();p.add_argument('--database',required=True,type=Path);p.add_argument('--report',required=True,type=Path);a=p.parse_args()
db=sqlite3.connect(a.database);db.row_factory=sqlite3.Row
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup=a.database.with_name(a.database.name+'.'+stamp+'.bak')
with sqlite3.connect(backup) as dest:db.backup(dest)
report={'backup':str(backup),'before':{},'after':{},'unresolved':[]}
for table in ['questions','paper_items','answer_scores']:
 report['before'][table]=dict(db.execute(f'SELECT count(*) total,sum(chapter_id IS NULL OR chapter_id="") missing FROM {table}').fetchone())
with db:
 db.execute('CREATE TABLE IF NOT EXISTS chapter_mapping_audit(entity_type TEXT,entity_id TEXT,chapter_id TEXT,basis TEXT,created_at TEXT,PRIMARY KEY(entity_type,entity_id))')
 # These labels already exist in the course knowledge-point catalogue.
 for cid,code,title,order in [('ch01','1','绪论',1),('ch_practice','practice','实验/框架',99)]:
  if db.execute('select 1 from knowledge_points where chapter=?',(title,)).fetchone():
   db.execute("insert or ignore into chapters(chapter_id,code,title,sort_order,source_ref,details,active,created_at) values(?,?,?,?,?,?,1,?)",(cid,code,title,order,'knowledge_points.chapter','{}',stamp))
 chapters=[SimpleNamespace(**dict(r)) for r in db.execute('select * from chapters')]
 kps={r['kp_id']:SimpleNamespace(**dict(r)) for r in db.execute('select * from knowledge_points')}
 def put(table,key,row,cid,basis):
  if cid:
   db.execute(f'update {table} set chapter_id=? where {key}=?',(cid,row[key]))
   db.execute('insert or replace into chapter_mapping_audit values(?,?,?,?,?)',(table,row[key],cid,basis,stamp))
  else:report['unresolved'].append({'table':table,'id':row[key],'basis':basis})
 for r in db.execute('select * from questions where chapter_id is null or chapter_id=""').fetchall():
  put('questions','row_id',r,*infer(chapters,kps,r['main_kp_id'],r['stem']))
 for r in db.execute('select * from paper_items where chapter_id is null or chapter_id=""').fetchall():
  snap=json.loads(r['snapshot']);cid,basis=infer(chapters,kps,snap.get('main_kp_id',''),snap.get('stem',''))
  if not cid:
   q=db.execute('select chapter_id from questions where question_id=? and question_version=?',(r['question_id'],r['question_version'])).fetchone()
   if q and q[0]:cid,basis=q[0],'对应题库版本章节'
  put('paper_items','item_id',r,cid,basis)
 for r in db.execute('select * from answer_scores where chapter_id is null or chapter_id=""').fetchall():
  item=db.execute('select chapter_id from paper_items where item_id=?',(r['paper_item_id'],)).fetchone()
  cid,basis=(item[0],'对应试卷题项') if item and item[0] else infer(chapters,kps,r['kp_id'],'')
  if not cid:
   q=db.execute('select chapter_id from questions where question_id=? and question_version=?',(r['question_id'],r['question_version'])).fetchone()
   if q and q[0]:cid,basis=q[0],'对应题库版本章节（历史评分缺题项关联）'
  put('answer_scores','score_id',r,cid,basis)
for table in report['before']:
 report['after'][table]=dict(db.execute(f'SELECT count(*) total,sum(chapter_id IS NULL OR chapter_id="") missing FROM {table}').fetchone())
assert db.execute('pragma integrity_check').fetchone()[0]=='ok'
a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:report[k] for k in ['before','after']},ensure_ascii=False))
