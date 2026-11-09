"""V5-only memory safety and descendant-aware Linux supervisor (no routing logic)."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from diagnostics import atomic_json, json_file, utc, windows_memory, ResourceMonitor, _numbers

MIB=2**20

def host_pressure(mem):
    if not mem:return 'HOST_MEMORY_UNAVAILABLE'
    if mem.get('commit_available_bytes',0)<512*MIB:return 'CRITICAL_WINDOWS_COMMIT'
    if mem.get('physical_available_bytes',0)<128*MIB:return 'CRITICAL_WINDOWS_RAM'
    return None

def linux_pressure(mem):
    available=mem.get('mem_available_bytes');swap=mem.get('swap_free_bytes')
    if available is None or swap is None:return 'LINUX_MEMORY_UNAVAILABLE'
    if available<256*MIB and swap<512*MIB:return 'CRITICAL_LINUX_RAM_SWAP'
    limit=mem.get('cgroup_limit_bytes');used=mem.get('cgroup_current_bytes')
    if isinstance(limit,int) and isinstance(used,int) and limit-used<256*MIB:return 'CRITICAL_CGROUP_MEMORY'
    return None

class HostGuard:
    def __init__(self,directory):
        self.directory=Path(directory);self.stop=threading.Event();self.thread=None
    def start(self):
        if os.name=='nt':
            self.thread=threading.Thread(target=self.watch,daemon=True);self.thread.start()
        return self
    def watch(self):
        low=0
        try:
            with (self.directory/'v5_memory_guard.jsonl').open('x',encoding='utf8') as log:
                while not self.stop.is_set():
                    mem=windows_memory();reason=host_pressure(mem)
                    low=low+1 if mem and mem['commit_available_bytes']<1024*MIB else 0
                    if low>=3:reason=reason or 'SUSTAINED_LOW_WINDOWS_COMMIT'
                    row={'timestamp_utc':utc(),'windows':mem,'reason':reason,'low_samples':low}
                    log.write(json.dumps(row)+'\n');log.flush()
                    if reason:
                        atomic_json(self.directory/'v5_safety_stop.json',row)
                        atomic_json(self.directory/'host_cancel.json',{'reason':reason,'timestamp_utc':utc()})
                        return
                    self.stop.wait(2)
        except Exception as error:
            atomic_json(self.directory/'host_cancel.json',{'reason':'MEMORY_GUARD_ERROR','error':repr(error)})
    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=5)

def identity(pid):
    try:
        fields=Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()
        return fields[19] # starttime, field 22, protects against PID reuse
    except (OSError,IndexError):return None

def descendants(pid):
    pending=[pid];found={}
    while pending:
        p=pending.pop()
        if p in found:continue
        stamp=identity(p)
        if stamp is None:continue
        found[p]=stamp
        try:pending.extend(int(v) for v in Path('/proc',str(p),'task',str(p),'children').read_text().split())
        except OSError:pass
    return found

def signal_known(known,sig):
    for pid,stamp in reversed(list(known.items())):
        if identity(pid)==stamp:
            try:os.kill(pid,sig)
            except ProcessLookupError:pass

def main():
    import resource
    p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,required=True);p.add_argument('--run-id',required=True)
    p.add_argument('--interval',type=float,default=2);p.add_argument('--lease-seconds',type=float,default=120)
    p.add_argument('--process-metrics',action='store_true',help='Observe descendant CPU/RSS; no change to safety thresholds')
    p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args();cmd=a.command[1:] if a.command[:1]==['--'] else a.command
    if not cmd:p.error('Missing command')
    # Adopt orphaned nested sessions so cancellation also reaps original stage children.
    if ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0)!=0:raise OSError('Cannot establish child subreaper')
    record={'run_id':a.run_id,'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'started_at':utc(),'command':cmd}
    start=time.monotonic();proc=None;monitor=None;known={};reason=None;cancel_at=None;low=0;code=None
    def cancel(why):
        nonlocal reason,cancel_at
        if reason is None:reason=why;cancel_at=time.monotonic()
    signal.signal(signal.SIGTERM,lambda *_:cancel('WRAPPER_SIGTERM'))
    signal.signal(signal.SIGINT,lambda *_:cancel('WRAPPER_SIGINT'))
    try:
        proc=subprocess.Popen(cmd,start_new_session=True);record['router_pid']=proc.pid;record['supervisor_pid']=os.getpid()
        atomic_json(a.directory/'router_process.json',record)
        options={'process_metrics_enabled':True} if a.process_metrics else {}
        monitor=ResourceMonitor(a.directory/'resource_monitor.csv',proc.pid,role='evaluator',**options)
        next_sample=0
        while True:
            known.update(descendants(os.getpid()));known.pop(os.getpid(),None)
            now=time.monotonic()
            if now>=next_sample:
                row=monitor.sample();next_sample=now+a.interval
                pressure=linux_pressure(row)
                low=low+1 if row.get('mem_available_bytes',0)<512*MIB and row.get('swap_free_bytes',0)<1024*MIB else 0
                if pressure or low>=3:
                    cancel(pressure or 'SUSTAINED_LOW_LINUX_MEMORY')
                    atomic_json(a.directory/'v5_linux_safety_stop.json',{'reason':reason,'timestamp_utc':utc(),'sample':row})
            if (a.directory/'host_cancel.json').exists():cancel(json_file(a.directory/'host_cancel.json').get('reason','HOST_CANCEL'))
            try:age=time.time()-(a.directory/'host_heartbeat.json').stat().st_mtime
            except OSError:age=now-start
            if age>a.lease_seconds:cancel('HOST_LEASE_EXPIRED')
            if reason:signal_known(known,signal.SIGKILL if now-cancel_at>=3 else signal.SIGTERM)
            code=proc.poll()
            if code is not None:
                alive={pid:stamp for pid,stamp in known.items() if identity(pid)==stamp}
                # Direct child is reaped by Popen; reap only adopted descendants now.
                while True:
                    try:
                        if os.waitpid(-1,os.WNOHANG)[0]==0:break
                    except ChildProcessError:break
                alive={pid:stamp for pid,stamp in alive.items() if identity(pid)==stamp}
                if not alive:break
                cancel(reason or 'ORPHAN_CHILD_CLEANUP')
            time.sleep(.2)
    except Exception as error:
        record['supervisor_error']=repr(error);cancel('SUPERVISOR_ERROR')
    finally:
        known.update(descendants(os.getpid()));known.pop(os.getpid(),None)
        if proc and proc.poll() is None:
            signal_known(known,signal.SIGTERM)
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:signal_known(known,signal.SIGKILL);proc.wait(timeout=5)
        signal_known(known,signal.SIGKILL)
        while True:
            try:os.waitpid(-1,0)
            except ChildProcessError:break
        if monitor:record['resource_summary']=monitor.finish()
        code=proc.returncode if proc else None
        record.update(router_returncode=code,router_signal=-code if code is not None and code<0 else None,
                      termination_reason=reason,runtime_seconds=time.monotonic()-start,finished_at=utc(),
                      child_peak_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*1024,
                      child_peak_rss_source='Linux RUSAGE_CHILDREN high-water max; sampled descendant RSS sum separately in resource_summary')
        atomic_json(a.directory/'router_exit.json',record)
    return 2 if reason else (code if code is not None and code>=0 else 128-code if code is not None else 127)

if __name__=='__main__':raise SystemExit(main())
