"""Explicit, low-memory one-Net Toy differential; never an official Case.

Use an existing Linux environment. The normal platform launch gate is retained;
the Windows 2 GiB reservation is not overridden. No budget flags are supplied.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from common import catalog,sha256,backend_command,linux_path,timed_process
from diagnostics import atomic_json,ResourceMonitor
from v5_resource_guard import linux_pressure,HostGuard,host_pressure
from v5_trace_monitor import TraceObserver
from diagnostics import windows_memory
import run_v5_trace_case as runner


def transport(output):
    """Windows -> WSL transport fixture only; no Router or official input."""
    if os.name!='nt':raise ValueError('Transport fixture requires Windows')
    if host_pressure(windows_memory()):raise RuntimeError('Existing memory safety blocks the tiny transport fixture')
    output.mkdir(parents=True,exist_ok=False);run_id='monitor-transport-fixture'
    code="""from pathlib import Path
import sys,time
d=Path(sys.argv[1]);stage=d/'.final-router-fixture';stage.mkdir()
(stage/'baseline.log').write_text('INITIAL fixture only\\n')
time.sleep(6)
print('STAGE baseline accepted True',flush=True)
(stage/'wire.log').write_text('INITIAL transport fixture only\\n')
print('complete transport fixture',flush=True)
"""
    args=['python3','-B',linux_path(ROOT/'scripts/v5_resource_guard.py'),'--directory',linux_path(output),'--run-id',run_id,'--process-metrics','--','python3','-B','-c',code,linux_path(output)]
    command=backend_command(args,'wsl',cwd=ROOT,env={'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1'})
    observer=TraceObserver(output,False);guard=HostGuard(output).start()
    try:
        result=timed_process(command,output/'router.stdout.log',float('inf'),monitor_dir=output,monitor_interval=5,monitor_observer=observer)
    finally:guard.close();observer.finish()
    events=[json.loads(line) for line in (output/'host_monitor.jsonl').read_text(encoding='utf8').splitlines()]
    exit_record=json.loads((output/'router_exit.json').read_text(encoding='utf8'))
    chunks=[json.loads(line) for line in (output/'router.log.index.jsonl').read_text(encoding='utf8').splitlines()]
    aggregate=(output/'router.log').read_bytes()
    original=b''.join(aggregate[r['aggregate_offset']:r['aggregate_offset']+r['length']] for r in chunks if r['source']==str(output/'router.stdout.log'))
    checks={'proxy_exit_zero':result['returncode']==0,'router_exit_zero':exit_record['router_returncode']==0,
            'run_identity_matches':exit_record['run_id']==run_id,'no_safety_stop':exit_record['termination_reason'] is None,
            'real_cpu_from_linux':any(e.get('cpu_percent') is not None for e in events),
            'real_rss_from_linux':any(e.get('rss_bytes',0) for e in events),
            'real_observed_stage':any(e.get('stage')=='baseline' for e in events),
            'raw_stdout_retained':(output/'router.stdout.log').read_bytes()==original,
            'all_progress_unknown':all(e['progress_status']=='PROGRESS_UNKNOWN' for e in events)}
    receipt={'status':'PASS' if all(checks.values()) else 'FAIL','scope':'Synthetic transport fixture, not a routed Guide','checks':checks,'execution':result,'directory':str(output)}
    atomic_json(output/'transport.json',receipt);print(json.dumps(receipt,ensure_ascii=False),flush=True)
    return 0 if receipt['status']=='PASS' else 1


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--transport',action='store_true');a=p.parse_args()
    if a.transport:return transport(a.output)
    if not sys.platform.startswith('linux'):p.error('Linux-only tiny fixture; use the existing WSL distro')
    a.output.mkdir(parents=True,exist_ok=False)
    monitor=ResourceMonitor(a.output/'probe_resources.csv',os.getpid(),process_metrics_enabled=True)
    memory=monitor.sample();monitor.finish()
    if linux_pressure(memory) or memory.get('mem_available_bytes',0)<1024**3:raise RuntimeError('Insufficient Linux reserve for the one-Net fixture')
    toy=catalog()['toy'];source=Path(toy['def']);text=source.read_text()
    match=re.search(r'(?ms)^NETS\s+\d+\s*;(.*?)^END NETS',text)
    first=re.search(r'(?ms)^\s*-\s+.*?;',match[1])[0]
    fixture=a.output/'one_net.input.def'
    fixture.write_text(text[:match.start()]+'NETS 1 ;\n'+first+'\nEND NETS'+text[match.end():],encoding='utf8')
    inputs={**toy,'def':str(fixture)}
    hashes={k:sha256(Path(inputs[k])) for k in ('def','lef','thermal')}
    # Unmonitored reference: identical Trace engine/inputs/default arguments.
    control=a.output/'control';control.mkdir();guide=control/'route.guide'
    env=dict(os.environ,PYTHONPATH='',PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',PYTHONUNBUFFERED='1')
    for key in list(env):
        if key.startswith('V5_TRACE_'):env.pop(key)
    env.update(V5_TRACE_DIR=str(control/'trace'),V5_TRACE_NETS='[]',V5_TRACE_MAX_BYTES=str(256*2**20),V5_TRACE_SEARCH='0')
    args=['--def',inputs['def'],'--lef',inputs['lef'],'--thermal-report',inputs['thermal']]
    with (control/'router.stdout.log').open('wb') as f:
        reference=subprocess.run([sys.executable,'-B',str(ROOT/'v5_trace/engine/final_router.py'),*args,'--output',str(guide)],env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
    report=json.loads(guide.with_suffix('.run.json').read_text())
    if report['validation']['status']!='LOCAL_PASS':raise RuntimeError('Toy control invalid')
    # Local interpreter-only catalog override: no catalog or real input edited.
    runner.catalog=lambda:{'toy':inputs}
    label='toy_monitor_1net_'+time.strftime('%Y%m%d_%H%M%S')
    actual=runner.execute('toy',label,trace=True,backend='native')
    out=Path(actual['output_directory']);events=[json.loads(s) for s in (out/'host_monitor.jsonl').read_text().splitlines()]
    live=[r for r in events if r['subprocess_status']=='RUNNING']
    diffs={
        'guide_sha256_equal':actual.get('guide_sha256')==sha256(guide),
        'local_validation_equal':actual.get('original_validation')==report['validation'],
        'stage_acceptance_equal':[(r['stage'],r['accepted']) for r in actual['original_engine_report']['stages']]==[(r['stage'],r['accepted']) for r in report['stages']],
        'stage_metrics_equal':[r.get('metrics') for r in actual['original_engine_report']['stages']]==[r.get('metrics') for r in report['stages']],
        'original_budgets_preserved':actual['original_engine_report']['runtime_budget_seconds']==report['runtime_budget_seconds']==39600 and not actual['original_engine_report']['development_small_budget'],
        'inputs_unchanged':hashes=={k:sha256(Path(inputs[k])) for k in hashes},
        'no_false_progress':all(r['progress_status']=='PROGRESS_UNKNOWN' for r in events),
        'real_cpu_samples':any(r.get('cpu_percent') is not None for r in live),
        'real_memory_samples':any(r.get('rss_bytes',0) for r in live),
    }
    # Real Linux resource guard path, on a new tiny CPU fixture only. Existing
    # Router PIDs are never supplied to the supervisor or its signal machinery.
    guard=a.output/'guard_probe';guard.mkdir()
    code='import time\nend=time.monotonic()+.5\nwhile time.monotonic()<end: sum(range(10000))'
    with (guard/'stdout.log').open('wb') as f:
        subprocess.run([sys.executable,'-B',str(ROOT/'scripts/v5_resource_guard.py'),'--directory',str(guard),'--run-id','toy-monitor-guard','--interval','.1','--process-metrics','--',sys.executable,'-B','-c',code],stdout=f,stderr=subprocess.STDOUT,check=True)
    guard_exit=json.loads((guard/'router_exit.json').read_text())
    diffs['linux_guard_clean_exit']=guard_exit['router_returncode']==0 and guard_exit['termination_reason'] is None
    result={'status':'PASS' if all(diffs.values()) and actual['status']=='ROUTED' else 'FAIL','fixture_nets':1,
            'fixture_source_def_sha256':sha256(source),'fixture_input_sha256':hashes,'linux_memory_before':memory,
            'control_directory':str(control),'monitored_directory':str(out),'status_actual':actual['status'],
            'guide_sha256':actual.get('guide_sha256'),'trace_verification':actual.get('trace_verification'),
            'differences':diffs,'running_heartbeat_count':len(live),
            'heartbeat_intervals_seconds':[b['elapsed_seconds']-x['elapsed_seconds'] for x,b in zip(live,live[1:])],
            'observed_stages':sorted({r['stage'] for r in events}),'guard_exit':guard_exit}
    atomic_json(a.output/'equivalence.json',result)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
