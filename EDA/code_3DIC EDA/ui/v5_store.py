"""Read-only Guide projection for V5. Derived caches never become Router results."""
import hashlib,json,os,sys,threading,types,uuid
from pathlib import Path
from result_store import Store,fingerprint,_import_slot
from common import PROJECT,sha256

_jobs={};_lock=threading.Lock()
NOTICE='V5 Guide 只读视图：矩形来自真实 Guide；Pin 点为 DEF/LEF 各 RECT 中心（不是内部接入节点，计数为几何块数）。橙色 TSV 按原包 Guide 选择规则解析，柱中心仅用于示意，不代表合法性验证。内部 Route / Via / MST / Trace、资源占用和映射热图为 N/A；原始温度和评分单独读取。'

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
        if q.get('full')=='1' or q.get('source')=='reference':return super().geometry(q)
        table,where,args=self._filter(q);items=[];size=256
        with self.db() as c:
            for row in c.execute('SELECT name FROM nets n WHERE '+where+' AND n.cross_die=1 ORDER BY name LIMIT 150',args):
                n=self._net(c,row['name']);n.update(pins=[],nodes=[],guides=[])
                size+=len(json.dumps(n,ensure_ascii=False).encode('utf8'))+2
                if size>4194304:break
                items.append(n)
        return {'nets':items,'limit':150,'byte_budget':4194304}

