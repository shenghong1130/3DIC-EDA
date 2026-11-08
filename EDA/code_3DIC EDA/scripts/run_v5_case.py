"""External V5 adapter: original Python engine, no algorithm/config overrides."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from common import PROJECT, catalog, safe_label, sha256, linux_path, backend_command, timed_process
from diagnostics import windows_memory, atomic_json, utc

sys.path.insert(0, str(PROJECT/'v5'))
from verify_source import verify

REPORTS = PROJECT/'outputs/reports/v5'


def read_receipt(path, errors):
    if not path.exists():return {}
    try:
        value=json.loads(path.read_text(encoding='utf8'))
        if not isinstance(value,dict):raise ValueError('Receipt must be an object')
        return value
    except (OSError,ValueError) as error:
        errors.append(str(path)+': '+str(error));return {}


def resource_check(case, backend):
    before = windows_memory() if os.name == 'nt' else {}
    minimum = (6 if case.endswith(('003', '005')) else 2)*2**30
    if os.name == 'nt':
        command=['powershell','-NoProfile','-Command',
                 "Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^(router|reference_202|score).*\\.exe$'} | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"]
        native=subprocess.run(command,capture_output=True,timeout=30)
        if native.returncode or native.stdout.strip():
            return {'allowed':False,'reason':'Native Router/scorer present or process inventory unavailable',
                    'windows':before,'native_process_inventory':native.stdout.decode('utf8','replace'),'required_bytes':minimum}
    if os.name == 'nt' and before.get('commit_available_bytes', 0) < minimum:
        return {'allowed': False, 'reason': 'Windows Commit below resource gate', 'windows': before, 'required_bytes': minimum}
    probe = """import json,subprocess,sys,os
from pathlib import Path
m={k:int(v.split()[0])*1024 for k,v in (line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())}
rows=subprocess.check_output(['ps','-eo','pid=,args='],text=True).splitlines()
busy=[]
for row in rows:
 parts=row.strip().split(None,1)
 if len(parts)!=2:continue
 pid,cmd=parts
 if int(pid)==os.getpid() or 'python3 -c' in cmd:continue
 if any(s in cmd for s in ['final_router.py','baseline_v12/portable.py','router_v','reference_202','/score --']):busy.append(row)
