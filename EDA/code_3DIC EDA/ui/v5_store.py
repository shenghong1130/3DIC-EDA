"""Read-only Guide projection for V5. Derived caches never become Router results."""
import hashlib,json,os,sys,threading,types,uuid
from pathlib import Path
from result_store import Store,fingerprint,_import_slot
from common import PROJECT,sha256
from v5_geometry import read_design,estimate_paths
from cache_policy import trim_cache

_jobs={};_lock=threading.Lock()
NOTICE='V5 原始 Guide + DEF/LEF RECT。圆点为几何中心，非 Router 接入点；推导路径 / 非 Router 真实 A* 仅作同层示意，不推断 Via 或跨层连接。橙色 TSV 是原始 Guide 规则解析，位置确定性不代表合法性验证。真实 Route / Node / MST / Trace 和内部资源为 N/A。'

def parser_module(name):
    path=PROJECT/'v5/engine'/name
    manifest=json.loads((PROJECT/'v5/original/source_manifest.json').read_text(encoding='utf8'))
    if sha256(path)!=manifest['files'][name]:raise ValueError('V5 read-only parser integrity mismatch: '+name)
    module=types.ModuleType('_ui_v5_'+path.stem);sys.modules[module.__name__]=module
    # Compile in memory: no imports from engine, no __pycache__ in protected source.
    exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__)
    return module

def guide_nets(path):
    name=None;opened=False;rects=[]
    with path.open(encoding='utf8') as f:
        for number,line in enumerate(f,1):
            line=line.strip()
            if not line:continue
            if name is None:
                if line in ('(',')'):raise ValueError('Guide missing Net at line '+str(number))
                name=line;continue
            if not opened:
                if line!='(':raise ValueError('Guide missing opening bracket at line '+str(number))
                opened=True;continue
            if line==')':
                if not rects:raise ValueError('Empty Guide Net '+name)
                yield name,rects;name=None;rects=[];opened=False;continue
            import re
            fields=line.split();m=re.fullmatch(r'(?:metal|m)([0-9]+)',fields[-1],re.I)
            if len(fields)!=5 or not m:raise ValueError('Invalid Guide line '+str(number))
            r=[*map(int,fields[:4]),int(m[1])]
            if r[0]>=r[2] or r[1]>=r[3] or not 1<=r[4]<=20:raise ValueError('Invalid Guide rectangle: '+line)
            rects.append(r)
    if name is not None:raise ValueError('Truncated Guide Net '+name)

class GuideIndex(Store):
    def geometry(self,q):
        if q.get('source')=='reference':return super().geometry(q)
        table,where,args=self._filter(q)
        with self.db() as c:
            root=self.meta(c,'root');box=[float(x) for x in q.get('box',','.join(map(str,root['die']))).split(',')]
            import math
            if len(box)!=4 or not all(map(math.isfinite,box)):raise ValueError('Invalid viewport')
            where+=' AND s.x2>=? AND s.x1<=? AND s.y2>=? AND s.y1<=?';args += [box[0],box[2],box[1],box[3]]
            if q.get('layer','all')!='all':where+=' AND g.layer=?';args.append(int(q['layer']))
            if q.get('tier')=='bottom':where+=' AND g.layer<=10'
            if q.get('tier')=='top':where+=' AND g.layer>=11'
            out={};truncated=False;size=256
            rows=c.execute('SELECT g.*,n.cross_die FROM guide_space s JOIN guides g ON g.id=s.id JOIN nets n ON n.name=g.net WHERE '+where+' LIMIT 12001',args)
            for i,r in enumerate(rows):
                if i==12000:truncated=True;break
                if r['net'] not in out:
                    if len(out)>=150:truncated=True;continue
                    n=self._net(c,r['net']);n.update(pins=[],nodes=[],guides=[],estimated_paths=None)
                    size+=len(json.dumps(n,ensure_ascii=False).encode('utf8'))+2
                    if size>4000000:truncated=True;break
                    out[r['net']]=n
                if q.get('full')=='1':
                    g=[r['x1'],r['y1'],r['x2'],r['y2'],r['layer']];size+=len(json.dumps(g).encode())+2
                    if size>4000000:truncated=True;break
                    out[r['net']]['guides'].append(g)
            return {'nets':list(out.values()),'limit':150,'rectangle_limit':12000,'byte_budget':4194304,'truncated':truncated,'viewport':box}

    def net(self,name,reference=False):
        n=super().net(name,reference)
        if not reference:
            with self.db() as c:layers={l['number']:l for l in self.meta(c,'root')['layers']}
            n['estimated_paths']=estimate_paths(n,layers)
        return n

    def cell(self,gx,gy):
        with self.db() as c:
            root=self.meta(c,'root');xs,ys=root['x_lines'],root['y_lines']
            if not 0<=gx<len(xs)-1 or not 0<=gy<len(ys)-1:raise ValueError('GCell outside grid')
            box=[xs[gx],ys[gy],xs[gx+1],ys[gy+1]]
            rows=c.execute('SELECT g.layer,count(*) rectangles,count(DISTINCT g.net) nets FROM guide_space s JOIN guides g ON g.id=s.id WHERE s.x2>? AND s.x1<? AND s.y2>? AND s.y1<? AND g.x2>? AND g.x1<? AND g.y2>? AND g.y1<? GROUP BY g.layer',(box[0],box[2],box[1],box[3])*2)
            return {'resources':[],'box':box,'coverage':[dict(r) for r in rows], 'source':'DEF GCELLGRID + original Guide positive-area intersection', 'unavailable_reason':'Guide 未记录 Router Node / Capacity / Usage / Fixed / Overflow；覆盖矩形数不是资源占用或官方拥塞分。'}

