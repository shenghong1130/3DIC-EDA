#!/usr/bin/env python3
"""Supervised official scoring; immutable routing records and exclusive attempts."""
import argparse,json,math,os,shutil,time,uuid
from pathlib import Path
from common import EDA,PROJECT,run_dir,safe_label,sha256,linux_path,backend_command,timed_process,peak_rss,existing_run_dir,load_run
from diagnostics import atomic_json,json_file,utc
from evaluator_diagnostics import log_tail,read_report,classify_evaluator,wsl_snapshot,windows_events,resource_tail


def execute(a,run,directory):
    conv=linux_path if a.backend=='wsl' else str
    tool_dir=EDA/'\u8bc4\u4f30\u7a0b\u5e8f' if a.engine=='latest' else EDA/'eda-elite-challenge/eda-elite-challenge2026/evaluator'
    tool=tool_dir/('score' if a.engine=='latest' else 'score.py')
    run_id=uuid.uuid4().hex
    result={'status':'ERROR','official_status':'NOT_EVALUATED','process_status':'PREPARING',
            'returncode':None,'engine':a.engine,'run_id':run_id,'started_at':utc(),
            'case':a.case,'label':a.label,'attempt':a.attempt,'report_directory':str(directory),
            'report':{},'failure_category':'UNKNOWN',
            'returncode_layer':'wsl.exe' if a.backend=='wsl' else 'GNU time/supervisor',
            'process_status_note':'FINISHED means host child exited, not evaluator success',
            'fidelity':'Official provided evaluator; GRT guide metrics / mock-DRT proxy; no detailed-routing signoff.'}
    atomic_json(directory/'invocation.json',result)
    start=time.monotonic()
    try:
        result['evaluator_sha256']=sha256(tool)
        result['guide_sha256']=sha256(Path(run['guide']))
        # Official caches must only be written beside byte-identical input copies.
        input_dir=directory/'inputs';input_dir.mkdir()
        copies={}
        for key,value in run['inputs'].items():
            source=Path(value);dest=input_dir/source.name
            if dest.exists():raise ValueError('Input basename collision: '+source.name)
            shutil.copyfile(source,dest);copies[key]=dest
        result['evaluated_input_sha256']={key:sha256(path) for key,path in copies.items()}
        if result['evaluated_input_sha256']!=run['input_sha256']:raise ValueError('Input changed during copying')
        if a.engine=='latest':
            command=[conv(tool)];env={}
            for name in ('POST9.dat','POWV9.dat'):shutil.copyfile(tool_dir/name,directory/name)
        else:
            command=['python3',conv(tool)]
            env={'LD_LIBRARY_PATH':conv(tool_dir),'PYTHONPATH':conv(tool_dir),'PYTHONDONTWRITEBYTECODE':'1'}
        command += [conv(Path(run['guide'])),'--lefs',conv(copies['lef']),
                    '--input-def',conv(copies['def']),'--thermal-json',conv(copies['thermal']),
                    '--output-dir',conv(directory)]
        supervised=['python3',conv(PROJECT/'scripts/linux_supervisor.py'),
                    '--directory',conv(directory),'--run-id',run_id,'--process-kind','evaluator',
                    '--interval',str(a.monitor_interval),'--lease-seconds',str(max(60,a.monitor_interval*4)),
                    '--timeout',str(a.timeout),
                    '--',*command]
        # timeout(time(supervisor)) can kill time before it flushes, and can
        # return before the supervisor reaps. Enforce budget inside supervisor.
        cmd=backend_command(supervised,a.backend,cwd=directory,time_file=directory/'resource_usage.txt',env=env)
        result.update(command=cmd,evaluator_command=command,timeout_seconds=a.timeout,
                      monitor_interval_seconds=a.monitor_interval,process_status='LAUNCHING')
        atomic_json(directory/'invocation.json',result)
        native_env=os.environ.copy();native_env.update(env)
        result.update(timed_process(cmd,directory/'evaluator.log',a.timeout+30,
                      cwd=directory if a.backend=='native' else None,
                      env=native_env if a.backend=='native' else None,
                      monitor_dir=directory,monitor_interval=a.monitor_interval,process_role='evaluator'))
        result['host_process_status']=result['process_status']
        # Preserve wsl.exe's real wait result BEFORE any post-exit WSL/Event
        # probe. If the host dies during diagnostics, LAUNCHING is not left as
        # the only on-disk evidence of a process that already exited.
        result['diagnostics_pending']=True
        atomic_json(directory/'invocation.json',result)
        end=time.monotonic()+1
        while not (directory/'evaluator_exit.json').exists() and time.monotonic()<end:time.sleep(.1)
        supervisor=json_file(directory/'evaluator_exit.json')
        process=json_file(directory/'evaluator_process.json')
        if not isinstance(supervisor,dict):supervisor={}
        if not isinstance(process,dict):process={}
        result.update(supervisor=supervisor,evaluator_process=process)
        report,state=read_report(directory/'evaluation_report.json')
        result.update(report=report,report_state=state)
        category,evidence=classify_evaluator(result,supervisor,run_id,report,state,a.backend)
        result.update(failure_category=category,evidence=evidence)
        atomic_json(directory/'invocation.json',result)
        if category!='SUCCESS' and a.backend=='wsl':
            result['wsl_after']=wsl_snapshot()
            category,evidence=classify_evaluator(result,supervisor or process,run_id,report,state,a.backend,result['wsl_after'])
        if category!='SUCCESS':result['windows_events']=windows_events(result['started_at'])
        result.update(failure_category=category,evidence=evidence,
                      wsl_returncode_uint32=(result['returncode']&0xffffffff) if a.backend=='wsl' and isinstance(result['returncode'],int) else None,
                      evaluator_pid=supervisor.get('evaluator_pid',process.get('evaluator_pid')),
                      evaluator_runtime_seconds=supervisor.get('child_runtime_seconds'),
                      evaluator_returncode=supervisor.get('evaluator_returncode'),
                      evaluator_signal=supervisor.get('evaluator_signal'),
                      peak_memory_bytes=supervisor.get('child_peak_rss_bytes'),
                      wrapper_peak_memory_bytes=peak_rss(directory/'resource_usage.txt'))
        if category in ('TIMEOUT','INTERRUPTED'):result['process_status']=category
        result['evaluator_process_status']=('SIGNALED' if supervisor.get('evaluator_signal') else
                                            'EXITED' if supervisor.get('evaluator_returncode') is not None else 'UNKNOWN')
        if result['peak_memory_bytes'] is None:
            result['peak_memory_bytes']=supervisor.get('resource_summary',{}).get('observed_evaluator_peak_rss_bytes')
            result['resource_tail']=resource_tail(directory/'resource_monitor.csv')
            if result['peak_memory_bytes'] is None:result['peak_memory_bytes']=result['resource_tail'].get('observed_peak_rss_bytes')
            result['peak_memory_source']='sampled /proc evaluator PID (lower bound)' if result['peak_memory_bytes'] is not None else 'unavailable'
        else:result['peak_memory_source']='Linux child wait4/getrusage high-water mark; not host RAM'
        if category=='SUCCESS':result.update(status='PASS',official_status='PASS')
        elif category=='OFFICIAL_FAIL':result.update(status='FAIL',official_status='FAIL')
        if result['status']=='ERROR':result['failure_reason']='Evaluator invocation incomplete: '+category
        if category!='SUCCESS':result['log_tail']=log_tail(directory/'evaluator.log')
    except KeyboardInterrupt:
        result.update(status='ERROR',official_status='NOT_EVALUATED',process_status='INTERRUPTED',failure_category='INTERRUPTED',failure_reason='Evaluation setup/finalization interrupted')
    except (OSError,ValueError,KeyError) as e:
        result.update(status='ERROR',official_status='NOT_EVALUATED',failure_category='SETUP_OR_REPORT_ERROR',failure_reason=str(e))
    finally:
        result.update(finished_at=utc(),total_runtime_seconds=time.monotonic()-start,diagnostics_pending=False)
        atomic_json(directory/'invocation.json',result)
    return result


