"""Extract a delivery into a new folder and run actual API, worker and browser checks.

Uses the calling Python environment (dependencies and Playwright must be installed).
Never imports a model key or touches the original runtime. Output contains no tokens.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',required=True,type=Path)
    p.add_argument('--extract',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--chromium',required=True)
    args=p.parse_args()
    folder=args.extract.resolve()
    assert not folder.exists(), 'Use a new extraction directory.'
    folder.mkdir(parents=True)
    with zipfile.ZipFile(args.archive) as archive:
        assert archive.testzip() is None
        for member in archive.namelist():
            assert (folder/member).resolve().is_relative_to(folder), member
        archive.extractall(folder)
    root=folder/'mlquiz-platform'
    manifest=json.loads((root/'DELIVERY_MANIFEST.json').read_text(encoding='utf-8'))
    for name,meta in manifest['files'].items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==meta['sha256'], name
    assert not (root/'.env').exists()
    env={k:v for k,v in os.environ.items() if not k.startswith('MLQ_')}
    env['PYTHONUTF8']='1'
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    logs=folder/'smoke-logs'
    logs.mkdir()
    receipt={'status':'running','method':'Fresh ZIP extraction; reused installed Python 3.13 lockfile environment; actual API, worker and Chromium; no model configuration copied.',
             'tested_archive_sha256':hashlib.sha256(args.archive.read_bytes()).hexdigest(),
             'runtime_sha256':{k:v['sha256'] for k,v in manifest['files'].items() if k.startswith(('backend/','frontend/')) or k in ['start.ps1','requirements.txt']},
             'checks':{}}
    def save():
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    def run(command):
        result=subprocess.run([sys.executable,'-X','utf8',*command],cwd=root,env=env,capture_output=True,text=True,encoding='utf-8',creationflags=flags)
        if result.returncode:
            raise RuntimeError('Subprocess failed: '+str(command[0])+'\n'+result.stderr[-1500:])
        return result.stdout
    def bootstrap_result():
        # Migration notices precede the final JSON object on a new database.
        output=run(['backend/scripts/bootstrap_demo.py'])
        start=output.rfind('\n{')
        return json.loads(output[start+1:] if start>=0 else output)
    def request(url, data=None):
        req=urllib.request.Request(url,data=json.dumps(data).encode() if data else None,headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=15) as response:
            return response.status,response.read()
    def serve(label):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        handles=[(logs/(label+'-'+name+'.log')).open('w',encoding='utf-8') for name in ['api','worker']]
        api=subprocess.Popen([sys.executable,'-X','utf8','-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port',str(port)],cwd=root,env=env,stdout=handles[0],stderr=subprocess.STDOUT,creationflags=flags)
        worker=subprocess.Popen([sys.executable,'-X','utf8','-m','backend.app.workers.worker','--interval','1'],cwd=root,env=env,stdout=handles[1],stderr=subprocess.STDOUT,creationflags=flags)
        try:
            for _ in range(100):
                assert api.poll() is None, 'API exited during startup'
                try:
                    status,body=request(base+'/api/health')
                    if status==200:break
                except Exception:time.sleep(.2)
            else:raise AssertionError('API startup timeout')
            status,html=request(base+'/')
            assert status==200 and b'<div id="app">' in html
            assets=re.findall(r'(?:src|href)="(/assets/[^\"]+)"',html.decode())
            assert assets
            for asset in assets:assert request(base+asset)[0]==200
            config=json.loads(request(base+'/api/config/status')[1])
            assert config['model']['configured'] is False
            login=json.loads(request(base+'/api/auth/quick-login',{'user_id':'teacher_demo'})[1])
            assert login['status']=='ok'
            assert worker.poll() is None
            receipt['checks'][label].update(health=200,frontend=200,built_assets_checked=len(assets),model_configured=False,teacher_login='ok',worker_alive=True)
            if label=='workbench_snapshot':
                browser_out=logs/'browser'
                browser_out.mkdir()
                common=['--base-url',base,'--database',str(root/'data/workbench-view/db/mlquiz.sqlite3'),'--cycle-record',str(root/'docs/verification/workbench/cycle-record.json'),'--output',str(browser_out),'--chromium',args.chromium]
                for script in ['verify_workbench_browser.py','verify_teacher_workbench_browser.py']:
                    print('Running extracted '+script,flush=True)
                    run(['backend/scripts/'+script,*common])
                student=json.loads((browser_out/'browser-persistence-final.json').read_text(encoding='utf-8'))
                teacher=json.loads((browser_out/'teacher-workbench-final.json').read_text(encoding='utf-8'))
                assert not student['failures'] and not teacher['failures']
                assert teacher['text_preview']['actual_text_characters']>0
                receipt['checks'][label]['browser']={'students':list(student['students']),'student_layout_checks':len(student['layout']),
                    'teacher_scopes':list(teacher['scopes']),'teacher_layout_checks':len(teacher['layout']),'pdf':teacher['pdf'],'text_preview':teacher['text_preview'],
                    'page_errors':student['page_errors']+teacher['page_errors'],
                    'weak_concepts':{k:v['api']['diagnosis_workbench']['current_weak_concept_count'] for k,v in student['students'].items()}}
        finally:
            for proc in [worker,api]:
                if proc.poll() is None:proc.terminate()
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
            for handle in handles:handle.close()
    try:
        receipt['checks']['default_snapshot']={'bootstrap':bootstrap_result()}
        serve('default_snapshot')
        print('Default snapshot/API/worker/frontend passed.',flush=True)
        run(['backend/scripts/restore_acceptance.py','--snapshot','workbench-review','--output','data/workbench-view'])
        env['MLQ_DATA_DIR']=str(root/'data/workbench-view')
        receipt['checks']['workbench_snapshot']={'bootstrap':bootstrap_result()}
        refused=subprocess.run([sys.executable,'backend/scripts/restore_acceptance.py','--snapshot','workbench-review','--output','data/workbench-view'],cwd=root,env=env,capture_output=True,creationflags=flags)
        assert refused.returncode!=0 and b'Refusing to overwrite' in refused.stderr
        receipt['restore_overwrite_protection']='passed'
        serve('workbench_snapshot')
        receipt['status']='passed'
    except Exception as exc:
        receipt['status']='failed'
        receipt['error']=str(exc)
        raise
    finally:save()
    print(json.dumps({'status':receipt['status'],'checks':receipt['checks']},ensure_ascii=False))


if __name__=='__main__':
    main()
