"""Package versionable project files plus frontend/dist, excluding local secrets.

Run in a Git checkout. Existing output is atomically replaced after validation.
This does not alter live databases or regenerate evidence.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    assert not output.is_relative_to(ROOT), 'Write the delivery outside the checkout.'
    names = set(filter(None, subprocess.check_output(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
        cwd=ROOT).decode('utf-8').split('\0')))
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT/'frontend/dist').rglob('*') if p.is_file())
    assert 'frontend/dist/index.html' in names
    secrets = []
    if (ROOT/'.env').exists():
        for line in (ROOT/'.env').read_text(encoding='utf-8-sig').splitlines():
            if '=' not in line or line.lstrip().startswith('#'):
                continue
            key, value = line.split('=', 1)
            value = value.strip().strip('\"\'')
            if re.search(r'KEY|TOKEN|SECRET|PASSWORD', key, re.I) and len(value) >= 12:
                secrets.append(value.encode())
    patterns = [rb'sk-[A-Za-z0-9_-]{24,}', rb'gh[pousr]_[A-Za-z0-9]{30,}',
                rb'github_pat_[A-Za-z0-9_]{30,}', rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----']
    def scan(data, label):
        assert not any(secret in data for secret in secrets), 'Known secret in '+label
        assert not any(re.search(pattern, data) for pattern in patterns), 'Credential pattern in '+label
    manifest = {'format': 1, 'source_commit': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
                'includes_working_tree': True, 'files': {}, 'snapshots': {}}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.building.zip')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(names):
            path = ROOT/name
            if not path.is_file():
                continue
            parts = Path(name).parts
            assert not any(part in {'.git','.venv','node_modules','.artifact-build','.artifact-output','private','__pycache__'} for part in parts), name
            assert path.name != '.env' and not path.name.endswith(('.key','.pem','.sqlite3','.sqlite3-wal','.sqlite3-shm')), name
            assert not name.startswith('data/') or name.startswith(('data/demo/','data/acceptance/')), name
            data = path.read_bytes()
            scan(data, name)
            if name.endswith(('.docx','.pptx')):
                with zipfile.ZipFile(io.BytesIO(data)) as office:
                    assert office.testzip() is None
                    for member in office.namelist():
                        if member.endswith(('.xml','.rels')):
                            scan(office.read(member), name+'!'+member)
            if name.endswith('.sqlite3.gz'):
                raw = gzip.decompress(data)
                scan(raw, name+' (decompressed)')
                with tempfile.TemporaryDirectory(prefix='mlq-package-check-') as folder:
                    db_path=Path(folder)/'check.sqlite3'
                    db_path.write_bytes(raw)
                    db=sqlite3.connect(db_path)
                    result={'integrity':db.execute('pragma integrity_check').fetchone()[0],
                            'sessions':db.execute('select count(*) from session_tokens').fetchone()[0],
                            'password_hashes':db.execute("select count(*) from users where password_hash<>''").fetchone()[0]}
                    db.close()
                    assert result=={'integrity':'ok','sessions':0,'password_hashes':0}, name
                    manifest['snapshots'][name]=result
            manifest['files'][name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            archive.writestr('mlquiz-platform/'+name, data)
        archive.writestr('mlquiz-platform/DELIVERY_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8'))
    with zipfile.ZipFile(temporary) as archive:
        assert archive.testzip() is None
    os.replace(temporary, output)
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.sha256.txt').write_text(digest+'  '+output.name+'\n',encoding='utf-8')
    print(json.dumps({'archive':str(output),'bytes':output.stat().st_size,'sha256':digest,
                      'files':len(manifest['files']),'snapshots':manifest['snapshots']},ensure_ascii=False))


if __name__=='__main__':
    main()
