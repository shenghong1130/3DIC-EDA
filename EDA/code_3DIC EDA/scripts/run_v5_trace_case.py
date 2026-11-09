"""Independent local analysis runner. Trace is OFF unless --trace is given."""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from common import PROJECT, catalog, safe_label, sha256, backend_command, linux_path
from diagnostics import atomic_json, utc, windows_memory


def startup_check(case,backend):
    """Read-only launch gate; never stop any existing process."""
    if os.name=='nt':
        command="Get-CimInstance Win32_Process | Where-Object {($_.Name -match '^(router|score|reference_202).*\\.exe$') -or ($_.Name -match '^python' -and $_.CommandLine -match '(final_router|portable)\\.py')} | Select-Object ProcessId,Name | ConvertTo-Json -Compress"
        probe=subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True,timeout=30)
        if probe.returncode or probe.stdout.strip():return {'allowed':False,'reason':'An existing Router/scorer is active or process inventory failed','inventory':probe.stdout.decode('utf8','replace')}
        memory=windows_memory()
        if memory.get('commit_available_bytes',0)<2*1024**3:return {'allowed':False,'reason':'Insufficient Windows commit reserve','memory':memory}
    if case not in ('toy','sample001','sample001_legacy'):
        if os.name=='nt' and backend=='native':return {'allowed':False,'reason':'Use --backend wsl for large cases and the existing Linux resource gate'}
        from run_v5_case import resource_check
        return resource_check(case,backend)
    return {'allowed':True,'reason':'Small local analysis run; no existing process is terminated'}


def source_identity():
    expected=json.loads((PROJECT/'v5_trace/original_sha256.json').read_text(encoding='utf8'))
    actual={p.relative_to(PROJECT/'v5/engine').as_posix():sha256(p) for p in (PROJECT/'v5/engine').rglob('*') if p.is_file()}
    if actual!=expected['files']:raise ValueError('Original V5 engine changed')
    return expected


