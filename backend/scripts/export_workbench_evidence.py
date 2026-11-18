"""Export real runtime prompts and a sanitized acceptance copy; never edit source."""
import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from backend.app import config as cfg
from backend.app.models.base import SessionLocal
from backend.app.services.prompt_history import export_history, redact


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--snapshot',type=Path,required=True)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    args.snapshot.parent.mkdir(parents=True,exist_ok=True)
    live=sqlite3.connect(cfg.DB_PATH.resolve().as_uri()+'?mode=ro',uri=True)
    baseline=sqlite3.connect(args.baseline.resolve().as_uri()+'?mode=ro',uri=True)
    preservation={}
    for table,key in [('questions','row_id'),('attempts','attempt_id'),('answers','answer_id'),('answer_scores','score_id')]:
        columns=[r[1] for r in baseline.execute('pragma table_info('+table+')')]
        names=','.join('"'+c+'"' for c in columns)
        rows=baseline.execute('select '+names+' from '+table).fetchall()
        changed=[r[columns.index(key)] for r in rows if tuple(live.execute('select '+names+' from '+table+' where '+key+'=?',(r[columns.index(key)],)).fetchone() or ()) != r]
        preservation[table]={'baseline_rows':len(rows),'changed_or_missing':len(changed),'changed_ids':changed}
        assert not changed,(table,changed)
    old_calls={r[0] for r in baseline.execute('select call_id from prompt_records')}
    baseline.close()
    with SessionLocal() as session:
        prompts=redact(export_history(session))
    (args.output/'runtime-prompts-final.json').write_text(json.dumps(prompts,ensure_ascii=False,indent=2),encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='mlq-workbench-snapshot-') as folder:
        dest=Path(folder)/'acceptance.sqlite3'
        clean=sqlite3.connect(dest)
        live.backup(clean)
        clean.execute('pragma secure_delete=ON')
        clean.execute('delete from session_tokens')
        clean.execute("update users set password_hash='',last_login_at=''")
        # Scan all strings using the same redactor used by runtime prompt export.
        # If operational errors ever included a key, sanitize only this copy.
        tables=[r[0] for r in clean.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
        for table in tables:
            for column in [r[1] for r in clean.execute('pragma table_info("'+table+'")')]:
                rows=clean.execute('select rowid,"'+column+'" from "'+table+'" where typeof("'+column+'")=\'text\'').fetchall()
                for rowid,value in rows:
                    safe=redact(value)
                    if safe!=value:
                        clean.execute('update "'+table+'" set "'+column+'"=? where rowid=?',(safe,rowid))
        clean.commit()
        clean.execute('pragma journal_mode=DELETE')
        clean.execute('vacuum')
        assert clean.execute('pragma integrity_check').fetchone()[0]=='ok'
        counts={t:clean.execute('select count(*) from '+t).fetchone()[0] for t in ['questions','attempts','answers','answer_scores','prompt_records','skill_runs','targeted_retest_cycles','session_tokens']}
        assert counts['session_tokens']==0
        assert clean.execute("select count(*) from users where password_hash<>'' or last_login_at<>''").fetchone()[0]==0
        clean.close()
        data=dest.read_bytes()
        for secret in [cfg.MODEL_API_KEY,cfg.EMBED_API_KEY]:
            if secret:
                assert secret.encode() not in data
        args.snapshot.write_bytes(gzip.compress(data,mtime=0))
    live.close()
    receipt={'created_at':datetime.now(timezone.utc).isoformat(),'original_data_preservation':preservation,
             'snapshot':args.snapshot.name,'counts':counts,'sha256':hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),
             'integrity_check':'ok','source_modified':False,'authentication':'sessions removed and password hashes blank in copy only',
             'real_prompt_count':prompts['count'],'new_call_ids_since_baseline':[r['call_id'] for r in prompts['items'] if r['call_id'] not in old_calls]}
    (args.output/'snapshot-and-preservation-final.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'counts':counts,'preservation':preservation,'new_model_calls':len(receipt['new_call_ids_since_baseline'])},ensure_ascii=False))


if __name__=='__main__':
    main()
