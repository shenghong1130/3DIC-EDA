#!/usr/bin/env python3
"""Offline local visualizer. Python standard library + browser Canvas; no pip packages."""
from pathlib import Path
import argparse,json,sys,re,copy,threading,subprocess,time,os
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import PROJECT,OUTPUTS,catalog,run_dir,safe_label
from result_store import Store,Cursor,LARGE_BYTES,thermal_sample
WEB=Path(__file__).resolve().parent/'static'
CACHE=Path(os.environ.get('GR_UI_CACHE_DIR',str(OUTPUTS/'ui_cache')))

def tail_text(path,limit=30000):
    try:
        with Path(path).open('rb') as f:f.seek(0,2);f.seek(max(0,f.tell()-limit));return f.read(limit).decode('utf8',errors='replace')
    except FileNotFoundError:return ''

def attach_reports(r,d):
    r['run']=load(d/'run.json')
    for engine in ['latest','legacy']:
        p=d/('official_'+engine)/'invocation.json'
        if p.exists():r['official_'+engine]=load(p)
        else:
            report=d/('official_'+engine)/'evaluation_report.json'
            if report.exists():r['official_'+engine]={'status':'REPORT_'+load(report).get('status','UNKNOWN')+' / invocation incomplete','report':load(report),'warning':'官方报告存在，但本地调用元数据未完整写出；不能据此证明进程与资源验证完成。'}
    # Scoring no longer rewrites immutable Router manifests.
    if 'official_latest' in r:r['run']['official_status']=r['official_latest'].get('status','NOT_EVALUATED')
    r['router_log']=tail_text(d/'router.log');return r

def store(key):
    d=selected(key);case=key.split('/')[0];return Store(d/'result.json',CACHE,catalog().get(case,{}).get('reference'))
def load(path):return json.loads(Path(path).read_text(encoding='utf8'))
def runs():
    out={}
    for p in sorted((OUTPUTS/'runs').glob('*/*/run.json')):
        r=load(p);inv=p.parent/'official_latest/invocation.json'
        if inv.is_file():r['official_status']=load(inv).get('status','NOT_EVALUATED')
        key=r['case']+'/'+r['label'];result=p.parent/'result.json';out[key]={'id':key,'case':r['case'],'label':r['label'],'status':r['status'],'official_status':r.get('official_status','NOT_EVALUATED'),'runtime_seconds':r.get('runtime_seconds'),'peak_memory_bytes':r.get('peak_memory_bytes'),'result_bytes':result.stat().st_size if result.is_file() else None,'indexed':result.is_file() and result.stat().st_size>LARGE_BYTES}
    return out
def selected(run):
    if len(run.split('/'))!=2:raise ValueError('Unknown run')
    case,label=run.split('/');d=run_dir(case,label);r=load(d/'run.json')
    if r.get('case')!=case or r.get('label')!=label:raise ValueError('Run manifest identity mismatch')
    return d
def guide(path):
    nets={};name=None;opened=False
    for line in Path(path).read_text(encoding='utf8').splitlines():
        line=line.strip()
        if not line:continue
        if line=='(':opened=True;continue
        if line==')':name=None;opened=False;continue
        if not opened:name=line;nets[name]=[];continue
        a=line.split()
        if len(a)!=5:raise ValueError('Bad Guide rectangle')
        m=re.fullmatch(r'(?:metal|m)(\d+)',a[4],re.I)
        if not m:raise ValueError('Unknown Guide layer')
        nets[name].append([*map(int,a[:4]),int(m[1])])
    return nets
def run_data(key):
    d=selected(key);return attach_reports(load(d/'result.json'),d)
