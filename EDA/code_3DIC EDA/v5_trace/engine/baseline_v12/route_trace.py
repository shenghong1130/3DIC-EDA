"""Fail-open, disk-backed observation of actual router operations.

No decision, random, cost, budget or geometry object is changed by this module.
Only explicitly chosen candidates can enter a validated Guide's lineage.
"""
import functools
import atexit
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

ENABLED = bool(os.environ.get('V5_TRACE_DIR'))
SEARCH = os.environ.get('V5_TRACE_SEARCH') == '1'
_writer = None
_failed = False
_stack = []
_design = None


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def guide_key(hash,path):
    return hash+':'+str(Path(path).resolve())


def geometry(rects, layers):
    """Canonical union of collinear directional rectangles, never a path."""
    groups = {}
    for x,y,xx,yy,l in rects:
        horizontal = layers[l]['direction'] == 'HORIZONTAL'
        key = (l,y,yy,True) if horizontal else (l,x,xx,False)
        groups.setdefault(key, []).append((x,xx) if horizontal else (y,yy))
    out = []
    for (l,lo,hi,h), intervals in sorted(groups.items()):
        start = end = None
        for a,b in sorted(intervals):
            if end is not None and a <= end:
                end = max(end,b)
            else:
                if end is not None:
                    out.append((start,lo,end,hi,l) if h else (lo,start,hi,end,l))
                start,end = a,b
        out.append((start,lo,end,hi,l) if h else (lo,start,hi,end,l))
    return hashlib.sha256(json.dumps(sorted(out),separators=(',',':')).encode()).hexdigest()


def diagnostic(exc):
    global _failed
    _failed = True
    try:
        directory = Path(os.environ['V5_TRACE_DIR'])
        with (directory/'diagnostics.jsonl').open('a',encoding='utf8') as f:
            f.write(json.dumps({'pid':os.getpid(),'error':repr(exc),'policy':'TRACE_DISABLED_ROUTER_CONTINUES'})+'\n')
    except Exception:
        try:sys.stderr.write('V5_TRACE_IO_ERROR: '+repr(exc)+'\n')
        except Exception:pass


