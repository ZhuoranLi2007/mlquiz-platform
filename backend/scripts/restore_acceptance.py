"""Restore the separately packaged acceptance evidence to a NEW data directory."""
import argparse
import gzip
import sqlite3
from pathlib import Path


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--snapshot', choices=['baseline','ai-review','diagnosis-review','workbench-review'], default='baseline',
                    help='baseline: first complete acceptance; ai-review: AI retest; diagnosis-review: diagnosis and resources; workbench-review: desktop workbench and updated feedback')
    args=ap.parse_args()
    root=Path(__file__).resolve().parents[2]
    filename = {'baseline':'mlquiz-verified.sqlite3.gz', 'ai-review':'mlquiz-ai-retest-reviewed.sqlite3.gz',
                'diagnosis-review':'mlquiz-diagnosis-resources-reviewed.sqlite3.gz',
                'workbench-review':'mlquiz-workbench-reviewed.sqlite3.gz'}[args.snapshot]
    source=root/'data/acceptance'/filename
    target=args.output.resolve()/'db/mlquiz.sqlite3'
    if target.exists():
        raise SystemExit('Refusing to overwrite an existing database. Choose a new --output directory.')
    target.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(source,'rb') as inp, target.open('xb') as out:
        import shutil
        shutil.copyfileobj(inp,out)
    with sqlite3.connect(target) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    print('Restored acceptance evidence:', target)
    print('Set MLQ_DATA_DIR to', target.parent.parent, 'then run bootstrap_demo.py to restore assets and local credentials.')


if __name__=='__main__': main()
