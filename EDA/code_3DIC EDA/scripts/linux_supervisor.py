#!/usr/bin/env python3
"""WSL-side local supervisor. Never used by the four-argument competition CLI."""
from pathlib import Path
import argparse,os,signal,subprocess,time,sys,tempfile,re,resource,math
from diagnostics import atomic_json,json_file,utc,ResourceMonitor

def kernel_oom(pid,started_uptime,directory=None):
    """Only a timestamped kernel record naming this victim PID confirms OOM."""
    result={'oom_confirmed_for_pid':False,'kernel_oom_evidence':[],'kernel_oom_check':'unavailable'}
    try:
        with tempfile.TemporaryFile(dir=directory) as output:
            run=subprocess.run(['dmesg','--color=never'],stdout=output,stderr=subprocess.PIPE,timeout=1)
            if run.returncode:return result
            output.seek(0,2);output.seek(max(0,output.tell()-1048576));lines=output.read().decode('utf8',errors='replace').splitlines()
        result['kernel_oom_check']='read_last_1MiB'
        for line in lines:
            stamp=re.search(r'^\[\s*(\d+(?:\.\d+)?)\]',line)
            victim=re.search(r'\bKilled process\s+(\d+)\b',line)
            if stamp and victim and int(victim[1])==pid and float(stamp[1])>=started_uptime and ('oom' in line.lower() or 'Out of memory' in line):
                result['oom_confirmed_for_pid']=True;result['kernel_oom_evidence'].append(line)
    except (OSError,ValueError,subprocess.TimeoutExpired):pass
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--run-id',required=True)
    parser.add_argument('--interval',type=float,default=5);parser.add_argument('--lease-seconds',type=float,default=60)
    parser.add_argument('--process-kind',choices=['router','evaluator'],default='router')
    parser.add_argument('--timeout',type=float,help='Child runtime budget; supervisor reaps before exiting')
    parser.add_argument('command',nargs=argparse.REMAINDER);args=parser.parse_args()
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    if not command or args.interval<=0 or args.lease_seconds<=0:parser.error('Invalid command/interval')
    if args.timeout is not None and (not math.isfinite(args.timeout) or args.timeout<=0):parser.error('Invalid timeout')
    directory=args.directory;started=time.monotonic()
    try:uptime=float(Path('/proc/uptime').read_text().split()[0])
    except (OSError,ValueError):uptime=float('inf')
    kind=args.process_kind
    process_file=directory/(kind+'_process.json');exit_file=directory/(kind+'_exit.json')
    record={'run_id':args.run_id,'process_kind':kind,'supervisor_pid':os.getpid(),'started_at':utc(),kind+'_returncode':None,kind+'_signal':None}
    try:record['boot_id']=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:record['boot_id']=None
    proc=None;monitor=None;requested=None;reason=None;deadline=None;monitor_errors=[]
    def forward(sig,why):
        nonlocal requested,reason,deadline
        if requested is not None:return
        # Leave time to reap and flush before GNU timeout's 5-second kill-after.
        requested=sig;reason=why;deadline=time.monotonic()+2
        if proc and proc.poll() is None:
            try:os.killpg(proc.pid,sig)
            except ProcessLookupError:pass
    signal.signal(signal.SIGTERM,lambda sig,frame:forward(sig,'wrapper_signal'))
    signal.signal(signal.SIGINT,lambda sig,frame:forward(sig,'wrapper_signal'))
    try:
        env=os.environ.copy();env['GR_EXTERNAL_RESOURCE_MONITOR']='1';env['PYTHONDONTWRITEBYTECODE']='1'
        if kind=='evaluator':env['PYTHONUNBUFFERED']='1'
        proc=subprocess.Popen(command,start_new_session=True,env=env)
        child_started=time.monotonic()
        record[kind+'_pid']=proc.pid
        record['command']=command
        if requested is not None:
            try:os.killpg(proc.pid,requested)
            except ProcessLookupError:pass
        try:atomic_json(process_file,{**record,'state':'RUNNING','note':'PID exists; not proof of forward algorithm progress'})
        except OSError as e:monitor_errors.append(str(e))
        try:monitor=ResourceMonitor(directory/'resource_monitor.csv',proc.pid,role=kind) if kind!='router' else ResourceMonitor(directory/'resource_monitor.csv',proc.pid)
        except OSError as e:monitor_errors.append(str(e))
        next_sample=0
        while True:
            now=time.monotonic()
            if monitor and now>=next_sample:
                try:monitor.sample()
                except OSError as e:
                    monitor_errors.append(str(e))
                    try:monitor.finish()
                    except OSError:pass
                    monitor=None
                next_sample=now+args.interval
            code=proc.poll()
            if code is not None:break
            if args.timeout is not None and now-child_started>=args.timeout:forward(signal.SIGTERM,'time_budget')
            if (directory/'host_cancel.json').exists():forward(signal.SIGTERM,'host_cancel')
            try:lease_age=time.time()-(directory/'host_heartbeat.json').stat().st_mtime
            except OSError:lease_age=time.monotonic()-started
            if lease_age>args.lease_seconds:forward(signal.SIGTERM,'host_lease_expired')
            if deadline and now>deadline:
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                deadline=None
            time.sleep(.2)
        record.update({kind+'_returncode':code,kind+'_signal':-code if code<0 else None})
        record['child_runtime_seconds']=time.monotonic()-child_started
        record.update(termination_requested_signal=requested,termination_reason=reason)
        if monitor:
            try:record['resource_summary']=monitor.finish()
            except OSError as e:monitor_errors.append(str(e))
            monitor=None
        # Preserve waitpid evidence even if a subsequent kernel probe is cut off.
        record['child_peak_rss_bytes']=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*1024
        record['child_peak_rss_source']='Linux wait4/getrusage RUSAGE_CHILDREN; child and waited descendants, not simultaneous tree RSS'
        try:atomic_json(exit_file,record)
        except OSError as e:monitor_errors.append(str(e))
        if code==-signal.SIGKILL:record.update(kernel_oom(proc.pid,uptime,directory))
    except Exception as e:
        record['launch_error' if proc is None else 'supervisor_error']=str(e)
    finally:
        try:
            if proc and proc.poll() is None:
                forward(signal.SIGTERM,'supervisor_cleanup')
                try:proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    proc.wait(timeout=5)
        except Exception as e:monitor_errors.append('cleanup: '+str(e))
        # Cleanup can be the first place that observes waitpid (for example if
        # sampling raised). Preserve that actual code instead of leaving null.
        if proc and proc.returncode is not None:
            code=proc.returncode
            record.update({kind+'_returncode':code,kind+'_signal':-code if code<0 else None})
            record.setdefault('child_runtime_seconds',time.monotonic()-child_started)
            record.update(termination_requested_signal=requested,termination_reason=reason)
            if 'child_peak_rss_bytes' not in record:
                record['child_peak_rss_bytes']=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*1024
        if monitor:
            try:record['resource_summary']=monitor.finish()
            except Exception as e:monitor_errors.append('resource finalization: '+str(e))
        record.update(finished_at=utc(),runtime_seconds=time.monotonic()-started,monitor_errors=monitor_errors[:10])
        try:atomic_json(exit_file,record)
        except OSError as e:print('MONITOR_UNAVAILABLE: '+str(e),file=sys.stderr,flush=True)
    code=record.get(kind+'_returncode')
    return (128-code if code is not None and code<0 else code) if code is not None else 127
if __name__=='__main__':raise SystemExit(main())
