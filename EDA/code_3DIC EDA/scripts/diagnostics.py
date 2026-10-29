"""Bounded, evidence-based diagnostics; standard library only."""
from pathlib import Path
import csv,datetime,json,os,sys,time

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def atomic_json(path,data):
    path=Path(path);temp=path.with_name(path.name+'.tmp')
    with temp.open('w',encoding='utf8',newline='\n') as f:
        json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n');f.flush()
    os.replace(temp,path)

def json_file(path):
    try:return json.loads(Path(path).read_text(encoding='utf8'))
    except (OSError,ValueError):return {}

def progress_tail(path,limit=131072):
    """Skip a partial first/last record; never load the entire log."""
    try:
        with Path(path).open('rb') as f:
            f.seek(0,2);size=f.tell();offset=max(0,size-limit);f.seek(offset);data=f.read(limit)
        lines=data.splitlines()
        if offset:lines=lines[1:]
        records=[]
        for line in lines:
            try:
                item=json.loads(line)
                if isinstance(item,dict):records.append(item)
            except (ValueError,UnicodeError):continue
        return {'last':records[-1] if records else {},'error':next((r for r in reversed(records) if r.get('event')=='error'),{})}
    except OSError:return {'last':{},'error':{}}

def _numbers(path):
    result={}
    try:
        for line in Path(path).read_text(encoding='ascii').splitlines():
            fields=line.replace(':',' ').split()
            if len(fields)>1 and fields[1].isdigit():result[fields[0]]=int(fields[1])*(1024 if len(fields)>2 and fields[2]=='kB' else 1)
    except OSError:pass
    return result

def cgroup_dir(pid):
    try:
        for line in Path('/proc',str(pid),'cgroup').read_text().splitlines():
            if line.startswith('0::'):
                root=Path('/sys/fs/cgroup');target=(root/line[3:].lstrip('/')).resolve()
                if target.is_relative_to(root):return target
    except OSError:pass
    return None

def cgroup_snapshot(directory):
    out={'cgroup_current_bytes':None,'cgroup_limit_bytes':None,'cgroup_limit_unlimited':None,'cgroup_oom_kill':None}
    if directory is None:return out
    for name,key in [('memory.current','cgroup_current_bytes'),('memory.max','cgroup_limit_bytes')]:
        try:
            text=(directory/name).read_text().strip()
            if name=='memory.max':out['cgroup_limit_unlimited']=text=='max'
            if text!='max':out[key]=int(text)
        except (OSError,ValueError):pass
    out['cgroup_oom_kill']=_numbers(directory/'memory.events').get('oom_kill')
    return out

RESOURCE_FIELDS=['timestamp_utc','elapsed_seconds','pid','process_state','rss_bytes','router_peak_rss_bytes','process_peak_rss_bytes','mem_available_bytes','swap_free_bytes','cgroup_current_bytes','cgroup_limit_bytes','cgroup_limit_unlimited','cgroup_oom_kill','source']

class ResourceMonitor:
    def __init__(self,path,pid,role='router'):
        self.pid=pid;self.role=role;self.start=time.monotonic();self.directory=cgroup_dir(pid) if sys.platform.startswith('linux') else None
        self.initial=cgroup_snapshot(self.directory);self.last={};self.peak=None
        self.file=Path(path).open('x',encoding='utf8',newline='')
        self.writer=csv.DictWriter(self.file,fieldnames=RESOURCE_FIELDS);self.writer.writeheader();self.file.flush()
    def sample(self):
        row={key:None for key in RESOURCE_FIELDS}
        row.update(timestamp_utc=utc(),elapsed_seconds=time.monotonic()-self.start,pid=self.pid,source='proc_'+self.role+'_pid' if sys.platform.startswith('linux') else 'linux_metrics_unsupported_on_host')
        if sys.platform.startswith('linux'):
            status=_numbers(Path('/proc',str(self.pid),'status'));mem=_numbers('/proc/meminfo')
            row.update(rss_bytes=status.get('VmRSS'),router_peak_rss_bytes=status.get('VmHWM') if self.role=='router' else None,process_peak_rss_bytes=status.get('VmHWM'),mem_available_bytes=mem.get('MemAvailable'),swap_free_bytes=mem.get('SwapFree'))
            try:
                state=next(line for line in Path('/proc',str(self.pid),'status').read_text().splitlines() if line.startswith('State:'))
                row['process_state']=state.partition(':')[2].strip()
            except (OSError,StopIteration):pass
            row.update(cgroup_snapshot(self.directory))
        peak=row['process_peak_rss_bytes']
        if peak is not None:self.peak=max(self.peak or 0,peak)
        self.writer.writerow({k:('N/A' if v is None else v) for k,v in row.items()});self.file.flush();self.last=row
        return row
    def finish(self):
        final=cgroup_snapshot(self.directory)
        initial=self.initial.get('cgroup_oom_kill');end=final.get('cgroup_oom_kill')
        self.file.close()
        return {'observed_'+self.role+'_peak_rss_bytes':self.peak,'last_sample':self.last,'cgroup_oom_kill_initial':initial,'cgroup_oom_kill_final':end,'cgroup_oom_kill_delta':end-initial if end is not None and initial is not None else None,'cgroup_scope':'shared_or_unknown; counters do not identify victim PID'}

