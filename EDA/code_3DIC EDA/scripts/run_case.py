#!/usr/bin/env python3
import argparse,json,os,sys,time,uuid,math
from pathlib import Path
from common import *
from diagnostics import atomic_json,progress_tail,json_file,classify,utc
from router_versions import identify_router,display_path

def optional_hash(path):
    try:return sha256(path)
    except OSError:return None

def main():
    p=argparse.ArgumentParser(description='Run C++ router with persistent local progress and host diagnostics.')
    p.add_argument('--case',choices=sorted(catalog()));p.add_argument('--def',dest='def_file',type=Path);p.add_argument('--lef',type=Path);p.add_argument('--thermal-report',type=Path)
    p.add_argument('--output',type=Path);p.add_argument('--label',default='default');p.add_argument('--backend',choices=['native','wsl'],default='native');p.add_argument('--router',type=Path)
    p.add_argument('--trace',action='store_true');p.add_argument('--config',type=Path,default=PROJECT/'configs/default.json');p.add_argument('--time-limit',type=float,default=600);p.add_argument('--list',action='store_true')
    p.add_argument('--progress-every',type=int,default=1000);p.add_argument('--monitor-interval',type=float,default=5)
    p.add_argument('--heartbeat-interval',type=float,default=10);p.add_argument('--stall-after',type=float,default=30)
    p.add_argument('--no-monitor',action='store_true',help='Disable new monitoring for an explicit overhead comparison')
    a=p.parse_args()
    if a.list:print(json.dumps(catalog(),ensure_ascii=False,indent=2));return 0
    if a.progress_every<=0 or any(not math.isfinite(x) or x<=0 for x in [a.time_limit,a.monitor_interval,a.heartbeat_interval,a.stall_after]):p.error('Budgets and monitoring intervals must be finite and positive')
    c=catalog().get(a.case,{})
    paths=[a.def_file or c.get('def'),a.lef or c.get('lef'),a.thermal_report or c.get('thermal')]
    if any(x is None for x in paths):p.error('Choose --case or supply --def, --lef, --thermal-report')
    paths=[Path(x).resolve() for x in paths]
    if any(not x.is_file() for x in paths):p.error('Missing input')
    case=a.case or 'custom'
    try:target=run_dir(case,a.label)
    except ValueError as e:p.error(str(e))
    output=(a.output or target/'route.guide').resolve()
    if not output.is_relative_to(OUTPUTS.resolve()):p.error('Local helper outputs must be under project outputs/')
    if target.exists() or output.exists():p.error('Run/output already exists; use a new --label (partial logs are history too)')
    config=a.config.resolve();cfg=json.loads(config.read_text(encoding='utf8'))
    exe=(a.router or (PROJECT/'router' if a.backend=='wsl' or os.name!='nt' else OUTPUTS/'router.exe')).resolve()
    binary_hash=optional_hash(exe);version=identify_router(binary_hash)
    conv=linux_path if a.backend=='wsl' else lambda x:str(Path(x).resolve())
    args=[conv(exe),'--def',conv(paths[0]),'--lef',conv(paths[1]),'--thermal-report',conv(paths[2]),'--output',conv(output),'--metrics',conv(target/'result.json'),'--config',conv(config),'--time-limit',str(a.time_limit)]
    if a.trace:args+=['--trace',conv(target/'trace.jsonl')]
    monitored=not a.no_monitor;linux_supervised=monitored and (a.backend=='wsl' or os.name!='nt')
    run_id=uuid.uuid4().hex
    if monitored:args+=['--progress-dir',conv(target),'--progress-every',str(a.progress_every),'--monitor-interval',str(a.monitor_interval),'--heartbeat-interval',str(a.heartbeat_interval)]
    if linux_supervised:
        args=['python3',conv(PROJECT/'scripts/linux_supervisor.py'),'--directory',conv(target),'--run-id',run_id,'--interval',str(a.monitor_interval),'--lease-seconds',str(max(60,a.stall_after*2,a.monitor_interval*3)),'--',*args]
    cmd=backend_command(args,a.backend,time_file=target/'resource_usage.txt',timeout=a.time_limit+10)
    target.parent.mkdir(parents=True,exist_ok=True)
    try:target.mkdir(exist_ok=False)
    except FileExistsError:p.error('Label was reserved concurrently; use a new --label')
    result={'schema_version':1,'run_id':run_id,'case':case,'dataset':c.get('dataset','custom'),'label':a.label,'started_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'backend':a.backend,
            'inputs':dict(zip(['def','lef','thermal'],map(str,paths))),'input_sha256':dict(zip(['def','lef','thermal'],map(sha256,paths))),'config':cfg,'guide':str(output),'trace':str(target/'trace.jsonl') if a.trace else None,
            'peak_memory_bytes':None,'official_status':'NOT_EVALUATED','command':cmd,'router_sha256':binary_hash,
            **version,'router_binary':display_path(exe),'config_path':display_path(config),
            'config_overrides':{'time_limit':a.time_limit},'effective_time_limit_seconds':a.time_limit,
            'source_sha256':{str(f.relative_to(PROJECT)):sha256(f) for folder in ['src','include'] for f in (PROJECT/folder).glob('*') if f.is_file()},
            'status':'RUNNING','process_status':'RUNNING','returncode':None,'runtime_seconds':None,
            'monitoring':{'enabled':monitored,'resource_mode':'linux_supervisor' if linux_supervised else 'native_host' if monitored else 'disabled','sample_interval_seconds':a.monitor_interval,'heartbeat_seconds':a.heartbeat_interval,'progress_every':a.progress_every,'stall_after_seconds':a.stall_after},
            'failure_category':None,'failure_evidence':[]}
    atomic_json(target/'run.json',result)
    # Identity is determined from the selected bytes, never a filename. Persist
    # it before launching so failures/timeouts keep the same provenance.
    for key,value in [('Router Version',version['router_version'].upper()),('Router Binary',result['router_binary']),
                      ('Router SHA256',binary_hash or 'UNAVAILABLE'),('Version Status',version['router_version_status']),
                      ('Version Detail',version['router_version_reason']),('Config File',result['config_path']),
                      ('Config Params',json.dumps(cfg,ensure_ascii=False,sort_keys=True)),
                      ('CLI Overrides',json.dumps(result['config_overrides'])),
                      ('Case',case),('Label',a.label),('Results',display_path(target))]:
        print(f'{key:15}: {value}',flush=True)
    if c.get('warning'):print(c['warning'],file=sys.stderr)
    env=os.environ.copy()
    if monitored and not linux_supervised:env['GR_EXTERNAL_RESOURCE_MONITOR']='1'
    try:
        execution=timed_process(cmd,target/'router.log',a.time_limit+45,env=env,monitor_dir=target if monitored else None,monitor_interval=a.monitor_interval,stall_after=a.stall_after,native_resources=monitored and not linux_supervised)
    except Exception as e:
        execution={'process_status':'LAUNCH_ERROR','returncode':None,'runtime_seconds':None,'host_exception':str(e)}
    result.update(execution)
    # A timeout wrapper may return before its supervised child finishes flushing.
    if linux_supervised and not (target/'router_exit.json').exists() and (target/'router_process.json').exists():
        deadline=time.monotonic()+(6 if result['returncode'] in [124,137] else 1)
        while time.monotonic()<deadline and not (target/'router_exit.json').exists():time.sleep(.1)
    progress=progress_tail(target/'progress.jsonl');last=progress['error'] or progress['last'];supervisor=json_file(target/'router_exit.json')
    result['peak_memory_bytes']=peak_rss(target/'resource_usage.txt')
    result['peak_memory_source']='gnu_time_command_tree' if result['peak_memory_bytes'] is not None else 'unavailable'
    result['router_peak_rss_bytes']=supervisor.get('resource_summary',{}).get('observed_router_peak_rss_bytes')
    success=result['process_status']=='FINISHED' and result['returncode']==0 and output.is_file() and (target/'result.json').is_file()
    result['status']='ROUTED' if success else 'FAIL'
    result['failure_category'],result['failure_evidence']=classify(result,progress,supervisor,run_id,a.backend,success)
    result.update(last_phase=last.get('phase'),last_net_index=last.get('net_index'),last_net=last.get('net'),last_progress_seconds=last.get('elapsed_seconds'),
                  finished_at=utc(),supervisor_exit=supervisor or None,router_log_encoding='raw bytes; WSL UTF-16LE and Router UTF-8 may coexist; no text matching used for classification')
    if not success:result['failure_reason']=progress['error'].get('message') or ('%s; no authoritative Router error message. Inspect host_monitor.jsonl, progress.jsonl, router_exit.json and raw router.log.'%result['failure_category'])
    atomic_json(target/'run.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if success else 1
if __name__=='__main__':raise SystemExit(main())
