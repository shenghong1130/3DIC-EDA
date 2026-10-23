#!/usr/bin/env python3
import argparse,json,os,sys,time
from pathlib import Path
from common import *
def main():
    p=argparse.ArgumentParser(description='Run independent C++ router; results are confined to outputs/.')
    p.add_argument('--case',choices=sorted(catalog()));p.add_argument('--def',dest='def_file',type=Path);p.add_argument('--lef',type=Path);p.add_argument('--thermal-report',type=Path)
    p.add_argument('--output',type=Path);p.add_argument('--label',default='default');p.add_argument('--backend',choices=['native','wsl'],default='native');p.add_argument('--router',type=Path)
    p.add_argument('--trace',action='store_true');p.add_argument('--config',type=Path,default=PROJECT/'configs/default.json');p.add_argument('--time-limit',type=float,default=600);p.add_argument('--list',action='store_true')
    a=p.parse_args()
    if a.list:print(json.dumps(catalog(),ensure_ascii=False,indent=2));return 0
    c=catalog().get(a.case,{})
    paths=[a.def_file or c.get('def'),a.lef or c.get('lef'),a.thermal_report or c.get('thermal')]
    if any(x is None for x in paths):p.error('Choose --case or supply --def, --lef, --thermal-report')
    paths=[Path(x).resolve() for x in paths]
    if any(not x.is_file() for x in paths):p.error('Missing input')
    if a.time_limit<=0:p.error('time-limit must be positive')
    case=a.case or 'custom';target=run_dir(case,a.label);target.mkdir(parents=True,exist_ok=True)
    output=(a.output or target/'route.guide').resolve()
    if not output.is_relative_to(OUTPUTS.resolve()):p.error('Local helper outputs must be under project outputs/')
    # Immutable run labels keep earlier comparison records and failures reviewable.
    if (target/'run.json').exists() or output.exists():p.error('Run/output already exists; use a new --label')
    config=a.config.resolve();cfg=json.loads(config.read_text(encoding='utf8'))
    if c.get('warning'):print(c['warning'],file=sys.stderr)
    exe=a.router or (PROJECT/'router' if a.backend=='wsl' or os.name!='nt' else OUTPUTS/'router.exe')
    exe=exe.resolve();conv=linux_path if a.backend=='wsl' else lambda x:str(Path(x).resolve())
    args=[conv(exe),'--def',conv(paths[0]),'--lef',conv(paths[1]),'--thermal-report',conv(paths[2]),'--output',conv(output),'--metrics',conv(target/'result.json'),'--config',conv(config),'--time-limit',str(a.time_limit)]
    if a.trace:args+=['--trace',conv(target/'trace.jsonl')]
    cmd=backend_command(args,a.backend,time_file=target/'resource_usage.txt',timeout=a.time_limit+10)
    started=time.strftime('%Y-%m-%dT%H:%M:%S%z');result=timed_process(cmd,target/'router.log',a.time_limit+45)
    result.update({'schema_version':1,'case':case,'dataset':c.get('dataset','custom'),'label':a.label,'started_at':started,'backend':a.backend,'inputs':dict(zip(['def','lef','thermal'],map(str,paths))),'input_sha256':dict(zip(['def','lef','thermal'],map(sha256,paths))),'config':cfg,'guide':str(output),'trace':str(target/'trace.jsonl') if a.trace else None,'peak_memory_bytes':peak_rss(target/'resource_usage.txt'),'official_status':'NOT_EVALUATED','command':cmd})
    result['router_sha256']=sha256(exe)
    result['source_sha256']={str(f.relative_to(PROJECT)):sha256(f) for folder in ['src','include'] for f in (PROJECT/folder).glob('*') if f.is_file()}
    result['status']='ROUTED' if result['returncode']==0 and output.exists() and (target/'result.json').exists() else 'FAIL'
    if result['status']=='FAIL':result['failure_reason']=(target/'router.log').read_text(encoding='utf8',errors='replace')[-6000:]
    save(target/'run.json',result);print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['status']=='ROUTED' else 1
if __name__=='__main__':raise SystemExit(main())