def execute(case,label=None,trace=False,nets=(),max_bytes=256*1024*1024,search=False,backend='native',control_original=False):
    identity=source_identity();c=catalog().get(case)
    if not c:raise ValueError('Unknown Case '+case)
    label=safe_label(label or case.removeprefix('open_3d_')+'_v5_trace')
    target=PROJECT/'outputs/runs'/case/label
    if target.exists():
        label+='_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
        target=target.parent/label
    target.mkdir(parents=True,exist_ok=False)
    gate=startup_check(case,backend)
    if not gate['allowed']:
        row={'case':case,'label':label,'status':'BLOCKED_RESOURCE','returncode':None,'router_version':'v5_trace','engine_type':'PYTHON_SCRIPT_ENGINE','resource_check':gate}
        atomic_json(target/'run.json',row);print(json.dumps(row,ensure_ascii=False),flush=True);return row
    inputs={k:Path(c[k]).resolve() for k in ('def','lef','thermal')}
    hashes={k:sha256(p) for k,p in inputs.items()}
    trace_dir=target/'trace';guide=target/'route.guide'
    engine=PROJECT/'v5_trace/engine'
    if control_original:
        engine=target/'control_engine';shutil.copytree(PROJECT/'v5/engine',engine)
    source={p.relative_to(engine).as_posix():sha256(p) for p in engine.rglob('*') if p.is_file()}
    atomic_json(target/'source_identity.json',{'original':identity,'executed_engine':str(engine),'executed_source_sha256':source})
    environment=dict(os.environ,PYTHONPATH='',PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
    for key in list(environment):
        if key.startswith('V5_TRACE_'):environment.pop(key)
    conv=linux_path if backend=='wsl' else lambda p:str(Path(p).resolve())
    trace_env={}
    if trace and not control_original:
        trace_env={'V5_TRACE_DIR':conv(trace_dir),'V5_TRACE_NETS':json.dumps(list(nets)), 'V5_TRACE_MAX_BYTES':str(max_bytes),'V5_TRACE_SEARCH':'1' if search else '0'}
        environment.update(trace_env)
    args=['--def',conv(inputs['def']),'--lef',conv(inputs['lef']),'--thermal-report',conv(inputs['thermal']),'--output',conv(guide)]
    command=[sys.executable,'-B',str(engine/'final_router.py'),*args]
    if backend=='wsl':
        env={'PYTHONPATH':'','PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1',**trace_env}
        command=backend_command(['python3','-B',linux_path(engine/'final_router.py'),*args],backend,cwd=engine,env=env)
    row={'schema_version':1,'run_id':uuid.uuid4().hex,'case':case,'label':label,'status':'RUNNING','returncode':None,'router_version':'v5_trace' if not control_original else 'v5','engine_type':'PYTHON_SCRIPT_ENGINE','inputs':{k:str(p) for k,p in inputs.items()},'input_sha256':hashes,'guide':str(guide),'trace_enabled':bool(trace and not control_original),'trace_options':{'nets':list(nets),'max_bytes':max_bytes,'search':search},'command':command,'backend':backend,'source_identity':'source_identity.json','original_default_parameters':True,'started_at':utc(),'resource_check':gate}
    atomic_json(target/'run.json',row)
    start=time.monotonic()
    try:
        with (target/'router.log').open('w',encoding='utf8') as log:
            result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,env=environment)
        row['returncode']=result.returncode
        if guide.is_file():row['guide_sha256']=sha256(guide)
        receipt=json.loads(guide.with_suffix('.run.json').read_text(encoding='utf8')) if guide.with_suffix('.run.json').is_file() else {}
        row['status']='ROUTED' if result.returncode==0 and receipt.get('validation',{}).get('status')=='LOCAL_PASS' else 'INCOMPLETE'
        row['original_validation']=receipt.get('validation');row['original_engine_report']=receipt
        if row['status']=='ROUTED' and row['trace_enabled']:
            try:
                sys.path.insert(0,str(PROJECT))
                from v5_trace.finalize import finalize
                spec=importlib.util.spec_from_file_location('_trace_design_reader',engine/'design.py')
                module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
                layers=module.Design(inputs['def'],inputs['lef']).layers
                row['trace_receipt']=finalize(trace_dir,guide,layers,max_bytes=max_bytes//8,snapshot_path=linux_path(guide) if backend=='wsl' else None)
            except Exception as error:row['trace_error']=repr(error)
    finally:
        row['runtime_seconds']=time.monotonic()-start;row['finished_at']=utc()
        row['original_source_unchanged']=source_identity()==identity
        row['inputs_unchanged']=hashes=={k:sha256(p) for k,p in inputs.items()}
        atomic_json(target/'run.json',row)
    print(json.dumps({'case':case,'label':label,'status':row['status'],'guide_sha256':row.get('guide_sha256'),'seconds':row['runtime_seconds'],'trace':row.get('trace_receipt'),'trace_error':row.get('trace_error')},ensure_ascii=False),flush=True)
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case',choices=sorted(catalog()),required=True);p.add_argument('--label')
    p.add_argument('--trace',action='store_true');p.add_argument('--trace-net',action='append',default=[])
    p.add_argument('--trace-search',action='store_true');p.add_argument('--trace-max-mib',type=int,default=256)
    p.add_argument('--backend',choices=['native','wsl'],default='native')
    p.add_argument('--control-original',action='store_true',help='Differential control in an isolated byte-identical original copy')
    a=p.parse_args()
    if a.trace_max_mib<1:p.error('Trace limit must be positive')
    row=execute(a.case,a.label,a.trace,a.trace_net,a.trace_max_mib*1024*1024,a.trace_search,a.backend,a.control_original)
    return 0 if row['status']=='ROUTED' else 2


if __name__=='__main__':raise SystemExit(main())
