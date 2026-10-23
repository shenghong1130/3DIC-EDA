"""Local paths and process helpers; no official input is ever written."""
from pathlib import Path
import json,os,re,shlex,subprocess,time,hashlib,sys
if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
if hasattr(sys.stderr,'reconfigure'):sys.stderr.reconfigure(encoding='utf-8',errors='replace')
os.environ['PYTHONUTF8']='1'
PROJECT=Path(__file__).resolve().parents[1]
EDA=PROJECT.parent
OUTPUTS=PROJECT/'outputs'
def catalog():
    cases={}
    for dataset,base in [('公开测试用例',EDA/'公开测试用例'),('公开数据集',EDA/'赛题八公开数据集/赛题八公开数据集/cases'),('旧版工具样例',EDA/'eda-elite-challenge/eda-elite-challenge2026/cases')]:
        if not base.exists():continue
        for d in sorted(base.glob('*/*.input.def')):
            stem=d.name.removesuffix('.input.def');key=stem if dataset!='旧版工具样例' else stem+'_legacy'
            lef=d.with_suffix('.lef');thermal=d.with_name(stem+'.thermal.input.json')
            candidates=sorted(d.parent.glob('*.thermal*.json'))
            warning=None
            if not thermal.exists() and len(candidates)==1:
                thermal=candidates[0];warning='热文件名称与用例不同；保留原始名称，运行前需核对来源。'
            if lef.exists() and thermal.exists():
                cases[key]={'id':key,'name':stem,'dataset':dataset,'def':str(d),'lef':str(lef),'thermal':str(thermal),'reference':str(d.with_name(stem+'.output.guide')) if d.with_name(stem+'.output.guide').exists() else None,'warning':warning}
    return cases
def safe_label(value):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',value) or value in {'.','..'}:raise ValueError('Label must contain ASCII letters/numbers/_.- only')
    return value
def run_dir(case,label):return OUTPUTS/'runs'/safe_label(case)/safe_label(label)
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def linux_path(path):
    p=Path(path).resolve();text=p.as_posix()
    if os.name=='nt':return '/mnt/'+text[0].lower()+text[2:]
    return text
def backend_command(args,backend,cwd=None,time_file=None,env=None,timeout=None):
    if backend=='wsl':
        command=' '.join(shlex.quote(str(x)) for x in args)
        if time_file:command='/usr/bin/time -v -o '+shlex.quote(linux_path(time_file))+' '+command
        if timeout:command='timeout --signal=TERM --kill-after=5s '+shlex.quote(str(timeout)+'s')+' '+command
        if env:command='env '+' '.join(shlex.quote(k+'='+v) for k,v in env.items())+' '+command
        if cwd:command='cd '+shlex.quote(linux_path(cwd))+' && '+command
        return ['wsl','-d','Ubuntu','--','bash','-lc',command]
    if time_file and os.name!='nt':args=['/usr/bin/time','-v','-o',str(time_file),*args]
    if timeout and os.name!='nt':args=['timeout','--signal=TERM','--kill-after=5s',str(timeout)+'s',*args]
    return list(map(str,args))
def timed_process(args,log,timeout,cwd=None,env=None):
    log=Path(log);log.parent.mkdir(parents=True,exist_ok=True);start=time.perf_counter();status='FINISHED';code=None
    # A log file avoids unbounded in-memory subprocess output on large cases.
    with log.open('wb') as out:
        try:
            proc=subprocess.Popen(list(map(str,args)),cwd=cwd,stdout=out,stderr=subprocess.STDOUT,env=env)
            try:code=proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                status='TIMEOUT';proc.kill();proc.wait()
        except OSError as e:out.write(str(e).encode('utf-8'));status='LAUNCH_ERROR'
    return {'process_status':status,'returncode':code,'runtime_seconds':time.perf_counter()-start}
def peak_rss(path):
    if not Path(path).exists():return None
    m=re.search(r'Maximum resident set size \(kbytes\):\s*(\d+)',Path(path).read_text(encoding='utf8',errors='replace'))
    return int(m[1])*1024 if m else None
def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()
