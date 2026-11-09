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
from common import PROJECT, catalog, safe_label, sha256, backend_command, linux_path, timed_process
from diagnostics import atomic_json, utc, windows_memory, json_file
from v5_resource_guard import HostGuard
from v5_trace_monitor import TraceObserver,TraceVerificationHeartbeat


def print_result(row,target):
    print('[RESULT] status=%s elapsed=%.1fs guide_sha256=%s trace_verification=%s output=%s'%(
        row['status'],row.get('runtime_seconds',0),row.get('guide_sha256') or 'NONE',
        row.get('trace_verification',{}).get('status','NOT_STARTED'),target),flush=True)
    print(json.dumps({'case':row['case'],'label':row['label'],'status':row['status'],
        'guide_sha256':row.get('guide_sha256'),'seconds':row.get('runtime_seconds'),
        'trace':row.get('trace_receipt'),'trace_verification':row.get('trace_verification'),
        'trace_error':row.get('trace_error'),'output_directory':str(target)},ensure_ascii=False),flush=True)


def trace_verification(receipt,directory,guide_hash,net_count):
    counts=receipt.get('counts',{});verified=counts.get('FINAL_ACCEPTED_VERIFIED',0)
    valid=(receipt.get('guide_sha256')==guide_hash and sum(counts.values())==net_count
           and receipt.get('trace_sha256')==sha256(directory/'route_trace.jsonl')
           and (directory/'route_trace.index.sqlite').is_file())
    status='FAILED' if not valid else 'VERIFIED_ALL' if verified==net_count else 'VERIFIED_PARTIAL' if verified else 'PATH_PROVENANCE_LOST'
    return {'status':status,'verified_nets':verified,'total_nets':net_count,'counts':counts,
            'guide_and_trace_identity_verified':valid}


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
    invocation_start=time.monotonic()
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
        row.update(runtime_seconds=time.monotonic()-invocation_start,output_directory=str(target),trace_verification={'status':'NOT_STARTED'})
        atomic_json(target/'run.json',row);print(json.dumps(row,ensure_ascii=False),flush=True);print_result(row,target);return row
    inputs={k:Path(c[k]).resolve() for k in ('def','lef','thermal')}
    hashes={k:sha256(p) for k,p in inputs.items()}
    trace_dir=target/'trace';guide=target/'route.guide'
    engine=PROJECT/'v5_trace/engine'
    if control_original:
        engine=target/'control_engine';shutil.copytree(PROJECT/'v5/engine',engine)
    source={p.relative_to(engine).as_posix():sha256(p) for p in engine.rglob('*') if p.is_file()}
    atomic_json(target/'source_identity.json',{'original':identity,'executed_engine':str(engine),'executed_source_sha256':source})
    environment=dict(os.environ,PYTHONPATH='',PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',PYTHONUNBUFFERED='1')
    for key in list(environment):
        if key.startswith('V5_TRACE_'):environment.pop(key)
    conv=linux_path if backend=='wsl' else lambda p:str(Path(p).resolve())
    trace_env={}
    if trace and not control_original:
        trace_env={'V5_TRACE_DIR':conv(trace_dir),'V5_TRACE_NETS':json.dumps(list(nets)), 'V5_TRACE_MAX_BYTES':str(max_bytes),'V5_TRACE_SEARCH':'1' if search else '0'}
        environment.update(trace_env)
    args=['--def',conv(inputs['def']),'--lef',conv(inputs['lef']),'--thermal-report',conv(inputs['thermal']),'--output',conv(guide)]
    run_id=uuid.uuid4().hex
    command=[sys.executable,'-B',str(engine/'final_router.py'),*args]
    if backend=='wsl':
        env={'PYTHONPATH':'','PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1','PYTHONUNBUFFERED':'1',**trace_env}
        supervised=['python3','-B',linux_path(PROJECT/'scripts/v5_resource_guard.py'),'--directory',conv(target),'--run-id',run_id,'--interval','2','--lease-seconds','120','--process-metrics','--','python3','-B',linux_path(engine/'final_router.py'),*args]
        command=backend_command(supervised,backend,cwd=engine,env=env)
    row={'schema_version':1,'run_id':run_id,'case':case,'label':label,'status':'RUNNING','returncode':None,'router_version':'v5_trace' if not control_original else 'v5','engine_type':'PYTHON_SCRIPT_ENGINE','inputs':{k:str(p) for k,p in inputs.items()},'input_sha256':hashes,'guide':str(guide),'trace_enabled':bool(trace and not control_original),'trace_options':{'nets':list(nets),'max_bytes':max_bytes,'search':search},'command':command,'backend':backend,'source_identity':'source_identity.json','original_default_parameters':True,'started_at':utc(),'resource_check':gate,
         'output_directory':str(target),'host_monitor_interval_seconds':5,'external_timeout_seconds':None,
         'trace_verification':{'status':'PENDING' if trace and not control_original else 'DISABLED'}}
    atomic_json(target/'run.json',row)
    start=time.monotonic()
    observer=TraceObserver(target,row['trace_enabled'])
    guard=HostGuard(target).start() if backend=='wsl' else None
    print('[RUN] case=%s backend=%s output=%s'%(case,backend,target),flush=True)
    try:
        # Infinite host deadline: all original per-stage/global budgets remain
        # inside final_router.py. The observer cannot decide to stop a Router.
        result=timed_process(command,target/'router.stdout.log',float('inf'),env=environment,
            monitor_dir=target,monitor_interval=5,native_resources=backend!='wsl',
            monitor_observer=observer,process_metrics_enabled=True)
        row.update(result)
        if guard:guard.close();guard=None
        row['router_runtime_seconds']=result['runtime_seconds']
        row['log_observation']=observer.finish()
        if guide.is_file():row['guide_sha256']=sha256(guide)
        receipt=json_file(guide.with_suffix('.run.json'))
        supervisor=json_file(target/'router_exit.json')
        row['supervisor_exit']=supervisor
        ok=(result['returncode']==0 and result['process_status']=='FINISHED' and guide.is_file()
            and receipt.get('validation',{}).get('status')=='LOCAL_PASS'
            and (backend!='wsl' or supervisor.get('run_id')==run_id and supervisor.get('router_returncode')==0 and not supervisor.get('termination_reason')))
        row['status']='ROUTED' if ok else 'INCOMPLETE'
        row['safety_stop']=json_file(target/'v5_safety_stop.json') or json_file(target/'v5_linux_safety_stop.json')
        if row['safety_stop']:row['status']='BLOCKED_RESOURCE'
        row['original_validation']=receipt.get('validation');row['original_engine_report']=receipt
        if row['status']=='ROUTED' and row['trace_enabled']:
            print('[TRACE] final Guide complete; verifying recorded path provenance',flush=True)
            verification_heartbeat=TraceVerificationHeartbeat(target,observer,start)
            try:
                sys.path.insert(0,str(PROJECT))
                from v5_trace.finalize import finalize
                spec=importlib.util.spec_from_file_location('_trace_design_reader',engine/'design.py')
                module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
                design=module.Design(inputs['def'],inputs['lef'])
                row['trace_receipt']=finalize(trace_dir,guide,design.layers,max_bytes=max_bytes//8,snapshot_path=linux_path(guide) if backend=='wsl' else None)
                row['trace_verification']=trace_verification(row['trace_receipt'],trace_dir,row['guide_sha256'],len(design.nets))
            except Exception as error:
                row['trace_error']=repr(error);row['trace_verification']={'status':'ERROR','error':repr(error)}
                print('[TRACE ERROR] '+repr(error),flush=True)
            finally:row['trace_monitor_errors']=verification_heartbeat.close()
        elif row['trace_enabled']:row['trace_verification']={'status':'NOT_FINALIZED','reason':'Router did not finish with an accepted Guide'}
    except Exception as error:
        row.update(status='INCOMPLETE',failure_reason=repr(error))
        print('[ERROR] '+repr(error),flush=True)
    finally:
        if guard:guard.close()
        row['log_observation']=observer.finish()
        row['runtime_seconds']=time.monotonic()-start;row['finished_at']=utc()
        try:row['original_source_unchanged']=source_identity()==identity
        except Exception as error:row.update(original_source_unchanged=False,source_integrity_error=repr(error))
        try:row['inputs_unchanged']=hashes=={k:sha256(p) for k,p in inputs.items()}
        except OSError as error:row.update(inputs_unchanged=False,input_integrity_error=repr(error))
        if not row['original_source_unchanged'] or not row['inputs_unchanged']:row['status']='INCOMPLETE'
        atomic_json(target/'run.json',row)
    print_result(row,target)
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
