#!/usr/bin/env python3
import argparse,json,os,sys,shutil
from pathlib import Path
from common import *
def main():
    p=argparse.ArgumentParser(description='Run unmodified official evaluators. Latest ELF is recommended.')
    p.add_argument('--case',required=True);p.add_argument('--label',default='default');p.add_argument('--engine',choices=['latest','legacy'],default='latest');p.add_argument('--backend',choices=['native','wsl'],default='wsl' if os.name=='nt' else 'native');p.add_argument('--timeout',type=float,default=600)
    a=p.parse_args();target=run_dir(a.case,a.label);manifest=target/'run.json'
    if not manifest.exists():p.error('Run not found')
    run=json.loads(manifest.read_text(encoding='utf8'))
    if run['status']!='ROUTED':p.error('Router failed; no complete guide to evaluate')
    report_dir=target/('official_'+a.engine)
    if report_dir.exists():p.error('Evaluation already exists; keep the original report and use a new run label')
    report_dir.mkdir();inputs={k:Path(v) for k,v in run['inputs'].items()};conv=linux_path if a.backend=='wsl' else str
    for key,path in inputs.items():
        if sha256(path)!=run['input_sha256'][key]:p.error('Input changed since routing: '+key)
    # The official tool writes caches beside DEF and a log into cwd. Evaluate
    # byte-identical copies so these side effects never touch official folders.
    input_dir=report_dir/'inputs';input_dir.mkdir()
    copies={}
    for key,path in inputs.items():
        dest=input_dir/path.name;shutil.copyfile(path,dest);copies[key]=dest
    tool_dir=EDA/'评估程序' if a.engine=='latest' else EDA/'eda-elite-challenge/eda-elite-challenge2026/evaluator'
    if a.engine=='latest':
        cmd=[conv(tool_dir/'score')];env={}
        for name in ['POST9.dat','POWV9.dat']:shutil.copyfile(tool_dir/name,report_dir/name)
    else:
        cmd=['python3',conv(tool_dir/'score.py')];env={'LD_LIBRARY_PATH':conv(tool_dir),'PYTHONPATH':conv(tool_dir),'PYTHONDONTWRITEBYTECODE':'1'}
    cwd=report_dir
    cmd += [conv(Path(run['guide'])),'--lefs',conv(copies['lef']),'--input-def',conv(copies['def']),'--thermal-json',conv(copies['thermal']),'--output-dir',conv(report_dir)]
    if os.name=='nt' and a.backend=='native':
        status={'status':'NOT_TESTED','failure_reason':'Official ELF and CPython 3.10 Linux .so require WSL/Linux. Use --backend wsl.'};save(report_dir/'invocation.json',status);print(status);return 2
    cmd=backend_command(cmd,a.backend,cwd=cwd,time_file=report_dir/'resource_usage.txt',env=env,timeout=a.timeout)
    native_env=os.environ.copy();native_env.update(env)
    result=timed_process(cmd,report_dir/'evaluator.log',a.timeout+30,cwd=cwd if a.backend=='native' else None,env=native_env if a.backend=='native' else None)
    report=report_dir/'evaluation_report.json';data=json.loads(report.read_text(encoding='utf8')) if report.exists() else {}
    result['status']='PASS' if data.get('status')=='PASS' and result['returncode']==0 else 'FAIL'
    result['engine']=a.engine;result['evaluator_sha256']=sha256(tool_dir/('score' if a.engine=='latest' else 'score.py'));result['peak_memory_bytes']=peak_rss(report_dir/'resource_usage.txt');result['report']=data
    result['evaluated_input_sha256']={key:sha256(path) for key,path in copies.items()}
    result['fidelity']='Official provided evaluator; GRT guide metrics / mock-DRT proxy; no detailed-routing signoff.'
    if result['status']=='FAIL':result['failure_reason']=(report_dir/'evaluator.log').read_text(encoding='utf8',errors='replace')[-8000:]
    save(report_dir/'invocation.json',result)
    if a.engine=='latest':run['official_status']=result['status'];save(manifest,run)
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