print(json.dumps({'mem_available_bytes':m['MemAvailable'],'swap_free_bytes':m['SwapFree'],'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'python':sys.version,'busy':busy}))
"""
    command = backend_command(['python3','-c',probe],backend)
    result = subprocess.run(command,capture_output=True,timeout=45)
    if result.returncode:
        return {'allowed':False,'reason':'Linux probe failed','returncode':result.returncode,'stderr_hex':result.stderr.hex(),'windows':before}
    linux = json.loads(result.stdout.decode('utf8'))
    after = windows_memory() if os.name == 'nt' else {}
    free = shutil.disk_usage(PROJECT).free
    allowed = (not linux['busy'] and linux['mem_available_bytes'] >= minimum and free >= 20*2**30
               and (os.name != 'nt' or after.get('commit_available_bytes',0) >= minimum))
    return {'allowed':allowed,'windows':after,'linux':linux,'disk_free_bytes':free,'required_bytes':minimum,
            'reason':'Resource reservation only; does not change engine budgets or algorithms'}


def execute(case, label=None, backend='wsl'):
    manifest = verify()
    c = catalog().get(case)
    if not c:
        raise ValueError('Unknown Case '+case)
    inputs = {k:Path(c[k]) for k in ['def','lef','thermal']}
    hashes = {k:sha256(v) for k,v in inputs.items()}
    label = safe_label(label or case.removeprefix('open_3d_')+'_v5')
    parent=PROJECT/'outputs/runs'/case;target=parent/label
    original_label=label;number=2
    while target.exists():
        label=original_label+'_run'+str(number);target=parent/label;number+=1
    check=resource_check(case,backend)
    REPORTS.mkdir(parents=True,exist_ok=True)
    gate=REPORTS/'resource_gates'/(case+'_'+str(time.time_ns())+'.json');gate.parent.mkdir(exist_ok=True)
    atomic_json(gate,check)
    if not check['allowed']:
        row={'case':case,'label':label,'status':'BLOCKED_RESOURCE','guide_generated':False,'gate':str(gate),'resource_check':check}
        print(json.dumps(row,ensure_ascii=False),flush=True)
        return row
    target.mkdir(parents=True,exist_ok=False)
    conv=linux_path if backend=='wsl' else lambda p:str(Path(p).resolve())
    guide=target/'route.guide';run_id=uuid.uuid4().hex
    original_args=['--def',conv(inputs['def']),'--lef',conv(inputs['lef']),'--thermal-report',conv(inputs['thermal']),'--output',conv(guide)]
    launch=['bash',conv(PROJECT/'v5/run_v5.sh'),*original_args]
    supervised=['python3',conv(PROJECT/'scripts/linux_supervisor.py'),'--directory',conv(target),'--run-id',run_id,'--interval','5','--lease-seconds','120','--',*launch]
    # No external algorithm deadline: preserve the original 39600s policy,
    # including its own final validation and checkpoint recovery.
    environment={'PYTHONPATH':'','PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'}
    command=backend_command(supervised,backend,cwd=PROJECT/'v5/engine',time_file=target/'resource_usage.txt',env=environment)
    row={'schema_version':1,'run_id':run_id,'case':case,'label':label,'backend':backend,'status':'RUNNING','returncode':None,
         'router_version':'v5','router_version_status':'ORIGINAL_SOURCE_VERIFIED','engine_type':'PYTHON_SCRIPT_ENGINE',
         'router_binary':'v5/engine/run.sh','router_sha256':manifest['files']['run.sh'],
         'engine_zip_sha256':manifest['selected_zip_sha256'],'source_sha256':manifest['files'],
         'inputs':{k:str(v) for k,v in inputs.items()},'input_sha256':hashes,'config':None,'config_path':None,'config_sha256':None,
         'config_overrides':{},'original_default_parameters':True,'original_internal_budget_seconds':39600,'external_timeout_seconds':None,
         'guide':str(guide),'original_arguments':original_args,'command':command,'environment':environment,
         'official_status':'NOT_EVALUATED','reference_status':'NOT_EVALUATED','started_at':utc(),'resource_check':check}
    atomic_json(target/'run.json',row)
    print(json.dumps({'case':case,'label':label,'status':'RUNNING','path':str(target),'original_defaults':True},ensure_ascii=False),flush=True)
    try:
        execution=timed_process(command,target/'router.log',float('inf'),cwd=PROJECT,monitor_dir=target,monitor_interval=5,stall_after=300)
        row.update(execution)
    except Exception as error:
        row.update(process_status='LAUNCH_ERROR',failure_reason=repr(error))
    finally:
        row['finished_at']=utc()
        try:verify();row['source_integrity_after']='PASS'
        except Exception as error:row['source_integrity_after']='FAIL';row['integrity_error']=str(error)
        row['receipt_errors']=[]
        try:row['inputs_unchanged']=hashes=={k:sha256(p) for k,p in inputs.items()}
        except OSError as error:row['inputs_unchanged']=False;row['receipt_errors'].append(str(error))
        original_report=guide.with_suffix('.run.json')
        report=read_receipt(original_report,row['receipt_errors'])
        row['original_report']=str(original_report) if report else None
        row['original_validation']=report.get('validation');row['original_engine_report']=report
        exit_path=target/'router_exit.json'
        receipt=read_receipt(exit_path,row['receipt_errors'])
        row['supervisor_exit']=receipt
        row['peak_memory_bytes']=receipt.get('child_peak_rss_bytes')
        row['peak_memory_source']=receipt.get('child_peak_rss_source','unavailable')
        ok=(row.get('returncode')==0 and receipt.get('router_returncode')==0 and guide.is_file()
            and report.get('validation',{}).get('status')=='LOCAL_PASS' and report.get('development_small_budget') is False
            and report.get('input_sha256')==hashes and row['inputs_unchanged'] and row['source_integrity_after']=='PASS')
        row['status']='ROUTED' if ok else 'INCOMPLETE'
        if guide.is_file():row['guide_sha256']=sha256(guide)
        row['failure_category']=None if ok else 'UNKNOWN_REVIEW_PROCESS_AND_ORIGINAL_STAGE_LOGS'
        atomic_json(target/'run.json',row)
    print(json.dumps({'case':case,'label':label,'status':row['status'],'runtime_seconds':row.get('runtime_seconds'),'guide_sha256':row.get('guide_sha256')},ensure_ascii=False),flush=True)
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case',choices=sorted(catalog()),required=True)
    p.add_argument('--label');p.add_argument('--backend',choices=['wsl','native'],default='wsl' if os.name=='nt' else 'native')
    a=p.parse_args()
    if os.name=='nt' and a.backend=='native':p.error('Original engine requires Linux bash; use --backend wsl')
    row=execute(a.case,a.label,a.backend)
    index=REPORTS/'case_results.json';data=json.loads(index.read_text(encoding='utf8')) if index.exists() else {}
    data.setdefault(a.case,[]).append(row);atomic_json(index,data)
    return 0 if row['status']=='ROUTED' else 2


if __name__=='__main__':raise SystemExit(main())
