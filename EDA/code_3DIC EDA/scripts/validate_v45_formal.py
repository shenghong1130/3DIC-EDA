"""Sequential V4.5 public-case validation on the measured native platform.

No compilation, registry promotion, official scoring, retry or remote operation.
The report explicitly distinguishes native PE from unverified Linux execution.
"""
import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path
from common import PROJECT, catalog, sha256, timed_process
from diagnostics import windows_memory, atomic_json, utc
from validate_v44_peer import source_hashes, save
from validate_v45 import ROOT, NativeMemory


def resource_gate(case):
    minimum=(6 if case.endswith('005') else 2)*2**30
    memory=windows_memory()
    proc=subprocess.run(['powershell','-NoProfile','-Command',
        r"Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^(router|reference_202|score)(.*)(\.exe)?$'} | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"],capture_output=True,timeout=30)
    raw=proc.stdout.decode('utf8','replace').strip()
    busy=bool(raw and raw!='null')
    return dict(allowed=proc.returncode==0 and not busy and memory.get('commit_available_bytes',0)>=minimum and memory.get('physical_available_bytes',0)>=minimum and shutil.disk_usage(PROJECT).free>=20*2**30,
        windows=memory,required_bytes=minimum,disk_free_bytes=shutil.disk_usage(PROJECT).free,busy=busy,process_probe=raw,
        platform='Windows native; WSL is not started by this gate',historical_case003_linux_peak_bytes=952193024,
        historical_case005_linux_peak_bytes=3689791488,reason='Native headroom gate; case005 retains 6 GiB minimum')


def execute(case, attempt):
    from compare_v36 import small_metadata
    from validate_v4_3 import output_audit
    receipt_path=ROOT/'builds'/attempt/'complete.json'
    receipt=json.loads(receipt_path.read_text(encoding='utf8'))
    if receipt['source']!=source_hashes():raise ValueError('Compiled source changed')
    binary=PROJECT/receipt['binary']
    if sha256(binary)!=receipt['router_sha256']:raise ValueError('Changed Router')
    version=subprocess.check_output([str(binary),'--version'],text=True).strip()
    if not version.startswith('router v4_5 EXPERIMENTAL'):raise ValueError('Unexpected Router version')
    config=PROJECT/'configs/v4_5.json';cfg=json.loads(config.read_text(encoding='utf8'))
    inputs={k:Path(catalog()[case][k]) for k in ['def','lef','thermal']}
    identities={k:sha256(p) for k,p in inputs.items()}
    stem=case.removeprefix('open_3d_')+'_v4_5';label=stem;parent=PROJECT/'outputs/runs'/case
    target=parent/label
    if target.exists():
        record=json.loads((target/'run.json').read_text(encoding='utf8')) if (target/'run.json').is_file() else {}
        if record.get('status')=='ROUTED' and record.get('router_sha256')==sha256(binary) and record.get('config_sha256')==sha256(config) and record.get('input_sha256')==identities and (target/'route.guide').is_file() and record.get('guide_sha256')==sha256(target/'route.guide'):
            return {'status':'REUSED_ROUTED','label':label,'path':str(target),'run':record}
        suffix=2
        while target.exists():label=stem+'_run'+str(suffix);target=parent/label;suffix+=1
    check=resource_gate(case)
    gate_path=ROOT/'formal_gates'/case/(str(time.time_ns())+'.json');save(gate_path,check)
    if not check['allowed']:
        print(case,'BLOCKED_RESOURCE',flush=True)
        return {'status':'BLOCKED_RESOURCE','label':label,'planned_path':str(target),'gate':check,'gate_path':str(gate_path)}
    target.mkdir(parents=True,exist_ok=False)
    budget=600 if case.endswith('001') else 3600 if case.endswith('003') else 14400
    command=[str(binary),'--def',str(inputs['def']),'--lef',str(inputs['lef']),'--thermal-report',str(inputs['thermal']),
        '--config',str(config),'--output',str(target/'route.guide'),'--metrics',str(target/'result.json'),
        '--time-limit',str(budget),'--progress-dir',str(target)]
    record={'schema_version':1,'case':case,'label':label,'backend':'native','router_version':'v4_5','router_version_status':'EXPERIMENTAL_UNREGISTERED',
        'router_binary':str(binary.relative_to(PROJECT)),'router_sha256':sha256(binary),'version_output':version,'build_receipt':str(receipt_path),
        'config':cfg,'config_path':'configs/v4_5.json','config_sha256':sha256(config),'config_overrides':{'time_limit':budget},
        'inputs':{k:str(p) for k,p in inputs.items()},'input_sha256':identities,'source_sha256':receipt['source'],
        'guide':str(target/'route.guide'),'command':command,'started_at':utc(),'status':'RUNNING','returncode':None,
        'official_status':'NOT_EVALUATED','platform':'Windows MinGW O1, not Linux validation','resource_gate':check}
    atomic_json(target/'run.json',record)
    with NativeMemory(target) as monitor:
        result=timed_process(command,target/'router.log',budget+30,cwd=PROJECT,monitor_dir=target,native_resources=False)
    record.update(result,finished_at=utc(),peak_memory_bytes=monitor.peak,peak_memory_source='Windows PeakWorkingSetSize',windows_memory_error=monitor.error)
    ok=result['returncode']==0 and result['process_status']=='FINISHED' and (target/'route.guide').is_file() and (target/'result.json').is_file()
    record['status']='ROUTED' if ok else 'FAIL';record['failure_category']=None if ok else 'UNKNOWN_REVIEW_LOGS'
    if ok:
        record['guide_sha256']=sha256(target/'route.guide')
        record['result_sha256']=sha256(target/'result.json')
        record['algorithm']=small_metadata(target/'result.json')
        record['output_audit']=output_audit(target/'result.json')
    atomic_json(target/'run.json',record)
    print(case,label,record['status'],flush=True)
    return {'status':record['status'],'label':label,'path':str(target),'run':record,'reference_status':'NOT_EVALUATED'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--attempt',required=True);p.add_argument('--cases',nargs='+',choices=['open_3d_case001','open_3d_case003','open_3d_case005'],default=['open_3d_case001','open_3d_case003','open_3d_case005']);a=p.parse_args()
    output=ROOT/'formal_validation.json';data=json.loads(output.read_text(encoding='utf8')) if output.exists() else {}
    for case in sorted(set(a.cases)):
        data[case]=execute(case,a.attempt);atomic_json(output,data)


if __name__=='__main__':main()
