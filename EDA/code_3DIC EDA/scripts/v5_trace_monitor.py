"""V5 Trace observation adapter for the existing HostMonitor/ResourceMonitor.

Only this invocation's directory is inspected. No captures are parsed, no
SQLite database is opened, and no routing/safety decision is made here.
"""
import csv
import io
import json
import os
from pathlib import Path
import re
import time
import threading
from diagnostics import HostMonitor,ResourceMonitor

STAGES=('baseline','landing','buffered','wire','tree','pin','maze','compact')
CHUNK=65536


def resource_tail(path):
    try:
        with Path(path).open('rb') as f:
            header=f.readline();f.seek(0,2);end=f.tell();start=max(len(header),end-32768)
            f.seek(start);data=f.read(32768)
        if start>len(header):data=data.partition(b'\n')[2]
        if not data.endswith(b'\n'):data=data.rpartition(b'\n')[0]+b'\n'
        records=list(csv.DictReader(io.StringIO((header+data).decode('utf8'))))
        return records[-1] if records else {}
    except (OSError,ValueError,csv.Error):return {}


class TraceObserver:
    def __init__(self,directory,trace_enabled):
        self.directory=Path(directory);self.trace_enabled=trace_enabled
        self.stage_directory=None;self.streams={};self.stage=None;self.errors=[]
        self.host_phase=None;self.resource_file=self.directory/'resource_monitor.csv'
        self.events=0;self.last_event=None
        (self.directory/'router.log').touch(exist_ok=True)

    def _logs(self):
        if self.stage_directory is None:
            paths=list(self.directory.glob('.final-router-*'))
            if len(paths)==1 and paths[0].is_dir():self.stage_directory=paths[0]
        paths=[self.directory/'router.stdout.log']
        if self.stage_directory:
            paths.extend(self.stage_directory/(stage+'.log') for stage in STAGES)
        return [p for p in paths if p.is_file()]

    def _record_line(self,path,line):
        text=line.decode('utf8',errors='replace').replace('\x00','').strip()
        # These are actual flushed algorithm records, not inferred percentages.
        interesting=(text.startswith(('STAGE ','Traceback ','V5_TRACE_IO_ERROR:'))
                     or re.search(r'\b(?:\w*Error|Exception):',text)
                     or '"validation"' in text or text.startswith('routed '))
        if interesting:
            self.events+=1;self.last_event={'file':str(path),'text':text[:1600]}
            display=text
            if text.startswith('{'):
                try:
                    record=json.loads(text);validation=record.get('validation')
                    if isinstance(validation,dict):validation=validation.get('status')
                    display='version=%s validation=%s seconds=%s'%(record.get('version','UNKNOWN'),validation or 'UNKNOWN',record.get('seconds','UNKNOWN'))
                except (ValueError,AttributeError):pass
            print('[LOG:%s] %s'%(path.name,display[:1600]),flush=True)

    def _copy(self,path,drain=False):
        key=str(path);offset,pending=self.streams.get(key,(0,b''))
        with path.open('rb') as src:
            if src.seek(0,2)<offset:raise OSError('Log truncated: '+key)
            src.seek(offset)
            while True:
                data=src.read(CHUNK)
                if not data:break
                # Original bytes, including WSL diagnostics, are retained.
                with (self.directory/'router.log').open('ab') as dst:
                    dst.write(('[SOURCE %s offset=%d]\n'%(path.relative_to(self.directory).as_posix(),offset)).encode('utf8'))
                    aggregate_offset=dst.tell()
                    dst.write(data)
                with (self.directory/'router.log.index.jsonl').open('a',encoding='utf8') as index:
                    index.write(json.dumps({'source':key,'source_offset':offset,'aggregate_offset':aggregate_offset,'length':len(data)},ensure_ascii=False)+'\n')
                offset+=len(data)
                lines=(pending+data).split(b'\n');pending=lines.pop()
                for line in lines:self._record_line(path,line)
                if len(pending)>CHUNK:pending=pending[-CHUNK:]
                self.streams[key]=(offset,pending)
                if not drain:break
        if drain and pending:
            self._record_line(path,pending);pending=b''
        self.streams[key]=(offset,pending)

    def collect(self,drain=False):
        paths=self._logs()
        for path in paths:
            try:self._copy(path,drain)
            except Exception as error:
                if len(self.errors)<10:self.errors.append(repr(error))
        observed=[s for s in STAGES if self.stage_directory and (self.stage_directory/(s+'.log')).is_file()]
        stage=observed[-1] if observed else None
        if stage and stage!=self.stage:
            self.stage=stage
            print('[STAGE] %s source=%s (stage log opened; no completion percentage)'%(stage,self.stage_directory/(stage+'.log')),flush=True)
        return paths

    def snapshot(self,state,pid):
        self.collect()
        row=resource_tail(self.resource_file)
        def number(key):
            try:return float(row[key])
            except (KeyError,ValueError,TypeError):return None
        sample_age=None
        try:
            import datetime
            sample_age=max(0,time.time()-datetime.datetime.fromisoformat(row['timestamp_utc']).timestamp())
        except (ValueError,KeyError):pass
        fresh=sample_age is not None and sample_age<15
        guide=self.directory/'route.guide';trace=self.directory/'trace'
        trace_files=[p for p in trace.iterdir() if p.is_file()] if trace.is_dir() else []
        trace_bytes=sum(p.stat().st_size for p in trace_files)
        guide_info={'state':'PRESENT' if guide.is_file() else 'MISSING','bytes':guide.stat().st_size if guide.is_file() else 0,
                    'stage_guides':sum((self.stage_directory/(s+'.guide')).is_file() for s in STAGES) if self.stage_directory else 0}
        trace_state='DISABLED' if not self.trace_enabled else 'RECEIPT_PRESENT' if (trace/'receipt.json').is_file() else 'CAPTURING' if trace_files else 'MISSING'
        return {'stage':self.host_phase or self.stage or 'PROGRESS_UNKNOWN',
                'stage_evidence':'host finalize() call' if self.host_phase else str(self.stage_directory/(self.stage+'.log')) if self.stage else None,
                'stage_state':'HOST_POSTPROCESS' if self.host_phase else 'LAST_OBSERVED_LOG_OPEN' if self.stage else 'PROGRESS_UNKNOWN',
                'last_algorithm_stage':self.stage,
                # A log or file write does not establish algorithm completion.
                'progress_status':'PROGRESS_UNKNOWN','router_liveness':'UNKNOWN_TO_HOST; subprocess state and resource samples are separate evidence',
                'cpu_percent':number('cpu_percent') if fresh else None,
                'rss_bytes':number('tree_rss_bytes') if fresh else None,
                'resource_process_state':row.get('process_state') if fresh else None,
                'resource_source':row.get('source') if fresh else 'UNAVAILABLE_OR_STALE',
                'resource_role':'host_trace_verifier' if self.host_phase else 'router_process_tree',
                'resource_sample_age_seconds':sample_age,'cpu_scope':row.get('cpu_scope'),
                'guide':guide_info,'trace':{'state':trace_state,'bytes':trace_bytes,'files':len(trace_files)},
                'observed_log_events':self.events,'last_log_event':self.last_event,'observer_errors':self.errors[:]}

    @staticmethod
    def format(item):
        cpu=item.get('cpu_percent');rss=item.get('rss_bytes');guide=item.get('guide',{});trace=item.get('trace',{})
        return ('stage=%s stage_state=%s cpu=%s rss=%s process_state=%s guide=%s(%sB,stage_guides=%s) trace=%s(%sB) progress=PROGRESS_UNKNOWN'%(
            item.get('stage','PROGRESS_UNKNOWN'),item.get('stage_state','PROGRESS_UNKNOWN'),'N/A' if cpu is None else '%.1f%%'%cpu,
            'N/A' if rss is None else '%.1fMiB'%(rss/2**20),item.get('resource_process_state') or 'UNKNOWN',
            guide.get('state','UNKNOWN'),guide.get('bytes',0),guide.get('stage_guides',0),trace.get('state','UNKNOWN'),trace.get('bytes',0)))

    def finish(self):
        try:self.collect(drain=True)
        except Exception as error:
            if len(self.errors)<10:self.errors.append(repr(error))
        return {'scope':'stdout/stderr plus all final-router stage logs; bounded streaming copy, no algorithm progress percentage',
                'streams':{key:{'copied_bytes':value[0]} for key,value in self.streams.items()},'errors':self.errors[:],
                'aggregate_index':'router.log.index.jsonl',
                'last_observed_stage':self.stage,'log_events':self.events}


class TraceVerificationHeartbeat:
    """Continue the existing HostMonitor while the host finalizes Trace data."""
    def __init__(self,directory,observer,started,interval=5):
        self.stop=threading.Event();self.monitor=None;self.thread=None;self.errors=[]
        observer.host_phase='trace_verify';observer.resource_file=Path(directory)/'trace_verification_resources.csv'
        try:
            self.monitor=HostMonitor(directory,interval=interval,observer=observer,append=True,
                                     start_time=started,initial_state='EXITED')
            self.monitor.native_resources=ResourceMonitor(observer.resource_file,os.getpid(),role='trace_verifier',process_metrics_enabled=True)
            self.thread=threading.Thread(target=self.watch,args=(interval,),daemon=True);self.thread.start()
        except Exception as error:self.errors.append(repr(error))

    def watch(self,interval):
        while not self.stop.wait(interval):
            try:self.monitor.tick('EXITED',force=True)
            except Exception as error:
                if len(self.errors)<10:self.errors.append(repr(error))

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join()
        if self.monitor:
            try:self.monitor.close('EXITED',0)
            except Exception as error:self.errors.append(repr(error))
            self.errors.extend(self.monitor.errors)
        return self.errors[:10]
