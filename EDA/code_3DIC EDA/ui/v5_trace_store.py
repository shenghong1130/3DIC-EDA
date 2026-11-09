"""Read-only, per-Net access to verified V5 observation artifacts."""
import hashlib
import json
import sqlite3
from pathlib import Path
import sys
from v5_store import V5Store

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from v5_trace.route_trace import geometry

NOTICE='V5 Trace：只有 FINAL_ACCEPTED_VERIFIED 显示真实最终实线。REAL_ASTAR 来自父节点回溯；REAL_ROUTER_PATH 来自实际线段/stack 操作。PATH_PROVENANCE_LOST 仅显示 Guide；GUIDE_ESTIMATED 是独立虚线示意。记录是 GCell 布线，不是轨道级制造导线。'


class V5TraceStore(V5Store):
    def __init__(self,directory,cache,reference=None):
        super().__init__(directory,Path(cache)/'v5_trace',reference)
        self.trace_dir=self.directory/'trace'
        self.trace_file=self.trace_dir/'route_trace.jsonl'
        self.trace_index=self.trace_dir/'route_trace.index.sqlite'
        self.available=self.trace_file.is_file() and self.trace_index.is_file() and (self.trace_dir/'receipt.json').is_file()
        self.trace_error=None
        try:self.trace_receipt=json.loads((self.trace_dir/'receipt.json').read_text(encoding='utf8')) if self.available else {}
        except (ValueError,OSError) as exc:
            self.trace_receipt={};self.available=False;self.trace_error=str(exc)
        if self.available and self.trace_receipt.get('guide_sha256')!=self.run.get('guide_sha256'):
            self.available=False
        self.sources.append(Path(__file__))
        if self.available:self.sources.extend([self.trace_file,self.trace_index,self.trace_dir/'receipt.json'])
        self.signature=self.sign();self.token=hashlib.sha256(json.dumps(self.signature).encode()).hexdigest()[:24]
        self.snapshot=self.cache/('v5_trace_'+self.token+'.json')

    def trace_db(self):
        return sqlite3.connect(self.trace_index.resolve().as_uri()+'?mode=ro',uri=True)

    def record(self,name):
        self.check()
        if not self.available:return None
        with self.trace_db() as c:
            row=c.execute('SELECT off,len,sha256 FROM final WHERE net=?',(name,)).fetchone()
        if not row:return None
        if row[1]==0:return None
        if row[1]>32*1024*1024:raise ValueError('Trace Net record exceeds UI limit')
        with self.trace_file.open('rb') as f:
            f.seek(row[0]);line=f.read(row[1])
        if hashlib.sha256(line).hexdigest()!=row[2]:raise ValueError('Trace record SHA256 mismatch')
        record=json.loads(line)
        if record['net']!=name or record['guide_sha256']!=self.run['guide_sha256']:raise ValueError('Trace Net/Guide identity mismatch')
        return record

    def attach(self,n):
        error=None
        try:record=self.record(n['net'])
        except (ValueError,OSError,sqlite3.Error) as exc:record=None;error='Invalid Trace: '+str(exc)
        n['path_provenance']=record.get('provenance') if record else 'PATH_PROVENANCE_LOST'
        n['path_reason']=record.get('reason') if record else error or 'No valid Trace for this Net'
        n['path_origins']=record.get('origins',[]) if record else []
        n['trace_candidate_id']=record.get('candidate_id') if record else None
        n['trace_guide_sha256']=record.get('guide_sha256') if record else None
        n['guide_estimated_provenance']='GUIDE_ESTIMATED'
        n['route_edges_available']=False
        n['edges']=[];n['nodes']=[]
        if not record or record['provenance']!='FINAL_ACCEPTED_VERIFIED' or not record.get('accepted'):return n
        layers={l['number']:l for l in self.index().summary()['layers']}
        if geometry(n['guides'],layers)!=record['geometry_sha256']:
            n['path_provenance']='PATH_PROVENANCE_LOST';n['path_reason']='Net Guide geometry differs from verified Trace';return n
        def node_id(i):return 'v5:'+n['net']+':'+str(i)
        n['nodes']=[dict(id=node_id(p['id']),x=p['x'],y=p['y'],l=p['layer']) for p in record['nodes']]
        n['edges']=[[node_id(e['a']),node_id(e['b'])] for e in record['edges']]
        n['edge_records']=record['edges'];n['route_edges_available']=True
        n['via_count']=sum(e['kind']=='VIA' for e in record['edges'])
        n['tsv_count']=sum(e['kind']=='TSV' for e in record['edges'])
        n['trace_tsv_edges']=[[node_id(e['a']),node_id(e['b'])] for e in record['edges'] if e['kind']=='TSV']
        n['tsv_markers']=[]
        n['status']='FINAL_ACCEPTED_VERIFIED'
        return n

    def net(self,name,reference=False):
        n=self.index().net(name,reference)
        return n if reference else self.attach(n)

    def geometry(self,q):
        out=self.index().geometry(q)
        # Overview remains bounded. Request full selected-Net geometry for
        # verification, then retain only the already bounded viewport Guides.
        if q.get('source')!='reference' and q.get('full')=='1':
            size=256;items=[]
            for n in out['nets']:
                full=self.net(n['net']);full['pins']=[]
                size+=len(json.dumps(full,ensure_ascii=False).encode('utf8'))
                if size>4*1024*1024:out['truncated']=True;break
                items.append(full)
            out['nets']=items
        return out

    def summary(self,reference=False):
        result=super().summary(reference)
        if not reference:
            result['source']='v5_trace';result['visualization_notice']=NOTICE
            result['warnings']=[NOTICE];result['trace_receipt']=self.trace_receipt
            result['capabilities'].update(route_edges=self.available,via=self.available,trace=self.available,guide_tsv=True,guide_estimates=True)
        return result

    def trace(self,name='',after=0,limit=1000):
        if not name:return {'events':[],'requires_net':True,'available':self.available,'reason':'Trace is paged per Net; only recorded events can replay'}
        record=self.record(name)
        if not record:return {'events':[],'requires_net':True,'available':False,'reason':'No Trace for this Net'}
        capture=self.trace_dir/'capture.sqlite'
        events=[];next_seq=None
        if capture.is_file():
            with sqlite3.connect(capture.resolve().as_uri()+'?mode=ro',uri=True) as c:
                rows=c.execute('SELECT seq,file,off,len FROM events WHERE net=? AND seq>? ORDER BY seq LIMIT ?',(name,max(0,int(after)),min(2000,max(1,int(limit)))+1))
                for i,(seq,file,off,length) in enumerate(rows):
                    if i>=min(2000,max(1,int(limit))):next_seq=events[-1]['seq'];break
                    if length>1024*1024:continue
                    path=(self.trace_dir/file).resolve()
                    if path.parent!=self.trace_dir.resolve():raise ValueError('Invalid Trace shard')
                    with path.open('rb') as f:f.seek(off);raw=f.read(length)
                    for offset,line in enumerate(raw.splitlines()):
                        event=json.loads(line);event['seq']=seq;event['chunk_event']=offset
                        # Candidates remain explicitly intermediate during replay.
                        events.append(event)
                    if len(events)>=2000:next_seq=seq;break
        return {'events':events,'requires_net':True,'available':True,'next_after':next_seq,'final_provenance':record['provenance'],'search_recorded':self.run.get('trace_options',{}).get('search',False),'replay_scope':'Recorded events only; no reconstructed Open/Closed set'}