def classify(result,progress,supervisor,run_id,backend,success):
    evidence=[];error=progress.get('error',{})
    trusted=supervisor if supervisor.get('run_id')==run_id else {}
    if trusted:evidence.append({'source':'router_exit.json','router_returncode':trusted.get('router_returncode'),'router_signal':trusted.get('router_signal')})
    evidence.append({'source':'host','process_status':result.get('process_status'),'returncode':result.get('returncode')})
    if success:return 'SUCCESS',evidence
    if result.get('process_status')=='LAUNCH_ERROR':return 'LAUNCH_ERROR',evidence
    if result.get('process_status')=='TIMEOUT':
        evidence.append({'source':'host','observation':'Python deadline expired and cancellation was requested'});return 'TIMEOUT',evidence
    if error:
        evidence.append({'source':'progress.jsonl','phase':error.get('phase'),'net':error.get('net'),'error_kind':error.get('error_kind'),'message':error.get('message')})
        if error.get('error_kind')=='time_budget':return 'TIMEOUT',evidence
        if error.get('error_kind')=='allocation_failure':return 'OOM_SUSPECTED',evidence
        return 'ROUTER_ERROR',evidence
    if trusted.get('oom_confirmed_for_pid'):
        evidence.append({'source':'kernel','observation':trusted.get('kernel_oom_evidence')});return 'OOM_CONFIRMED',evidence
    signal=trusted.get('router_signal')
    if signal:
        if trusted.get('termination_reason') in ['wrapper_signal','host_cancel','host_lease_expired']:
            evidence.append({'source':'supervisor','termination_reason':trusted['termination_reason']})
            if trusted.get('termination_reason')=='host_lease_expired':return 'UNKNOWN',evidence
            if result.get('returncode')==124 and trusted.get('termination_requested_signal')==15:return 'TIMEOUT',evidence
        if signal==9 and (trusted.get('resource_summary',{}).get('cgroup_oom_kill_delta') or 0)>0:
            evidence.append({'source':'cgroup','observation':'oom_kill increased in a shared cgroup; victim attribution unavailable'});return 'OOM_SUSPECTED',evidence
        return 'ROUTER_SIGNAL',evidence
    if trusted.get('launch_error'):return 'LAUNCH_ERROR',evidence
    if trusted.get('router_returncode') not in [None,0]:return 'ROUTER_ERROR',evidence
    if backend=='native' and isinstance(result.get('returncode'),int) and result['returncode']<0 and os.name!='nt':
        evidence.append({'source':'native_waitpid','signal':-result['returncode']});return 'ROUTER_SIGNAL',evidence
    rc=result.get('returncode')
    if backend=='wsl' and (trusted.get('router_returncode')==0 and rc not in [None,0] or isinstance(rc,int) and (rc&0xffffffff)>=0x80000000):
        return 'WSL_EXIT_ERROR',evidence
    evidence.append({'source':'diagnostic_policy','observation':'No authoritative Router/WSL/OOM cause; raw router.log may contain mixed encodings and was not used for text classification'})
    return 'UNKNOWN',evidence

class HostMonitor:
    """Runs in the launching Python process. Never starts WSL probes."""
    def __init__(self,directory,interval=5,stall_after=30,lease_seconds=60,role='router'):
        self.directory=Path(directory);self.interval=interval;self.stall_after=stall_after;self.role=role
        self.start=time.monotonic();self.next=0;self.pid=None;self.errors=[];self.native_resources=None
        self.file=(self.directory/'host_monitor.jsonl').open('x',encoding='utf8',newline='\n')
        self.tick('LAUNCHING',force=True)
    def _error(self,e):
        if len(self.errors)<10:self.errors.append(str(e))
    def tick(self,state,force=False,returncode=None):
        now=time.monotonic()
        if not force and now<self.next:return
        self.next=now+self.interval
        try:
            path=self.directory/('evaluator.log' if self.role=='evaluator' else 'progress.jsonl')
            age=max(0,time.time()-path.stat().st_mtime) if path.exists() else None
            elapsed=now-self.start
            item={'timestamp_utc':utc(),'elapsed_seconds':elapsed,'subprocess_pid':self.pid,'subprocess_status':state,'returncode':returncode,
                  'last_progress_age_seconds':age,'progress_status':'NO_PROGRESS' if (age if age is not None else elapsed)>self.stall_after else 'RECENT' if age is not None else 'WAITING_FOR_ROUTER',
                  'router_liveness':'UNKNOWN_TO_HOST; recent file writes do not prove algorithm progress'}
            if self.role=='evaluator':
                item.pop('router_liveness')
                item.update(process_role='evaluator',activity_source='evaluator.log mtime; may be buffered',
                            evaluator_liveness='UNKNOWN_TO_HOST; log activity is not proof of forward progress')
                if item['progress_status']=='WAITING_FOR_ROUTER':item['progress_status']='WAITING_FOR_EVALUATOR'
            self.file.write(json.dumps(item,ensure_ascii=False)+'\n');self.file.flush()
            atomic_json(self.directory/'host_heartbeat.json',{'timestamp_utc':utc(),'elapsed_seconds':elapsed})
            if self.native_resources:self.native_resources.sample()
            print('[HOST] elapsed=%.1fs subprocess=%s last_progress=%s status=%s'%(elapsed,state,'N/A' if age is None else '%.1fs ago'%age,item['progress_status']),flush=True)
        except OSError as e:self._error(e)
    def cancel(self,reason):
        try:atomic_json(self.directory/'host_cancel.json',{'reason':reason,'timestamp_utc':utc()})
        except OSError as e:self._error(e)
    def close(self,state,code):
        self.tick(state,force=True,returncode=code)
        if self.native_resources:
            try:self.native_resources.finish()
            except OSError as e:self._error(e)
        self.file.close()