class V5Store:
    def __init__(self,directory,cache,reference=None):
        self.directory=Path(directory);self.cache=Path(cache);self.reference=reference
        self.run=json.loads((self.directory/'run.json').read_text(encoding='utf8'))
        if self.run.get('engine_type')!='PYTHON_SCRIPT_ENGINE' or self.run.get('router_version') not in ('v5','v5_trace'):raise ValueError('Not a V5 Python run')
        if self.run.get('status')!='ROUTED' or self.run.get('returncode')!=0:raise ValueError('V5 run is incomplete; no final Guide to display')
        self.inputs={k:Path(self.run['inputs'][k]).resolve() for k in ('def','lef','thermal')}
        if any(not p.is_relative_to(PROJECT.parent.resolve()) for p in self.inputs.values()):raise ValueError('V5 input outside EDA')
        self.guide=self.directory/'route.guide';self.receipt=self.directory/'route.run.json'
        self.sources=[self.directory/'run.json',self.guide,self.receipt,*self.inputs.values(),Path(__file__),PROJECT/'v5/original/source_manifest.json',PROJECT/'v5/engine/design.py',PROJECT/'v5/engine/guide_rules.py',Path(__file__).with_name('v5_geometry.py')]
        self.signature=self.sign();self.token=hashlib.sha256(json.dumps(self.signature).encode()).hexdigest()[:24]
        self.cache.mkdir(parents=True,exist_ok=True);self.snapshot=self.cache/('v5_guide_'+self.token+'.json')
    def sign(self):return [fingerprint(p) for p in self.sources]
    def check(self):
        if self.sign()!=self.signature:raise ValueError('V5 source changed; retry loading the run')
    def index(self):self.check();return GuideIndex(self.snapshot,self.cache,self.reference)
    def status(self,retry=False):
        self.check()
        if self.snapshot.exists():
            state=self.index().status(retry);state['version']=self.token;return state
        with _lock:
            job=_jobs.get(self.token)
            if retry and job and job['state']=='error':job=None
            if job is None:
                for key in list(_jobs):
                    if len(_jobs)<32:break
                    if _jobs[key]['state']!='indexing':_jobs.pop(key)
                job={'state':'indexing','phase':'V5 input verification','nets':0,'pins':0,'processed_bytes':0,'total_bytes':self.guide.stat().st_size};_jobs[self.token]=job
                threading.Thread(target=self.build,args=(job,),daemon=True).start()
            return dict(job,version=self.token)
    def build(self,job):
        design=None;db_path=self.cache/('def_'+self.token+'.'+uuid.uuid4().hex+'.tmp')
        temp=self.snapshot.with_name(self.snapshot.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            with _import_slot:
                receipt=json.loads(self.receipt.read_text(encoding='utf8'))
                if sha256(self.guide)!=self.run.get('guide_sha256'):raise ValueError('V5 Guide SHA256 mismatch')
                hashes={k:sha256(p) for k,p in self.inputs.items()}
                if hashes!=self.run.get('input_sha256') or hashes!=receipt.get('input_sha256'):raise ValueError('V5 input identity mismatch')
                if receipt.get('validation',{}).get('status')!='LOCAL_PASS':raise ValueError('V5 original receipt lacks LOCAL_PASS')
                job['phase']='V5 DEF/LEF geometry'
                design=read_design(parser_module('design.py'),self.inputs['def'],self.inputs['lef'],db_path)
                rules=parser_module('guide_rules.py');grid=rules.GCellGrid(design)
                nx=len(grid.x)-1;ny=len(grid.y)-1;density=[0]*(nx*ny);tsvs=0;ambiguous=0;net_count=0;pin_count=0;terminal_count=0
                root={'schema_version':1,'source':'v5_guide_readonly','design':self.run['case'],'dbu_per_micron':design.dbu,'die':design.die,
                      'x_lines':grid.x,'y_lines':grid.y,'layers':[dict(v,number=k) for k,v in sorted(design.layers.items())],
                      'capabilities':{'route_edges':False,'via':False,'trace':False,'resources':False,'mapped_heat':False,'guide_tsv':True},
                      'visualization_notice':NOTICE,'iterations':[],'resources':[],'heat':[],'thermal_node_counts':[],'trace_truncated':False,
                      'warnings':[NOTICE],'ambient':None}
                with temp.open('x',encoding='utf8',newline='\n') as f:
                    f.write('{"nets":[');first=True
                    for name,rects in guide_nets(self.guide):
                        if name not in design.nets:raise ValueError('Unknown Guide Net '+name)
                        try:design.ui_db.execute('INSERT INTO seen VALUES(?)',(name,))
                        except Exception:raise ValueError('Duplicate Guide Net '+name)
                        net_count+=1;pins=[];shapes=[]
                        for inst,pin in design.nets[name]:
                            for i,(layer,box) in enumerate(design.pin_shapes(inst,pin)):
                                shapes.append((layer,box));pins.append({'name':inst+'/'+pin+'#RECT'+str(i+1),'terminal':inst+'/'+pin,'x':(box[0]+box[2])/2,'y':(box[1]+box[3])/2,'layer':layer,'node':None,'box':box,'die':1 if layer<=10 else 2,'rect_index':i+1,'access_point':None,'provenance':'DEF_LEF_RECT_CENTER'})
                        if any(g[4] not in design.layers for g in rects):raise ValueError('Unknown Guide layer for '+name)
                        cross=len({g[4]<=10 for g in rects})>1;markers=[];candidates=[];selection_error=None
                        if cross:
                            candidates=rules.tsv_candidates(rects)
                            try:
                                box=rules.select_tsv(rects,shapes);x=(box[0]+box[2])/2;y=(box[1]+box[3])/2
                                gx,gy=grid.cell(x,y);density[gy*nx+gx]+=1;tsvs+=1
                                markers=[{'x':x,'y':y,'box':box,'layers':[10,11],'provenance':'ORIGINAL_GUIDE_SELECTION_RULE','display_only_center':True,'position_status':'GUIDE_RULE_SELECTED','legality':'NOT_VALIDATED'}]
                            except ValueError as e:ambiguous+=1;selection_error=str(e)
                        net={'net':name,'terminal_count':len(set(p['terminal'] for p in pins)),'pins':pins,'cross_die':cross,'guides':rects,'nodes':[],'edges':[],'tsv_node':None,'tsv_markers':markers,
                             'tsv_position_status':'GUIDE_RULE_SELECTED' if markers else 'CANDIDATES_ONLY' if candidates else 'UNRESOLVED','tsv_legality':'NOT_VALIDATED','tsv_candidates':candidates,'tsv_selection_error':selection_error,'tsv_count':len(markers) if not selection_error else None,
                             'via_count':None,'wirelength_dbu_local':None,'route_edges_available':False,'status':'GUIDE_ONLY'}
                        if not first:f.write(',')
                        json.dump(net,f,ensure_ascii=False,separators=(',',':'));first=False;pin_count+=len(pins)
                        terminal_count+=net['terminal_count'];job.update(phase='V5 Guide rectangles',nets=net_count,pins=pin_count)
                    if net_count!=len(design.nets):raise ValueError('Guide missing input Nets')
                    if receipt['validation'].get('nets')!=net_count:raise ValueError('Guide/receipt Net count mismatch')
                    f.write('],"instances":[');first=True
                    for name,(macro,x,y,orientation) in design.components.items():
                        m=design.macros[macro];w,h=m.width*design.dbu,m.height*design.dbu
                        if orientation in ('W','E','FW','FE'):w,h=h,w
                        tiers=sorted({1 if l<=10 else 2 for shapes in m.pins.values() for l,_ in shapes})
                        item={'name':name,'macro':macro,'box':[x,y,x+w,y+h],'tiers':tiers}
                        if not first:f.write(',')
                        json.dump(item,f,separators=(',',':'));first=False
                    root['metrics']={'nets':net_count,'routed_nets':net_count,'tsv_count':None,'guide_tsv_count':tsvs,'overflow_local':None,'wirelength_dbu_local':None,'thermal_correlation_local':None,'runtime_seconds':self.run.get('runtime_seconds')}
                    root['pin_terminal_count']=terminal_count;root['pin_rect_count']=pin_count;root['tsv_density']=density;root['guide_tsv_ambiguous_nets']=ambiguous
                    if ambiguous:root['warnings'].append(str(ambiguous)+' Net TSV selection ambiguous: no fabricated marker; inspect candidates')
                    f.write(']')
                    for key,value in root.items():f.write(','+json.dumps(key)+':');json.dump(value,f,ensure_ascii=False,separators=(',',':'))
                    f.write('}');f.flush()
                self.check();os.replace(temp,self.snapshot);trim_cache(self.cache,keep=[self.snapshot]);job['state']='ready'
        except Exception as e:job.update(state='error',error=type(e).__name__+': '+str(e))
        finally:
            if design is not None:design.ui_db.close()
            if db_path.exists():db_path.unlink()
            if temp.exists():temp.unlink()
    def summary(self,reference=False):
        result=self.index().summary(reference);result['version']=self.token
        if reference:
            result['capabilities']['guide_tsv']=False
            result['visualization_notice']='官方参考 Guide 区域；Pin 为 V5 输入 DEF/LEF 的 RECT 中心。内部 Route / Via / TSV / MST / Trace 和资源占用为 N/A，不从参考矩形推断。'
        return result
    def __getattr__(self,name):
        if name in ('net','search','pins','geometry','cell','tiles','instances'):
            return lambda *a,**kw:getattr(self.index(),name)(*a,**kw)
        raise AttributeError(name)
