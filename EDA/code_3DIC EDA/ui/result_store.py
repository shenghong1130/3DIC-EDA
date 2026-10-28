"""Read-only result sources, bounded streaming import and disposable SQLite UI cache.

No router/evaluator data is rewritten. One importer at a time; a 1 MiB reader,
64 MiB single-value ceiling, 8 MiB SQLite cache and file-backed temp storage.
Normal memory is proportional to one Net, never to the full result's Net array.
"""
from pathlib import Path
import json,sqlite3,hashlib,threading,time,os,uuid,math,re
from contextlib import contextmanager

VERSION=3
LARGE_BYTES=16*1024*1024
_jobs={};_lock=threading.RLock();_import_slot=threading.Semaphore(1)
_query_slot=threading.BoundedSemaphore(4)
_thermal_slot=threading.Lock()

def thermal_sample(path,cache):
    """Uniform deterministic stride sample, at most 10k original thermal nodes."""
    path=Path(path);signature=fingerprint(path);key=hashlib.sha256(json.dumps(signature).encode()).hexdigest()[:24]
    target=Path(cache)/('thermal_'+key+'.json');target.parent.mkdir(parents=True,exist_ok=True)
    with _thermal_slot:
        if target.is_file():return json.loads(target.read_text(encoding='utf8'))
        r=Cursor(path);sample=[];count=0;stride=1
        try:
            r.take('{')
            while r.char()!='}':
                name=r.value()[0];r.take(':')
                if name=='nodes':
                    for node,_,_ in r.array():
                        if count%stride==0:sample.append(node)
                        count+=1
                        if len(sample)>10000:sample=sample[::2];stride*=2
                else:r.skip()
                if r.char()=='}':break
                r.take(',')
            r.take('}')
            if r.char():raise ValueError('Trailing thermal JSON')
        finally:r.close()
        if signature!=fingerprint(path):raise ValueError('Thermal source changed; retry')
        result={'nodes':sample,'total_nodes':count,'stride':stride};temp=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp');temp.write_text(json.dumps(result,separators=(',',':')),encoding='utf8');os.replace(temp,target);return result

def fingerprint(path):
    s=path.stat()
    return [str(path.resolve()),s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_ino]

class Cursor:
    """Incremental JSON values with exact UTF-8 byte offsets (also for non-ASCII)."""
    def __init__(self,path):
        self.file=Path(path).open('r',encoding='utf8',newline='');self.buf='';self.pos=0;self.eof=False;self.base=0;self.mark=0;self.decoder=json.JSONDecoder();self.maximum=0
    def close(self):self.file.close()
    def offset(self):
        self.base+=len(self.buf[self.mark:self.pos].encode('utf8'));self.mark=self.pos;return self.base
    def more(self):
        self.offset();self.buf=self.buf[self.pos:];self.pos=self.mark=0
        if len(self.buf)>64*1024*1024:raise ValueError('UI index: one JSON value exceeds the 64 MiB safety limit')
        text=self.file.read(1024*1024);self.eof=not text;self.buf+=text;self.maximum=max(self.maximum,len(self.buf))
    def char(self):
        while True:
            while self.pos<len(self.buf) and self.buf[self.pos].isspace():self.pos+=1
            if self.pos<len(self.buf):return self.buf[self.pos]
            if self.eof:return ''
            self.more()
    def take(self,c):
        if self.char()!=c:raise ValueError('UI index: expected '+c+' at byte '+str(self.offset()))
        self.pos+=1
    def value(self):
        self.char();start=self.offset()
        while True:
            try:
                value,end=self.decoder.raw_decode(self.buf,self.pos)
                if end==len(self.buf) and not self.eof:self.more();continue
                if end<len(self.buf) and self.buf[end] not in ' \t\r\n,]}:':raise ValueError('Invalid JSON delimiter')
                self.pos=end;return value,start,self.offset()-start
            except ValueError:
                if self.eof:raise
                self.more()
    def array(self):
        self.take('[')
        if self.char()==']':self.pos+=1;return
        while True:
            yield self.value()
            if self.char()==']':self.pos+=1;return
            self.take(',')
    def skip(self):
        """Discard an unused container lexically, without materializing it.

        Thermal meta may itself contain tens of MB of instance descriptions.
        Strings/escapes and nesting survive reader chunk boundaries.
        """
        if self.char() not in ['{','[']:self.value();return
        stack=[];quoted=False;tokens=re.compile(r'["\\{}\[\]]')
        while True:
            match=tokens.search(self.buf,self.pos)
            if match is None:
                self.pos=len(self.buf)
                if self.eof:raise ValueError('Unterminated skipped JSON container')
                self.more();continue
            char=match[0];self.pos=match.end()
            if quoted:
                if char=='\\':
                    if self.pos==len(self.buf):self.more()
                    if self.pos==len(self.buf):raise ValueError('Truncated JSON escape')
                    self.pos+=1
                elif char=='"':quoted=False
            elif char=='"':quoted=True
            elif char in '{[':stack.append(char)
            elif char in '}]':
                if not stack or stack.pop()!=('{' if char=='}' else '['):raise ValueError('Mismatched skipped JSON container')
                if not stack:return