def safe(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        if not ENABLED or _failed:
            return None
        try:return fn(*args, **kwargs)
        except Exception as exc:diagnostic(exc);return None
    return wrapped


class Writer:
    def __init__(self):
        self.directory = Path(os.environ['V5_TRACE_DIR'])
        self.directory.mkdir(parents=True,exist_ok=True)
        self.pid = os.getpid()
        self.process = str(self.pid)+'-'+uuid.uuid4().hex[:8]
        self.stage = Path(sys.argv[0]).name
        self.filter = set(json.loads(os.environ.get('V5_TRACE_NETS','[]')))
        self.limit = max(4096,int(os.environ.get('V5_TRACE_MAX_BYTES',str(256*1024*1024))))
        self.db = sqlite3.connect(self.directory/'capture.sqlite',timeout=5,isolation_level=None)
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          PRAGMA synchronous=OFF;
          PRAGMA cache_size=-2048;
          CREATE TABLE IF NOT EXISTS budget(k TEXT PRIMARY KEY,n INTEGER);
          INSERT OR IGNORE INTO budget VALUES('bytes',0);
          CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,net TEXT,candidate TEXT,event TEXT,file TEXT,off INTEGER,len INTEGER);
          CREATE INDEX IF NOT EXISTS event_net ON events(net,seq);
          CREATE INDEX IF NOT EXISTS event_candidate ON events(candidate,seq);
          CREATE TABLE IF NOT EXISTS candidates(seq INTEGER PRIMARY KEY,id TEXT UNIQUE,process TEXT,net TEXT,geom TEXT,complete INTEGER,parts TEXT,stage TEXT,object_id INTEGER,scope TEXT);
          CREATE INDEX IF NOT EXISTS candidate_geom ON candidates(process,net,geom,seq);
          CREATE TABLE IF NOT EXISTS choices(process TEXT,net TEXT,geom TEXT,candidate TEXT,PRIMARY KEY(process,net));
          CREATE TABLE IF NOT EXISTS guides(hash TEXT PRIMARY KEY,path TEXT);
          CREATE TABLE IF NOT EXISTS guide_nets(hash TEXT,net TEXT,geom TEXT,candidate TEXT,PRIMARY KEY(hash,net));
          CREATE TABLE IF NOT EXISTS options(process TEXT,net TEXT,geom TEXT,candidate TEXT,PRIMARY KEY(process,net,geom));
          CREATE TABLE IF NOT EXISTS invalidations(process TEXT,net TEXT,geom TEXT,reason TEXT,PRIMARY KEY(process,net,geom));
          CREATE TABLE IF NOT EXISTS scratch(id TEXT PRIMARY KEY,file TEXT,complete INTEGER,off INTEGER,len INTEGER);
          CREATE TABLE IF NOT EXISTS materialized(id TEXT PRIMARY KEY);
        ''')
        self.file = 'capture-'+self.process+'.jsonl'
        self.stream = (self.directory/self.file).open('ab',buffering=256*1024)
        self.pending = None
        self.reserved = 0
        self.exhausted = set()
        self.scratch_bytes = 0
        self.scratch_limit = min(32*1024*1024,self.limit//8)
        self.scratch_files = {}
        self.scratch_file='_scratch-'+self.process+'.jsonl'
        self.scratch_stream=(self.directory/self.scratch_file).open('w+b',buffering=256*1024)
        atexit.register(self.flush)

    def flush(self):
        if self.pending:
            net,candidate,event,off,length=self.pending
            self.stream.flush()
            self.db.execute('INSERT INTO events(net,candidate,event,file,off,len) VALUES(?,?,?,?,?,?)',(net,candidate,event,self.file,off,length))
            self.pending=None

    def reserve(self,amount,fraction):
        if fraction in self.exhausted:return False
        if self.reserved>=amount:
            self.reserved-=amount
            return True
        self.db.execute('BEGIN IMMEDIATE')
        used=self.db.execute("SELECT n FROM budget WHERE k='bytes'").fetchone()[0]
        reserve=min(1024*1024,max(0,int(self.limit*fraction)-used))
        if reserve<amount:
            self.db.execute('ROLLBACK');self.exhausted.add(fraction);return False
        self.db.execute("UPDATE budget SET n=n+? WHERE k='bytes'",(reserve,))
        self.db.execute('COMMIT')
        self.reserved+=reserve-amount
        return True

    def emit(self,event,net=None,candidate=None,priority='path',**data):
        item = dict(schema_version=1,event=event,net=net,candidate_id=candidate,stage=self.stage,process=self.process,accepted=False,provenance='INTERMEDIATE_ONLY',**data)
        line = (json.dumps(item,separators=(',',':'),ensure_ascii=False)+'\n').encode('utf8')
        fraction = .08 if priority=='search' else .72 if priority=='option' else .85 if priority in ('path','accepted') else 1
        # Include an allowance for SQLite index pages. Reserve 15% for decisions
        # and publication; search cannot consume the path allowance.
        amount = len(line)+256
        if not self.reserve(amount,fraction):return False
        if self.pending and (self.pending[:3] != [net,candidate,event] or self.pending[4]+len(line)>32768):self.flush()
        off = self.stream.tell()
        self.stream.write(line)
        if self.pending:self.pending[4]+=len(line)
        else:self.pending=[net,candidate,event,off,len(line)]
        return True


def writer():
    global _writer
    if _writer is None:
        _writer = Writer()
    return _writer


def net_name(router):
    frame = sys._getframe(1)
    for _ in range(32):
        if frame is None:break
        name = frame.f_locals.get('name')
        if isinstance(name,str) and name in router.d.nets:
            return name
        frame = frame.f_back
    return getattr(router,'net_name',None)


def begin(router, option=False):
    w = writer();name = net_name(router)
    if w.filter and name not in w.filter:
        return None
    cap = dict(id=uuid.uuid4().hex,net=name,complete=True,parts=[],option=option,algorithm=getattr(router,'mode',getattr(router,'path_pattern','MANHATTAN')),router=router)
    cap['scratch_file']=w.scratch_file
    cap['stream']=w.scratch_stream
    cap['start']=w.scratch_stream.tell()
    w.scratch_files[cap['id']]=[w.scratch_file,0,cap['net']]
    _stack.append(cap)
    return cap


def current():
    return _stack[-1] if _stack else None


def emit(event,**data):
    cap = current()
    if cap:
        w=writer()
        if event.startswith('search_'):
            w.emit(event,cap['net'],cap['id'],priority='search',**data)
            return
        # Candidate geometry is streamed to bounded temporary disk space.
        # Only the candidate actually selected by the algorithm is promoted to
        # persistent path records. Rejected candidates retain summary events.
        line=(json.dumps(dict(event=event,candidate_id=cap['id'],**data),ensure_ascii=False,separators=(',',':'))+'\n').encode('utf8')
        if w.scratch_bytes+len(line)>w.scratch_limit:
            cap['complete']=False;return
        cap['stream'].write(line);w.scratch_bytes+=len(line)
        w.scratch_files[cap['id']][1]+=len(line)


def finish(cap,rects):
    if cap is None:return
    if not _stack or _stack[-1] is not cap:
        raise RuntimeError('Unbalanced trace context')
    _stack.pop()
    w = writer();geom = geometry(rects,cap['router'].d.layers)
    cap['stream'].flush()
    w.db.execute('INSERT INTO scratch VALUES(?,?,?,?,?)',(cap['id'],cap['scratch_file'],int(cap['complete']),cap['start'],cap['stream'].tell()-cap['start']))
    complete = cap['complete']
    # The exact emitted rectangle footprint must equal the candidate. Rejected
    # adaptive alternatives are isolated and enter only via selected parts.
    if not w.emit('candidate',cap['net'],cap['id'],priority='decision',geometry_sha256=geom,complete=complete,parts=cap['parts'],algorithm=cap['algorithm']):
        complete = False
    w.db.execute('INSERT INTO candidates(id,process,net,geom,complete,parts,stage,object_id,scope) VALUES(?,?,?,?,?,?,?,?,?)',(cap['id'],w.process,cap['net'],geom,int(complete),json.dumps(cap['parts']),w.stage,id(rects),'option' if cap['option'] else 'build'))
    w.flush()
    return geom


def observe_build(fn):
    if not ENABLED:return fn
    @functools.wraps(fn)
    def wrapped(self,*args,**kwargs):
        # Inherited builders form one operation; do not duplicate observations.
        if current() or _failed:return fn(self,*args,**kwargs)
        cap = _begin(self)
        try:
            result = fn(self,*args,**kwargs)
        except BaseException:
            if cap:
                while _stack:
                    abandoned=_stack.pop()
                    abandoned['complete']=False
                    if abandoned is cap:break
            raise
        if cap:
            center = args[1] if fn.__name__=='_build' and len(args)>1 else None
            if center is not None:
                _tsv(center)
            elif fn.__name__=='_route_net_fixed' and result[1] and hasattr(self,'last_tsv_center'):
                _tsv(self.last_tsv_center)
            _finish(cap,result[0] if fn.__name__=='_route_net_fixed' else result)
        return result
    return wrapped


_begin = safe(begin)
_finish = safe(finish)


@safe
def _tsv(center):
    emit('edge',a=[*center,10],b=[*center,11],kind='TSV',algorithm='BUILD_TSV_CENTER',source='actual _build center argument',origin='REAL_ROUTER_PATH')


def observe_rect(fn):
    if not ENABLED:return fn
    @functools.wraps(fn)
    def wrapped(self,x1,y1,x2,y2,layer,*args,**kwargs):
        result = fn(self,x1,y1,x2,y2,layer,*args,**kwargs)
        _rect(self,x1,y1,x2,y2,layer,result)
        return result
    return wrapped


@safe
def _rect(self,x1,y1,x2,y2,layer,result):
    if current():
        emit('primitive',a=[x1,y1,layer],b=[x2,y2,layer],rectangle=result,kind='WIRE' if (x1,y1)!=(x2,y2) else 'NODE',algorithm=current()['algorithm'],source='actual rect() arguments before Guide conversion',origin='REAL_ROUTER_PATH')


def observe_stack(fn):
    if not ENABLED:return fn
    @functools.wraps(fn)
    def wrapped(self,rects,x,y,lo,hi):
        result = fn(self,rects,x,y,lo,hi)
        _stack_edges(x,y,lo,hi)
        return result
    return wrapped


@safe
def _stack_edges(x,y,lo,hi):
    for layer in range(min(lo,hi),max(lo,hi)):
        emit('edge',a=[x,y,layer],b=[x,y,layer+1],kind='TSV' if layer==10 else 'VIA',algorithm='ACCESS_STACK',source='actual stack() operation',origin='REAL_ROUTER_PATH')


@safe
def astar_path(router,path):
    # Called immediately after parent backtracking, before any rect conversion.
    # This is the path the algorithm just generated, not a Guide traversal.
    previous = None
    search_id = uuid.uuid4().hex
    for col,row,layer in reversed(path):
        p = [*router.grid.center(col,row),layer]
        emit('astar_node',node=p,gcell=[col,row,layer],search_id=search_id,algorithm='ASTAR',source='MazeRouter.connect parents backtrack',origin='REAL_ASTAR')
        if previous is not None:
            emit('edge',a=previous,b=p,kind='WIRE' if previous[2]==layer else 'VIA',search_id=search_id,algorithm='ASTAR',source='MazeRouter.connect parents backtrack',origin='REAL_ASTAR')
        previous = p


@safe
def search_event(router,event,state,parent=None):
    if SEARCH:
        emit(event,node=[*router.grid.center(*state[:2]),state[2]],gcell=state,parent=[*router.grid.center(*parent[:2]),parent[2]] if parent else None,algorithm='ASTAR',source='actual heap expansion / relaxation',origin='REAL_ASTAR')


@safe
def option_begin(router):
    if current():begin(router,True)


@safe
def option_end(router,rects):
    cap = current()
    if cap and cap['option']:
        geom = finish(cap,rects);w = writer()
        w.db.execute('INSERT OR REPLACE INTO options VALUES(?,?,?,?)',(w.process,cap['net'],geom,cap['id']))


@safe
def option_selected(router,rects):
    cap = current()
    if cap:
        w = writer();geom = geometry(rects,router.d.layers)
        row = w.db.execute('SELECT candidate FROM options WHERE process=? AND net IS ? AND geom=?',(w.process,cap['net'],geom)).fetchone()
        if row:cap['parts'].append(row[0])
        else:cap['complete'] = False


def observe_choice(fn):
    if not ENABLED:return fn
    @functools.wraps(fn)
    def wrapped(self,*args,**kwargs):
        start = _choice_start()
        result = fn(self,*args,**kwargs)
        _choice_end(self,fn.__name__,args,result,start)
        return result
    return wrapped


@safe
def _choice_start():
    return writer().db.execute('SELECT coalesce(max(seq),0) FROM candidates').fetchone()[0]


@safe
def _choice_end(self,method,args,result,start):
    global _design
    _design=self.d
    w = writer();name = args[0] if method=='reroute' else net_name(self)
    if w.filter and name not in w.filter:return
    geom = geometry(result if method=='reroute' else result[0],self.d.layers)
    rects=result if method=='reroute' else result[0]
    row = w.db.execute('SELECT id FROM candidates WHERE process=? AND net IS ? AND geom=? AND seq>? AND object_id=? AND scope=\'build\' ORDER BY seq DESC LIMIT 1',(w.process,name,geom,start or 0,id(rects))).fetchone()
    if method=='reroute' and result==args[1]:row=None
    if method=='route_net' and not row:
        matches=w.db.execute('SELECT id FROM candidates WHERE process=? AND net IS ? AND geom=? AND seq>? AND scope=\'build\' ORDER BY seq DESC LIMIT 2',(w.process,name,geom,start or 0)).fetchall()
        if len(matches)==1:row=matches[0]
    candidate = row[0] if row else None
    if candidate:_materialize(candidate)
    w.db.execute('INSERT OR REPLACE INTO choices VALUES(?,?,?,?)',(w.process,name,geom,candidate))
    w.emit('choice',name,candidate,priority='decision',geometry_sha256=geom,locally_selected=bool(candidate),method=method)
    _discard_scratch(name)


def _materialize(candidate):
    w=writer()
    if w.db.execute('SELECT 1 FROM materialized WHERE id=?',(candidate,)).fetchone():return
    row=w.db.execute('SELECT net,parts FROM candidates WHERE id=?',(candidate,)).fetchone()
    scratch=w.db.execute('SELECT file,complete,off,len FROM scratch WHERE id=?',(candidate,)).fetchone()
    if not row or not scratch:
        w.db.execute('UPDATE candidates SET complete=0 WHERE id=?',(candidate,));return
    complete=bool(scratch[1])
    for part in json.loads(row[1]):_materialize(part)
    with (w.directory/scratch[0]).open('rb') as f:
        f.seek(scratch[2]);end=scratch[2]+scratch[3]
        while f.tell()<end:
            line=f.readline();item=json.loads(line)
            if item.pop('candidate_id')!=candidate:continue
            event=item.pop('event')
            if not w.emit(event,row[0],candidate,priority='accepted',**item):complete=False
    w.flush()
    w.db.execute('UPDATE candidates SET complete=? WHERE id=?',(int(complete),candidate))
    w.db.execute('INSERT OR IGNORE INTO materialized VALUES(?)',(candidate,))


def _discard_scratch(name=None):
    w=writer()
    for id,(file,size,net) in list(w.scratch_files.items()):
        if name is None or name==net:
            w.scratch_bytes-=size;w.scratch_files.pop(id)
            w.db.execute('DELETE FROM scratch WHERE id=?',(id,))
    if not w.scratch_files and not current():
        w.scratch_stream.seek(0);w.scratch_stream.truncate();w.scratch_bytes=0


def observe_validation(fn):
    if not ENABLED:return fn
    @functools.wraps(fn)
    def wrapped(design,path,*args,**kwargs):
        result = fn(design,path,*args,**kwargs)
        _validated(design,path)
        return result
    return wrapped


@safe
def _validated(design,path):
    global _design
    _design=design
    w = writer();hash = digest(path);snapshot=guide_key(hash,path)
    if w.db.execute('SELECT 1 FROM guides WHERE hash=?',(snapshot,)).fetchone():return
    seed = None
    if '--seed-guide' in sys.argv:
        seedpath = Path(sys.argv[sys.argv.index('--seed-guide')+1])
        if seedpath.is_file():seed = guide_key(digest(seedpath),seedpath)
    from validate import read_guide
    for name,rects in read_guide(path):
        if w.filter and name not in w.filter:continue
        geom = geometry(rects,design.layers)
        choice = w.db.execute('SELECT candidate FROM choices WHERE process=? AND net=? AND geom=?',(w.process,name,geom)).fetchone()
        candidate = choice[0] if choice else None
        invalid = w.db.execute('SELECT 1 FROM invalidations WHERE process=? AND net=? AND geom=?',(w.process,name,geom)).fetchone()
        if invalid:candidate = None
        if not candidate and seed and not invalid:
            row = w.db.execute('SELECT candidate FROM guide_nets WHERE hash=? AND net=? AND geom=?',(seed,name,geom)).fetchone()
            candidate = row[0] if row else None
        w.db.execute('INSERT OR REPLACE INTO guide_nets VALUES(?,?,?,?)',(snapshot,name,geom,candidate))
    w.db.execute('INSERT INTO guides VALUES(?,?)',(snapshot,str(path)))
    w.emit('guide_validated',priority='decision',guide_sha256=hash,path=str(path))


@safe
def stage_decision(record,current,output):
    writer().emit('stage_decision',priority='decision',record=record,retained_guide_sha256=digest(current),candidate_guide_sha256=digest(output) if Path(output).is_file() else None)


@safe
def alias_guide(source,target):
    if not str(target).endswith('.guide') or not Path(target).is_file():return
    w=writer();hash=digest(target);old=guide_key(hash,source);new=guide_key(hash,target)
    if not w.db.execute('SELECT 1 FROM guides WHERE hash=?',(old,)).fetchone():return
    w.db.execute('INSERT OR REPLACE INTO guide_nets SELECT ?,net,geom,candidate FROM guide_nets WHERE hash=?',(new,old))
    w.db.execute('INSERT OR REPLACE INTO guides VALUES(?,?)',(new,str(target)))
    w.emit('guide_published',priority='decision',guide_sha256=hash,source_snapshot=old,target_snapshot=new)


def observe_topology(fn):
    if not ENABLED:return fn
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        result = fn(*args,**kwargs)
        _topology(fn.__name__,args,result)
        return result
    return wrapped


@safe
def _topology(method,args,result):
    if not current():return
    algorithm = 'STEINER' if method=='steiner_tree' else 'PRIM_MST' if len(args[0])<=128 else 'SPARSE_MST'
    emit('topology_generated',algorithm=algorithm,source='actual '+method+'() return',origin='REAL_ROUTER_PATH',terminal_count=len(args[0]))


def observe_compaction(fn):
    if not ENABLED:return fn
    @functools.wraps(fn)
    def wrapped(d,r,name,rects,*args,**kwargs):
        result=fn(d,r,name,rects,*args,**kwargs)
        _compacted(d,name,rects,result[0])
        return result
    return wrapped


@safe
def _compacted(d,name,before,after):
    if before!=after:
        w=writer();geom=geometry(after,d.layers)
        row=w.db.execute('SELECT id FROM candidates WHERE process=? AND net=? AND geom=? AND object_id=? ORDER BY seq DESC LIMIT 1',(w.process,name,geom,id(after))).fetchone()
        if row:
            if w.stage=='compact_guide.py':
                _materialize(row[0]);w.db.execute('INSERT OR REPLACE INTO choices VALUES(?,?,?,?)',(w.process,name,geom,row[0]))
                w.emit('choice',name,row[0],priority='decision',geometry_sha256=geom,locally_selected=True,method='compact_net validated return')
                _discard_scratch(name)
        else:
            w.db.execute('INSERT OR REPLACE INTO invalidations VALUES(?,?,?,?)',(w.process,name,geom,'COMPACTION_CHANGED_SUBGRAPH'))
            w.emit('provenance_lost',name,priority='decision',geometry_sha256=geom,reason='COMPACTION_CHANGED_SUBGRAPH',algorithm='LEAF_PRUNING_OR_DIJKSTRA')


@safe
def compact_result(d,r,name,result,alive,neighbors):
    if current():return
    from types import SimpleNamespace
    router=SimpleNamespace(d=d,net_name=name,path_pattern='COMPACT_SELECTED_SUBGRAPH')
    cap=begin(router)
    if cap is None:return
    grid=d._guide_grid
    def p(key):
        layer,index=divmod(key,r.cells);row,col=divmod(index,r.cols)
        return [*grid.center(col,row),layer]
    for key in sorted(alive):
        node=p(key);x,y,layer=node
        # Occupancy is read from the actual selected internal GCell subgraph.
        # Edges are the compactor's own adjacency, before its locals disappear.
        emit('primitive',a=node,b=node,rectangle=grid.rectangle(x,y,x,y,layer),kind='NODE',algorithm='COMPACT_SELECTED_SUBGRAPH',source='compact_net actual alive GCell set',origin='REAL_ROUTER_PATH')
        for other,weight in neighbors(key,alive):
            if other<=key:continue
            target=p(other)
            emit('edge',a=node,b=target,kind='WIRE' if layer==target[2] else 'TSV' if {layer,target[2]}=={10,11} else 'VIA',algorithm='COMPACT_SELECTED_SUBGRAPH',source='compact_net actual neighbors(alive)',origin='REAL_ROUTER_PATH')
    finish(cap,result)


@safe
def compact_path(r,path,root):
    # Actual Dijkstra parent chain. An intermediate record only: the complete
    # retained subgraph also contains implicit edges that this chain cannot prove.
    w=writer();frame=sys._getframe(1);name=None
    for _ in range(8):
        if frame is None:break
        if 'name' in frame.f_locals:name=frame.f_locals['name'];break
        frame=frame.f_back
    if w.filter and name not in w.filter:return
    previous=root
    for key in reversed(path):
        def p(k):
            l,i=divmod(k,r.cells);row,col=divmod(i,r.cols)
            return [*r.d._guide_grid.center(col,row),l]
        w.emit('compact_dijkstra_edge',name,priority='path',a=p(previous),b=p(key),algorithm='DIJKSTRA',origin='REAL_ROUTER_PATH',source='compact_net parent backtrack')
        previous=key


@safe
def _publish_output():
    # Some seed scripts validate each Net rather than calling validate() on
    # their whole output. Publish their selected identity before the parent
    # consumes that output. The final router still performs the real validation.
    if _design is not None and '--output' in sys.argv:
        output=Path(sys.argv[sys.argv.index('--output')+1])
        if output.is_file():_validated(_design,output)
    if _writer:
        _writer.flush()
        _discard_scratch()
        _writer.scratch_stream.close()
        (_writer.directory/_writer.scratch_file).unlink(missing_ok=True)


if ENABLED:atexit.register(_publish_output)