class V5Store:
    def __init__(self,directory,cache,reference=None):
        self.directory=Path(directory);self.cache=Path(cache);self.reference=reference
        self.run=json.loads((self.directory/'run.json').read_text(encoding='utf8'))
        if self.run.get('engine_type')!='PYTHON_SCRIPT_ENGINE' or self.run.get('router_version')!='v5':raise ValueError('Not a V5 Python run')
        if self.run.get('status')!='ROUTED' or self.run.get('returncode')!=0:raise ValueError('V5 run is incomplete; no final Guide to display')
        self.inputs={k:Path(self.run['inputs'][k]).resolve() for k in ('def','lef','thermal')}
        if any(not p.is_relative_to(PROJECT.parent.resolve()) for p in self.inputs.values()):raise ValueError('V5 input outside EDA')
        self.guide=self.directory/'route.guide';self.receipt=self.directory/'route.run.json'
        self.sources=[self.directory/'run.json',self.guide,self.receipt,*self.inputs.values(),Path(__file__),PROJECT/'v5/original/source_manifest.json',PROJECT/'v5/engine/design.py',PROJECT/'v5/engine/guide_rules.py']
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
                job={'state':'indexing','phase':'V5 input verification','nets':0,'pins':0,'processed_bytes':0,'total_bytes':self.guide.stat().st_size};_jobs[self.token]=job
                threading.Thread(target=self.build,args=(job,),daemon=True).start()
            return dict(job,version=self.token)
    def build(self,job):
        temp=self.snapshot.with_name(self.snapshot.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            with _import_slot:
                receipt=json.loads(self.receipt.read_text(encoding='utf8'))
                if sha256(self.guide)!=self.run.get('guide_sha256'):raise ValueError('V5 Guide SHA256 mismatch')
                hashes={k:sha256(p) for k,p in self.inputs.items()}
                if hashes!=self.run.get('input_sha256') or hashes!=receipt.get('input_sha256'):raise ValueError('V5 input identity mismatch')
                if receipt.get('validation',{}).get('status')!='LOCAL_PASS':raise ValueError('V5 original receipt lacks LOCAL_PASS')
                job['phase']='V5 DEF/LEF geometry'
                design=parser_module('design.py').Design(self.inputs['def'],self.inputs['lef'])
                rules=parser_module('guide_rules.py');grid=rules.GCellGrid(design)
                nx=len(grid.x)-1;ny=len(grid.y)-1;density=[0]*(nx*ny);tsvs=0;ambiguous=0;seen=set();pin_count=0
                root={'schema_version':1,'source':'v5_guide_readonly','design':self.run['case'],'dbu_per_micron':design.dbu,'die':design.die,
                      'x_lines':grid.x,'y_lines':grid.y,'layers':[dict(v,number=k) for k,v in sorted(design.layers.items())],
                      'capabilities':{'route_edges':False,'via':False,'trace':False,'resources':False,'mapped_heat':False,'guide_tsv':True},
                      'visualization_notice':NOTICE,'iterations':[],'resources':[],'heat':[],'thermal_node_counts':[],'trace_truncated':False,
                      'warnings':[NOTICE],'ambient':None}
                with temp.open('x',encoding='utf8',newline='\n') as f:
                    f.write('{"nets":[');first=True
                    for name,rects in guide_nets(self.guide):
                        if name in seen or name not in design.nets:raise ValueError('Duplicate/unknown Guide Net '+name)
                        seen.add(name);pins=[];shapes=[]
                        for inst,pin in design.nets[name]:
                            for i,(layer,box) in enumerate(design.pin_shapes(inst,pin)):
                                shapes.append((layer,box));pins.append({'name':inst+'/'+pin+'#RECT'+str(i+1),'terminal':inst+'/'+pin,'x':(box[0]+box[2])/2,'y':(box[1]+box[3])/2,'layer':layer,'node':None,'box':box,'provenance':'DEF_LEF_RECT_CENTER'})
                        if any(g[4] not in design.layers for g in rects):raise ValueError('Unknown Guide layer for '+name)
                        cross=len({g[4]<=10 for g in rects})>1;markers=[];candidates=[];selection_error=None
                        if cross:
                            candidates=rules.tsv_candidates(rects)
                            try:
                                box=rules.select_tsv(rects,shapes);x=(box[0]+box[2])/2;y=(box[1]+box[3])/2
                                gx,gy=grid.cell(x,y);density[gy*nx+gx]+=1;tsvs+=1
                                markers=[{'x':x,'y':y,'box':box,'layers':[10,11],'provenance':'ORIGINAL_GUIDE_SELECTION_RULE','display_only_center':True}]
                            except ValueError as e:ambiguous+=1;selection_error=str(e)
                        net={'net':name,'pins':pins,'cross_die':cross,'guides':rects,'nodes':[],'edges':[],'tsv_node':None,'tsv_markers':markers,
                             'tsv_candidates':candidates,'tsv_selection_error':selection_error,'tsv_count':len(markers) if not selection_error else None,
                             'via_count':None,'wirelength_dbu_local':None,'route_edges_available':False,'status':'GUIDE_ONLY'}
                        if not first:f.write(',')
                        json.dump(net,f,ensure_ascii=False,separators=(',',':'));first=False;pin_count+=len(pins)
                        job.update(phase='V5 Guide rectangles',nets=len(seen),pins=pin_count)
                    if seen!=set(design.nets):raise ValueError('Guide missing '+str(len(set(design.nets)-seen))+' input Nets')
                    if receipt['validation'].get('nets')!=len(seen):raise ValueError('Guide/receipt Net count mismatch')
                    f.write('],"instances":[');first=True
                    for name,(macro,x,y,orientation) in design.components.items():
                        m=design.macros[macro];w,h=m.width*design.dbu,m.height*design.dbu
                        if orientation in ('W','E','FW','FE'):w,h=h,w
                        tiers=sorted({1 if l<=10 else 2 for shapes in m.pins.values() for l,_ in shapes})
                        item={'name':name,'macro':macro,'box':[x,y,x+w,y+h],'tiers':tiers}
                        if not first:f.write(',')
                        json.dump(item,f,separators=(',',':'));first=False
                    root['metrics']={'nets':len(seen),'routed_nets':len(seen),'tsv_count':None,'guide_tsv_count':tsvs,'overflow_local':None,'wirelength_dbu_local':None,'thermal_correlation_local':None,'runtime_seconds':self.run.get('runtime_seconds')}
                    root['tsv_density']=density;root['guide_tsv_ambiguous_nets']=ambiguous
                    if ambiguous:root['warnings'].append(str(ambiguous)+' Net TSV selection ambiguous: no fabricated marker; inspect candidates')
                    f.write(']')
                    for key,value in root.items():f.write(','+json.dumps(key)+':');json.dump(value,f,ensure_ascii=False,separators=(',',':'))
                    f.write('}');f.flush()
                self.check();os.replace(temp,self.snapshot);job['state']='ready'
        except Exception as e:job.update(state='error',error=type(e).__name__+': '+str(e))
        finally:
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
