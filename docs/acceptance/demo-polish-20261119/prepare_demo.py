from pathlib import Path
from datetime import datetime
import json,sqlite3,hashlib,shutil
root=Path.cwd();stamp=datetime.now().strftime('%Y%m%d-%H%M%S');out=root/'docs/acceptance/demo-polish-20261119';source=root/'data/report-ui-20261119';target=root/'data/demo-polish-20261119'
if not target.exists():
 (target/'db').mkdir(parents=True)
 for p in source.iterdir():
  if p.is_dir() and p.name!='db':shutil.copytree(p,target/p.name)
 with sqlite3.connect('file:'+(source/'db/mlquiz.sqlite3').as_posix()+'?mode=ro',uri=True) as c,sqlite3.connect(target/'db/mlquiz.sqlite3') as dest:c.backup(dest)
 shutil.copy2(target/'db/mlquiz.sqlite3',target/'db'/('mlquiz.before-demo-'+stamp+'.sqlite3.bak'))
 proof={'date':stamp,'isolated_data':str(target.relative_to(root)),'source_database_sha256':hashlib.sha256((source/'db/mlquiz.sqlite3').read_bytes()).hexdigest(),'original_acceptance_database_sha256':hashlib.sha256((root/'data/final-acceptance/db/mlquiz.sqlite3').read_bytes()).hexdigest()}
 (out/'baseline.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
 print('isolated demo data ready')
else:print('reuse existing isolated demo data')