def evaluation_run(case,label):
    """Read-only preflight, also usable without launching an evaluator."""
    target=existing_run_dir(case,label)
    if not (target/'run.json').is_file():raise ValueError('Run not found')
    run=load_run(target)
    if run.get('status')!='ROUTED' or run.get('returncode',0)!=0:
        raise ValueError('Router failed; no complete guide to evaluate')
    if not Path(run['guide']).is_file():raise ValueError('Guide not found')
    for key,value in run['inputs'].items():
        if sha256(Path(value))!=run['input_sha256'][key]:raise ValueError('Input changed since routing: '+key)
    return target,run


def main(argv=None):
    p=argparse.ArgumentParser(description='Run unmodified official evaluators. Latest ELF is recommended.')
    p.add_argument('--case',required=True);p.add_argument('--label',default='default')
    p.add_argument('--engine',choices=['latest','legacy'],default='latest')
    p.add_argument('--backend',choices=['native','wsl'],default='wsl' if os.name=='nt' else 'native')
    p.add_argument('--timeout',type=float,default=600)
    p.add_argument('--monitor-interval',type=float,default=5,help='Linux PID sampling and host heartbeat seconds')
    p.add_argument('--attempt',help='Independent scoring attempt; never overwrites official_<engine> or run.json')
    a=p.parse_args(argv)
    if not all(math.isfinite(x) and x>0 for x in (a.timeout,a.monitor_interval)):p.error('Budgets must be finite and positive')
    if os.name=='nt' and a.backend=='native':p.error('Official ELF requires WSL/Linux. Use --backend wsl.')
    try:
        target,run=evaluation_run(a.case,a.label)
        directory=(target/('official_'+a.engine+'_attempts')/safe_label(a.attempt)) if a.attempt else target/('official_'+a.engine)
        if directory.exists():p.error('Evaluation already exists; preserve it and choose a new --attempt name')
        directory.parent.mkdir(parents=True,exist_ok=True)
        directory.mkdir()
    except (OSError,ValueError,KeyError) as e:p.error(str(e))
    result=execute(a,run,directory)
    # Read-only run.json; consumers obtain the verdict from invocation.json.
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
