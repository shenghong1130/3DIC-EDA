"""V5 identity adapter to the unchanged project REFERENCE_20261103 scorer."""
import argparse
import json
import os
import time
import uuid
from pathlib import Path
from common import PROJECT, catalog, sha256, safe_label, linux_path, backend_command, timed_process
from diagnostics import atomic_json, windows_memory
from evaluate_reference_20261103 import source_identity
from scoring import summary, write_index
from run_v5_case import verify, resource_check, REPORTS
from v5_resource_guard import HostGuard

SCORER=PROJECT/'outputs/development/v4_3/reference_20261103'
SCORER_SHA='8ab0ed59a85ac7003cdacfe31bac93412591ee1717d46b2ab0ba8b44a4fdd271'
SOURCE_SHA='9a33e788a847f356bdea51fc596ea6fe66e1d22f606c049bf7eb62021caf8b51'


def scoring_resources_available(check,host,memory_mb):
    return (check['allowed'] and host.get('commit_available_bytes',0)>=(memory_mb+1024)*2**20
            and check.get('linux',{}).get('mem_available_bytes',0)>=(memory_mb+512)*2**20)


def execute(case,label,memory_mb=1536,timeout=600,force_resource_gate=False):
    manifest=verify();safe_label(case);safe_label(label)
    run_path=PROJECT/'outputs/runs'/case/label
    run=json.loads((run_path/'run.json').read_text(encoding='utf8'))
    inputs={k:Path(catalog()[case][k]) for k in ['def','lef','thermal']}
    hashes={k:sha256(p) for k,p in inputs.items()};guide=run_path/'route.guide'
    if (run.get('status')!='ROUTED' or run.get('returncode')!=0 or run.get('engine_type')!='PYTHON_SCRIPT_ENGINE'
        or run.get('engine_zip_sha256')!=manifest['selected_zip_sha256'] or run.get('source_sha256')!=manifest['files']
        or run.get('input_sha256')!=hashes or sha256(guide)!=run.get('guide_sha256')):
        raise ValueError('V5 run/input/source/Guide identity mismatch')
    if sha256(SCORER)!=SCORER_SHA or source_identity()[0]!=SOURCE_SHA:
        raise ValueError('Pinned reference scorer identity changed; no substitution')
    target=PROJECT/'outputs/reports/scoring/reference_20261103'/case/label
    if target.exists():
        result=summary(case,label,'reference_20261103')
        if result['status'] in ('REFERENCE_VALID','REFERENCE_INVALID'):return result
        raise ValueError('Existing incomplete report protected: '+str(target))
    check=resource_check(case,'wsl',force_resource_gate);host=windows_memory()
    check.update(scoring_windows_snapshot=host,scoring_commit_required_bytes=(memory_mb+1024)*2**20,scoring_linux_required_bytes=(memory_mb+512)*2**20)
    if not scoring_resources_available(check,host,memory_mb):
        result={'case':case,'label':label,'status':'BLOCKED_RESOURCE','resource_check':check,'memory_mb':memory_mb}
        dest=REPORTS/'scoring_gates'/(case+'_'+str(time.time_ns())+'.json');dest.parent.mkdir(parents=True,exist_ok=True);atomic_json(dest,result)
        return result
    target.mkdir(parents=True,exist_ok=False)
    ident={'case':case,'label':label,'ruleset':'REFERENCE_20261103','official':False,'guide_sha256':sha256(guide),
           'input_sha256':hashes,'router_sha256':run['router_sha256'],'engine_zip_sha256':manifest['selected_zip_sha256'],
           'engine_type':'PYTHON_SCRIPT_ENGINE','config_sha256':None,'reference_source_sha256':SOURCE_SHA,
           'pin_assignment_policy':'nearest_in_cell'}
    command=[linux_path(SCORER),linux_path(inputs['lef']),linux_path(inputs['def']),linux_path(inputs['thermal']),
             linux_path(guide),linux_path(target),'--memory-mb',str(memory_mb),'--pin-assignment','nearest_in_cell']
    invocation={'identity':ident,'status':'RUNNING','compiled_binary_sha256':SCORER_SHA,'official':False,'scorer_id':'REFERENCE_20261103','resource_check':check,'command':command}
    atomic_json(target/'invocation.json',invocation)
    args=['python3',linux_path(PROJECT/'scripts/linux_supervisor.py'),'--directory',linux_path(target),'--run-id',uuid.uuid4().hex,
          '--process-kind','evaluator','--timeout',str(timeout),'--',*command]
    call=backend_command(args,'wsl',cwd=PROJECT,time_file=target/'resource_usage.txt')
    guard=HostGuard(target).start()
    try:
        execution=timed_process(call,target/'evaluator.log',timeout+60,monitor_dir=target,process_role='evaluator')
    except Exception as error:
        execution={'returncode':None,'process_status':'LAUNCH_ERROR','error':repr(error)}
    finally:
        guard.close()
    invocation.update(execution)
    report_path=target/'reference_report.json'
    if execution['returncode']==0 and report_path.exists():
        report=json.loads(report_path.read_text(encoding='utf8'))
        if sha256(guide)!=ident['guide_sha256'] or hashes!={k:sha256(p) for k,p in inputs.items()}:
            invocation.update(status='ERROR',error='Input changed during scoring')
        else:
            report.update(guide_sha256=ident['guide_sha256'],input_sha256=hashes,reference_source_sha256=SOURCE_SHA,reference_scorer_sha256=SCORER_SHA)
            atomic_json(report_path,report);invocation['status']='COMPLETED'
    else:invocation.update(status='ERROR',error='ANALYSIS_ERROR: see process receipt and evaluator.log; not an official FAIL')
    atomic_json(target/'invocation.json',invocation)
    result=summary(case,label,'reference_20261103');write_index(result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case',required=True);p.add_argument('--label',required=True)
    p.add_argument('--memory-mb',type=int,default=1536);p.add_argument('--timeout',type=int,default=600)
    p.add_argument('--force-resource-gate',action='store_true',help='Bypass generic reserve only; keep scoring budget plus headroom and memory safety')
    a=p.parse_args()
    if not 256<=a.memory_mb<=8192 or not 0<a.timeout<=7200:p.error('Invalid reference resource budget')
    r=execute(a.case,a.label,a.memory_mb,a.timeout,a.force_resource_gate)
    print(json.dumps({k:r.get(k) for k in ['case','label','status','metrics','runtime_seconds','peak_rss_bytes','report_path']},ensure_ascii=False),flush=True)
    raise SystemExit(0 if r['status'] in ['REFERENCE_VALID','REFERENCE_INVALID'] else 2)
