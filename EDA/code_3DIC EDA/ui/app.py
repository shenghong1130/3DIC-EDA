#!/usr/bin/env python3
"""Offline local visualizer. Python standard library + browser Canvas; no pip packages."""
from pathlib import Path
import argparse,json,sys,re,copy,threading,subprocess,time
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import PROJECT,OUTPUTS,catalog,run_dir,safe_label
WEB=Path(__file__).resolve().parent/'static'
def load(path):return json.loads(Path(path).read_text(encoding='utf8'))
def runs():
    out={}
    for p in sorted((OUTPUTS/'runs').glob('*/*/run.json')):
        r=load(p);key=r['case']+'/'+r['label'];out[key]={'id':key,'case':r['case'],'label':r['label'],'status':r['status'],'official_status':r.get('official_status','NOT_EVALUATED'),'runtime_seconds':r.get('runtime_seconds'),'peak_memory_bytes':r.get('peak_memory_bytes')}
    return out
def selected(run):
    if run not in runs():raise ValueError('Unknown run')
    case,label=run.split('/');return run_dir(case,label)
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
    d=selected(key);r=load(d/'result.json');r['run']=load(d/'run.json')
    for engine in ['latest','legacy']:
        p=d/('official_'+engine)/'invocation.json'
        if p.exists():r['official_'+engine]=load(p)
        else:
            report=d/('official_'+engine)/'evaluation_report.json'
            if report.exists():r['official_'+engine]={'status':'REPORT_'+load(report).get('status','UNKNOWN')+' / invocation incomplete','report':load(report),'warning':'官方报告存在，但本地调用元数据未完整写出；不能据此证明进程与资源验证完成。'}
    r['router_log']=(d/'router.log').read_text(encoding='utf8',errors='replace')[-30000:]
    return r
class Handler(BaseHTTPRequestHandler):
    def send(self,data,status=200,mime='application/json; charset=utf-8'):
        if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode('utf8')
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
    def do_GET(self):
        try:
            u=urlparse(self.path);q=parse_qs(u.query);get=lambda k:q.get(k,[''])[0]
            if u.path=='/api/catalog':return self.send({'cases':list(catalog().values()),'runs':list(runs().values())})
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
                nodes=load(p)['nodes'];stride=max(1,len(nodes)//10000);return self.send({'nodes':nodes[::stride],'total_nodes':len(nodes),'stride':stride})
            if u.path=='/api/reference':
                case=get('case');c=catalog().get(case)
                if not c or not c['reference']:raise ValueError('No official reference Guide')
                matches=[r['id'] for r in runs().values() if r['case']==case and r['status']=='ROUTED']
                if not matches:raise ValueError('请先运行该用例，以取得真实引脚、实例和网格信息。')
                d=run_data(matches[-1]);metadata={n['net']:n for n in d['nets']};d['nets']=[{'net':n,'guides':gs,'pins':metadata.get(n,{}).get('pins',[]),'cross_die':len({g[4]<=10 for g in gs})>1,'edges':[],'nodes':[],'tsv_count':None,'status':'REFERENCE'} for n,gs in guide(c['reference']).items()]
                d['source']='official_reference_guide';d['resources']=[];d['metrics']={k:None for k in d['metrics']};d['metrics']['nets']=len(d['nets']);d['metrics']['official_status']='NOT_EVALUATED';d['iterations']=[];d['tsv_density']=[];d['router_log']='官方参考 Guide，仅用于展示与对比。没有内部搜索路径与过程记录。';d.pop('official_latest',None);d.pop('official_legacy',None);d['reference_path']=c['reference'];return self.send(d)
            names={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/layers3d.js':'layers3d.js'}
            if u.path in names:
                file=WEB/names[u.path];mime={'.html':'text/html','.js':'text/javascript','.css':'text/css'}[file.suffix];return self.send(file.read_bytes(),mime=mime+'; charset=utf-8')
            self.send({'error':'Not found'},404)
        except (ValueError,OSError,KeyError) as e:self.send({'error':str(e)},400)
    def log_message(self,fmt,*args):print(fmt%args)
def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8501);a=p.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler);print(f'3DIC Global Routing Visualizer: http://127.0.0.1:{a.port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:server.server_close()
if __name__=='__main__':main()