def _connect(path,readonly=False):
    c=sqlite3.connect(path.as_uri()+'?mode=ro' if readonly else str(path),uri=readonly,timeout=30)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA cache_size=-8192');c.execute('PRAGMA temp_store=FILE')
    return c

class Store:
    def __init__(self,result,cache,reference=None):
        self.result=Path(result);self.reference=Path(reference) if reference and Path(reference).is_file() else None
        self.signature=[VERSION,fingerprint(self.result),fingerprint(self.reference) if self.reference else None]
        self.token=hashlib.sha256(json.dumps(self.signature,ensure_ascii=False).encode()).hexdigest()[:24]
        self.path=Path(cache)/(self.token+'.sqlite');self.path.parent.mkdir(parents=True,exist_ok=True)
    @contextmanager
    def db(self):
        if not self.path.is_file():raise ValueError('Index not ready')
        if self.signature!=[VERSION,fingerprint(self.result),fingerprint(self.reference) if self.reference else None]:raise ValueError('Result source changed; retry loading the run')
        with _query_slot:
            c=_connect(self.path,True)
            try:yield c
            finally:c.close()
    def status(self,retry=False):
        if self.path.is_file():return {'state':'ready','version':self.token}
        with _lock:
            job=_jobs.get(str(self.path))
            if retry and job and job['state']=='error':_jobs.pop(str(self.path));job=None
            if job is None:
                job={'state':'indexing','phase':'queued','nets':0,'pins':0,'resources':0,'processed_bytes':0,'total_bytes':self.result.stat().st_size,'started':time.monotonic()};_jobs[str(self.path)]=job
                threading.Thread(target=self._build,args=(job,),daemon=True).start()
            return {k:v for k,v in job.copy().items() if k!='started'}|{'elapsed_seconds':time.monotonic()-job['started'],'version':self.token}
    def _build(self,job):
        temp=self.path.with_name(self.path.name+'.'+uuid.uuid4().hex+'.building');c=None;r=None
        try:
            with _import_slot:
                if self.path.exists():return
                c=_connect(temp);c.execute('PRAGMA journal_mode=OFF');c.execute('PRAGMA synchronous=OFF')
                c.executescript('''
CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT);
CREATE TABLE nets(name TEXT PRIMARY KEY,ordinal INTEGER,cross_die INTEGER,pin_count INTEGER,off INTEGER,len INTEGER,tsv_a INTEGER,tsv_b INTEGER);
CREATE TABLE pins(id INTEGER PRIMARY KEY,net TEXT,name TEXT,x REAL,y REAL,layer INTEGER,node INTEGER);
CREATE VIRTUAL TABLE pin_space USING rtree(id,x1,x2,y1,y2);
CREATE TABLE resources(id INTEGER PRIMARY KEY,cap REAL,usage REAL,fixed REAL,overflow REAL,gx INTEGER,gy INTEGER,layer INTEGER);
CREATE TABLE grid(kind TEXT,idx INTEGER,value REAL,gx INTEGER,gy INTEGER,PRIMARY KEY(kind,idx));
CREATE TABLE instances(name TEXT PRIMARY KEY,off INTEGER,len INTEGER,x1 REAL,y1 REAL,x2 REAL,y2 REAL,bottom INTEGER,top INTEGER);
CREATE TABLE reference(name TEXT PRIMARY KEY,cross_die INTEGER,guide TEXT);
''')
                r=Cursor(self.result);r.take('{');root={};pin_id=0;counts={};max_net=0
                while r.char()!='}':
                    key,_,_=r.value();r.take(':');job['phase']=key
                    if key in ['nets','instances','resources','heat','thermal_node_counts','tsv_density']:
                        count=0
                        for value,off,length in r.array():
                            if key=='nets':
                                n=value;candidate=[e for e in n.get('edges',[]) if n.get('cross_die') and min(e)==n.get('tsv_node')];edge=max(candidate,key=lambda e:abs(e[1]-e[0]),default=[None,None])
                                c.execute('INSERT INTO nets VALUES(?,?,?,?,?,?,?,?)',(n['net'],count,int(n.get('cross_die',False)),len(n.get('pins',[])),off,length,min(edge) if edge[0] is not None else None,max(edge) if edge[0] is not None else None))
                                for p in n.get('pins',[]):
                                    pin_id+=1;c.execute('INSERT INTO pins VALUES(?,?,?,?,?,?,?)',(pin_id,n['net'],p['name'],p['x'],p['y'],p['layer'],p.get('node')));c.execute('INSERT INTO pin_space VALUES(?,?,?,?,?)',(pin_id,p['x'],p['x'],p['y'],p['y']))
                                max_net=max(max_net,length);job.update(nets=count+1,pins=pin_id)
                            elif key=='instances':
                                b=value['box'];c.execute('INSERT INTO instances VALUES(?,?,?,?,?,?,?,?,?)',(value['name'],off,length,*b,int(1 in value['tiers']),int(2 in value['tiers'])))
                            elif key=='resources':
                                c.execute('INSERT INTO resources(id,cap,usage,fixed,overflow) VALUES(?,?,?,?,?)',value);job['resources']=count+1
                            else:c.execute('INSERT INTO grid(kind,idx,value) VALUES(?,?,?)',(key,count,value))
                            count+=1
                            if count%1000==0:job['processed_bytes']=r.offset();c.commit()
                        counts[key]=count
                    else:root[key]=r.value()[0]
                    job['processed_bytes']=r.offset()
                    if r.char()=='}':break
                    r.take(',')
                r.take('}')
                if r.char():raise ValueError('Trailing result JSON content')
                job['phase']='SQL indexes';nx=len(root['x_lines'])-1;ny=len(root['y_lines'])-1
                if nx<1 or ny<1:raise ValueError('Invalid result grid')
                c.execute('UPDATE resources SET gx=id%?,gy=(id/? )%?,layer=id/?',(nx,nx,ny,nx*ny))
                c.execute('UPDATE grid SET gx=idx%?,gy=idx/?',(nx,nx))
                # An interface marker is valid only when the original Net had
                # this actual M10->M11 edge; tsv_node alone never creates one.
                c.execute('UPDATE nets SET tsv_a=NULL,tsv_b=NULL WHERE tsv_b-tsv_a!=? OR tsv_a/?!=10',(nx*ny,nx*ny))
                c.executescript('CREATE INDEX net_order ON nets(ordinal);CREATE INDEX net_cross ON nets(cross_die,ordinal);CREATE INDEX pin_net ON pins(net);CREATE INDEX resource_xy ON resources(gy,gx,layer);CREATE INDEX grid_xy ON grid(kind,gy,gx);')
                if self.reference:
                    job['phase']='reference Guide';name=None;guides=[]
                    with self.reference.open(encoding='utf8') as f:
                        for line in f:
                            line=line.strip()
                            if not line or line=='(':continue
                            if line==')':
                                c.execute('INSERT INTO reference VALUES(?,?,?)',(name,int(len({g[4]<=10 for g in guides})>1),json.dumps(guides,separators=(',',':'))));name=None;guides=[]
                            elif name is None:name=line
                            else:
                                a=line.split();layer=int(a[4].lower().removeprefix('metal').removeprefix('m'));guides.append([*map(int,a[:4]),layer])
                counts.update(pins=pin_id,max_net_bytes=max_net,reader_peak_chars=r.maximum)
                for key,val in [('root',root),('counts',counts),('signature',self.signature)]:c.execute('INSERT INTO meta VALUES(?,?)',(key,json.dumps(val,ensure_ascii=False,separators=(',',':'))))
                c.commit();c.close();c=None;r.close();r=None
                if self.signature!=[VERSION,fingerprint(self.result),fingerprint(self.reference) if self.reference else None]:raise ValueError('Result changed during indexing; retry loading')
                os.replace(temp,self.path);job.update(state='ready',phase='ready',processed_bytes=job['total_bytes'])
        except Exception as e:job.update(state='error',error=type(e).__name__+': '+str(e))
        finally:
            if c:c.close()
            if r:r.close()
            if temp.exists():temp.unlink()
    @staticmethod
    def meta(c,key):return json.loads(c.execute('SELECT v FROM meta WHERE k=?',(key,)).fetchone()[0])
    def summary(self,reference=False):
        with self.db() as c:
            root=self.meta(c,'root');counts=self.meta(c,'counts')
            if reference:
                if not self.reference:raise ValueError('No official reference Guide for this Case')
                root.update(source='official_reference_guide',metrics={k:None for k in root['metrics']},iterations=[])
                root['metrics']['nets']=c.execute('SELECT count(*) FROM reference').fetchone()[0];root['metrics']['official_status']='NOT_EVALUATED'
                counts['pins']=c.execute('SELECT count(*) FROM pins p JOIN reference n ON n.name=p.net').fetchone()[0]
            return root|{'nets':[],'instances':[],'resources':[],'heat':[],'thermal_node_counts':[],'tsv_density':[],'indexed':True,'version':self.token,'index_counts':counts}
    def _filter(self,q,alias='n'):
        reference=q.get('source')=='reference';table='reference' if reference else 'nets';where=['1'];params=[]
        if q.get('q'):where.append('instr(lower('+alias+'.name),?)>0');params.append(q['q'].lower())
        if q.get('cross')=='1':where.append(alias+'.cross_die=1')
        return table,' AND '.join(where),params
    def search(self,q):
        table,where,args=self._filter(q);offset=max(0,int(q.get('offset',0)));limit=min(500,max(1,int(q.get('limit',500))))
        with self.db() as c:
            total=c.execute('SELECT count(*) FROM '+table+' n WHERE '+where,args).fetchone()[0]
            rows=c.execute('SELECT n.name AS net,n.cross_die FROM '+table+' n WHERE '+where+' ORDER BY n.name LIMIT ? OFFSET ?',args+[limit,offset])
            return {'items':[dict(r) for r in rows],'total':total,'offset':offset,'limit':limit}
    def _net(self,c,name,reference=False):
        row=c.execute('SELECT off,len FROM nets WHERE name=?',(name,)).fetchone();n=None
        if row:
            with self.result.open('rb') as f:f.seek(row['off']);raw=f.read(row['len'])
            n=json.loads(raw)
        if reference:
            ref=c.execute('SELECT cross_die,guide FROM reference WHERE name=?',(name,)).fetchone()
            if not ref:raise ValueError('Unknown reference Net')
            return {'net':name,'cross_die':bool(ref['cross_die']),'pins':n.get('pins',[]) if n else [],'edges':[],'nodes':[],'guides':json.loads(ref['guide']),'tsv_count':None,'status':'REFERENCE'}
        if n is None:raise ValueError('Unknown Net')
        return n
    def net(self,name,reference=False):
        with self.db() as c:return self._net(c,name,reference)
    def geometry(self,q):
        table,where,args=self._filter(q);reference=q.get('source')=='reference';full=q.get('full')=='1';out=[];size=256
        with self.db() as c:
            if not full and reference:return {'nets':[]}
            if not full:where+=' AND n.tsv_a IS NOT NULL'
            for row in c.execute('SELECT n.* FROM '+table+' n WHERE '+where+' ORDER BY n.name LIMIT 150',args):
                if full:
                    n=self._net(c,row['name'],reference);n['pins']=[];n['nodes']=[]
                else:n={'net':row['name'],'cross_die':True,'tsv_node':row['tsv_a'],'edges':[[row['tsv_a'],row['tsv_b']]],'pins':[],'nodes':[],'guides':[]}
                size+=len(json.dumps(n,ensure_ascii=False).encode('utf8'))+2
                if size>4*1024*1024:break
                out.append(n)
        return {'nets':out,'limit':150,'byte_budget':4194304}
    def _pin_filter(self,q):
        table,where,args=self._filter(q);where='p.net=n.name AND '+where
        tier=q.get('tier','both');layer=q.get('layer','all')
        if tier=='bottom':where+=' AND p.layer<=10'
        if tier=='top':where+=' AND p.layer>=11'
        if layer!='all':where+=' AND p.layer=?';args.append(int(layer))
        if q.get('exclude'):where+=' AND p.net!=?';args.append(q['exclude'])
        return table,where,args
    def pins(self,q):
        with self.db() as c:
            root=self.meta(c,'root');die=root['die'];box=[float(x) for x in q.get('box',','.join(map(str,die))).split(',')]
            if len(box)!=4 or not all(math.isfinite(x) for x in box):raise ValueError('Invalid viewport')
            x1,y1,x2,y2=box;x2=max(x1+1e-9,x2);y2=max(y1+1e-9,y2)
            table,where,args=self._pin_filter(q)
            base=' FROM pin_space s JOIN pins p ON p.id=s.id JOIN '+table+' n ON p.net=n.name WHERE '+where+' AND s.x2>=? AND s.x1<=? AND s.y2>=? AND s.y1<=? AND p.x>=? AND p.x<=? AND p.y>=? AND p.y<=?'
            if q.get('cluster')=='1':base=base.replace('p.x<=?','p.x<?').replace('p.y<=?','p.y<?')
            args += [x1,x2,y1,y2,x1,x2,y1,y2]
            if q.get('detail')=='1':
                offset=max(0,int(q.get('offset',0)));total=c.execute('SELECT count(*)'+base,args).fetchone()[0]
                rows=c.execute('SELECT p.*,n.cross_die'+base+' ORDER BY p.id LIMIT 20 OFFSET ?',args+[offset])
                return {'items':[self.pin_row(r) for r in rows],'total':total,'offset':offset,'box':box}
            bins=24;dx=(x2-x1)/bins;dy=(y2-y1)/bins
            rows=c.execute('SELECT p.layer,CAST((p.x-?)/? AS INT) bx,CAST((p.y-?)/? AS INT) by,avg(p.x) x,avg(p.y) y,count(*) count,min(p.id) id'+base+' GROUP BY p.layer,bx,by',[x1,dx,y1,dy]+args)
            items=[];total=0
            for row in rows:
                total+=row['count']
                if row['count']==1:items.append(self.pin_row(c.execute('SELECT p.*,n.cross_die FROM pins p JOIN '+table+' n ON n.name=p.net WHERE p.id=?',(row['id'],)).fetchone()))
                else:
                    bx,by=row['bx'],row['by'];items.append({'cluster':True,'count':row['count'],'net':'','key':'cluster:'+str(row['layer'])+':'+str(bx)+':'+str(by),'p':{'x':row['x'],'y':row['y'],'layer':row['layer']},'box':[x1+bx*dx,y1+by*dy,x1+(bx+1)*dx,y1+(by+1)*dy]})
            return {'items':items,'total':total,'box':box,'bins':bins}
    @staticmethod
    def pin_row(r):return {'key':r['id'],'net':r['net'],'cross':bool(r['cross_die']),'p':{'name':r['name'],'x':r['x'],'y':r['y'],'layer':r['layer'],'node':r['node']}}
    def cell(self,gx,gy):
        with self.db() as c:return {'resources':[[r['id'],r['cap'],r['usage'],r['fixed'],r['overflow']] for r in c.execute('SELECT * FROM resources WHERE gx=? AND gy=?',(gx,gy))]}
    def tiles(self,q):
        with self.db() as c:
            root=self.meta(c,'root');xs,ys=root['x_lines'],root['y_lines'];nx=len(xs)-1
            import bisect
            box=[float(x) for x in q.get('box',','.join(map(str,root['die']))).split(',')]
            if len(box)!=4 or not all(math.isfinite(x) for x in box):raise ValueError('Invalid viewport')
            xa=max(0,bisect.bisect_right(xs,box[0])-1);xb=min(len(xs)-2,bisect.bisect_left(xs,box[2]));ya=max(0,bisect.bisect_right(ys,box[1])-1);yb=min(len(ys)-2,bisect.bisect_left(ys,box[3]))
            sx=max(1,math.ceil((xb-xa+1)/80));sy=max(1,math.ceil((yb-ya+1)/80));mode=q.get('view');tiles=[]
            if mode=='congestion':
                where='gy BETWEEN ? AND ? AND gx BETWEEN ? AND ?';args=[ya,yb,xa,xb]
                if q.get('tier')=='bottom':where+=' AND layer<=10'
                if q.get('tier')=='top':where+=' AND layer>=11'
                if q.get('layer','all')!='all':where+=' AND layer=?';args.append(int(q['layer']))
                rows=c.execute('SELECT gx/? bx,gy/? by,max((usage+fixed)/max(1,cap)) value FROM resources WHERE '+where+' GROUP BY bx,by',[sx,sy]+args)
            else:
                kind='heat' if mode=='thermal' else 'tsv_density';agg='avg' if mode=='thermal' else 'sum'
                rows=c.execute('SELECT gx/? bx,gy/? by,'+agg+'(value) value FROM grid WHERE kind=? AND gy BETWEEN ? AND ? AND gx BETWEEN ? AND ? GROUP BY bx,by',(sx,sy,kind,ya,yb,xa,xb))
            for r in rows:
                if r['value'] is None:continue
                x=int(r['bx'])*sx;y=int(r['by'])*sy;tiles.append({'box':[xs[x],ys[y],xs[min(x+sx,len(xs)-1)],ys[min(y+sy,len(ys)-1)]],'value':r['value']})
            return {'tiles':tiles,'aggregation':'max pressure' if mode=='congestion' else 'mean temperature' if mode=='thermal' else 'sum TSV','min':min((t['value'] for t in tiles),default=0),'max':max((t['value'] for t in tiles),default=0)}
    def instances(self,q):
        with self.db() as c:
            box=[float(x) for x in q.get('box',','.join(map(str,self.meta(c,'root')['die']))).split(',')]
            if len(box)!=4 or not all(math.isfinite(x) for x in box):raise ValueError('Invalid viewport')
            rows=c.execute('SELECT off,len FROM instances WHERE x2>=? AND x1<=? AND y2>=? AND y1<=? LIMIT 12000',(box[0],box[2],box[1],box[3]));items=[]
            with self.result.open('rb') as f:
                for row in rows:f.seek(row['off']);items.append(json.loads(f.read(row['len'])))
            return {'items':items,'limit':12000}
