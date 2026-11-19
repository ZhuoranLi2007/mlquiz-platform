"""Read-only check of corrected questions through the existing student DTO."""
from pathlib import Path
import sys,json,sqlite3
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from backend.app.services.question_dto import student_view
from revise_display_questions import current,DB
ids=['slq_62cc358de7_p12_3','slq_62cc358de7_p12_4','seed_note_pending_010','seed_note_pending_023']
checks=[]
for qid in ids:
 q=current(qid);v=student_view(q,reveal=False,mode='quiz')
 assert not any(k in v for k in ['answer_payload','rubric','explanation','notes'])
 assert len(v['options'])==4 and not v['options_incomplete']
 if qid.startswith('seed_'):assert not v['stem_images']
 assert all(not any(k in ref for k in ['assets','url','href']) for ref in v.get('source_refs',[]))
 checks.append({'question_id':qid,'answer_fields_hidden':True,'four_options_complete':True,'body_images':len(v.get('stem_images',[]))})
with sqlite3.connect('file:'+DB.as_posix()+'?mode=ro',uri=True) as c:
 c.row_factory=sqlite3.Row
 rows=[dict(r) for r in c.execute('select * from questions where question_id=? order by question_version',('slq_62cc358de7_p12_4',))]
 stable=['stem','options','answer_payload','explanation','rubric','score','question_type']
 assert all(rows[0][k]==rows[1][k] for k in stable)
p=ROOT/'docs/report/visible_content_verification.json';r=json.loads(p.read_text(encoding='utf-8'));r['revised_student_dto_readonly']=checks;r['checks']['adjacent_question_content_preserved']=True;r['checks']['corrected_question_student_payload_safe']=True;p.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'questions_checked':len(checks),'safe_student_dto':True,'adjacent_question_content_preserved':True}))