class Handler(BaseHTTPRequestHandler):
    def send(self,data,status=200,mime='application/json; charset=utf-8'):
        if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode('utf8')
        try:
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    def do_GET(self):
        try:
            u=urlparse(self.path);q=parse_qs(u.query);get=lambda k:q.get(k,[''])[0]
            if u.path=='/api/catalog':return self.send({'cases':list(catalog().values()),'runs':list(runs().values())})
            indexed_paths={'/api/summary','/api/nets','/api/net','/api/pins','/api/geometry','/api/tiles','/api/cell','/api/instances'}
            if u.path in indexed_paths or u.path=='/api/run' and (selected(get('id'))/'result.json').stat().st_size>LARGE_BYTES:
                s=store(get('id'));state=s.status(get('retry')=='1')
                if state['state']=='error':return self.send({'error':state['error'],'error_kind':'index_error','index':state},500)
                if state['state']!='ready':return self.send(state,202)
                if get('version') and get('version')!=s.token:return self.send({'error':'结果文件已变化，请重试加载。','error_kind':'source_changed'},409)
                query={k:v[0] for k,v in q.items()};ref=get('source')=='reference'
                if u.path in ['/api/summary','/api/run']:
                    d=selected(get('id'));data=attach_reports(s.summary(ref),d)
                    if ref:
                        data.pop('official_latest',None);data.pop('official_legacy',None);data['router_log']='官方参考 Guide；无内部路径或过程记录。'
                    return self.send(data)
                if u.path=='/api/nets':return self.send(s.search(query))
                if u.path=='/api/net':return self.send(s.net(get('net'),ref))
                if u.path=='/api/pins':return self.send(s.pins(query))
                if u.path=='/api/geometry':return self.send(s.geometry(query))
                if u.path=='/api/tiles':return self.send({'tiles':[]} if ref and get('view')!='thermal' else s.tiles(query))
                if u.path=='/api/cell':return self.send({'resources':[]} if ref else s.cell(int(get('gx')),int(get('gy'))))
                if u.path=='/api/instances':return self.send(s.instances(query))
            if u.path=='/api/run':return self.send(run_data(get('id')))
            if u.path=='/api/trace':
                p=selected(get('id'))/'trace.jsonl';scope=get('net')
                large=p.exists() and p.stat().st_size>4*1024*1024
                if large and not scope:return self.send({'events':[],'requires_net':True})
                lines=[];matched=0
                if p.exists():
                    # Keep serialized event lines; avoid decoding an entire large
                    # Trace and serializing a second in-memory copy.
                    with p.open('rb') as f:
                        for line in f:
                            if not line.strip():continue
                            if scope:
                                event=json.loads(line)
                                if event.get('net') not in [None,scope]:continue
                                matched+=event.get('net')==scope
                            lines.append(line.strip())
                body=b'{"requires_net":false,"scoped":'+(b'true' if scope else b'false')+b',"matched_events":'+str(matched).encode()+b',"events":['+b','.join(lines)+b']}'
                return self.send(body)
            if u.path=='/api/thermal':
                run=load(selected(get('id'))/'run.json');p=Path(run['inputs']['thermal']).resolve()
                if not p.is_relative_to(PROJECT.parent.resolve()):raise ValueError('Thermal input outside EDA')
                return self.send(thermal_sample(p,CACHE))
            if u.path=='/api/reference':
                case=get('case');c=catalog().get(case)
                if not c or not c['reference']:raise ValueError('No official reference Guide')
                matches=[r['id'] for r in runs().values() if r['case']==case and r['status']=='ROUTED']
                if not matches:raise ValueError('请先运行该用例，以取得真实引脚、实例和网格信息。')
                d=run_data(matches[-1]);metadata={n['net']:n for n in d['nets']};d['nets']=[{'net':n,'guides':gs,'pins':metadata.get(n,{}).get('pins',[]),'cross_die':len({g[4]<=10 for g in gs})>1,'edges':[],'nodes':[],'tsv_count':None,'status':'REFERENCE'} for n,gs in guide(c['reference']).items()]
                d['source']='official_reference_guide';d['resources']=[];d['metrics']={k:None for k in d['metrics']};d['metrics']['nets']=len(d['nets']);d['metrics']['official_status']='NOT_EVALUATED';d['iterations']=[];d['tsv_density']=[];d['router_log']='官方参考 Guide，仅用于展示与对比。没有内部搜索路径与过程记录。';d.pop('official_latest',None);d.pop('official_legacy',None);d['reference_path']=c['reference'];return self.send(d)
            names={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/layers3d.js':'layers3d.js','/remote.js':'remote.js'}
            if u.path in names:
                file=WEB/names[u.path];mime={'.html':'text/html','.js':'text/javascript','.css':'text/css'}[file.suffix];return self.send(file.read_bytes(),mime=mime+'; charset=utf-8')
            self.send({'error':'Not found'},404)
        except (BrokenPipeError,ConnectionResetError):pass # Normal when the user aborts an old selection.
        except (ValueError,OSError,KeyError) as e:self.send({'error':type(e).__name__+': '+str(e),'error_kind':'server_error'},400)
        except Exception as e:self.send({'error':type(e).__name__+': '+str(e),'error_kind':'server_error'},500)
    def log_message(self,fmt,*args):print(fmt%args)
def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8501);a=p.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler);print(f'3DIC Global Routing Visualizer: http://127.0.0.1:{a.port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:server.server_close()
if __name__=='__main__':main()
