"""Explicit V4 build, isolated ablations and sequential validation; no retries.

Every invocation reserves new artifacts exclusively. Existing completed formal
runs are reused only after identity verification. No official scoring/upload.
"""
import argparse,json,os,shutil,subprocess,sys,tempfile,time,uuid
from pathlib import Path
from common import PROJECT,catalog,sha256,linux_path,backend_command,timed_process
from diagnostics import atomic_json,json_file,windows_memory,utc,progress_tail
from evaluate_reference import source_identity,build as build_reference,execute as reference
from scoring import reference_directory
from compare_v36 import small_metadata

ROOT=PROJECT/'outputs/reports/v4'
FLAGS=['-DGR_V3_FINAL','-DGR_V3_5','-DGR_V3_6','-DGR_V4']
VARIANTS={'escape':['GR_V4_DISABLE_TSV','GR_V4_DISABLE_THERMAL'],
          'tsv':['GR_V4_DISABLE_ESCAPE','GR_V4_DISABLE_THERMAL'],
          'thermal':['GR_V4_DISABLE_ESCAPE','GR_V4_DISABLE_TSV']}

def save(path,data):
    with path.open('x',encoding='utf8') as f:json.dump(data,f,ensure_ascii=False,indent=2)

def compile_binary(destination,flags,log):
    if destination.exists():raise ValueError('Binary exists: '+str(destination))
    with tempfile.TemporaryDirectory(prefix='v4_build_') as temp:
        exe=Path(temp)/'router';cmd=['g++','-std=c++17','-O2','-I','include',*flags,'src/main.cpp','src/parser.cpp','src/router.cpp','-o',linux_path(exe)]
        with log.open('xb') as f:proc=subprocess.run(backend_command(cmd,'wsl',cwd=PROJECT),stdout=f,stderr=subprocess.STDOUT,timeout=180)
        if proc.returncode:raise ValueError('Build failed: '+str(log))
        with exe.open('rb') as src,destination.open('xb') as dst:shutil.copyfileobj(src,dst)
    return {'sha256':sha256(destination),'flags':flags,'build_command':cmd,
        'source_sha256':{str(p.relative_to(PROJECT)).replace('\\','/'):sha256(p) for folder in ['src','include'] for p in (PROJECT/folder).glob('*') if p.is_file()}}

def prepare():
    tests=json_file(ROOT/'tests/suite.json');assert tests['v36_byte_regression']['identical']
    assert all(x.get('returncode')==0 for k,x in tests.items() if k!='v36_byte_regression')
    verified=json_file(ROOT/'tests/v4_verified_test.json');assert verified.get('returncode')==0
    assert verified['test_source_sha256']==sha256(PROJECT/'tests/v4_regression.cpp')
    config=PROJECT/'configs/v4.json';binary=PROJECT/'build/router_v4'
    if config.exists() or binary.exists():raise ValueError('V4 Config/Binary already reserved')
    data=compile_binary(binary,FLAGS,ROOT/'build.log')
    with config.open('xb') as f:f.write((PROJECT/'configs/v3_6.json').read_bytes())
    compiler=subprocess.check_output(['wsl','-d','Ubuntu','--','g++','--version'],timeout=20).decode('utf8','replace').splitlines()[0]
    data.update(path='build/router_v4',config_path='configs/v4.json',config_sha256=sha256(config),label='case001_v4',verified=False,status='CANDIDATE',release_status='NOT_RELEASED',compiler=compiler,base_commit='f9db70d5f63543fa60a0e4d348fc0a44adbc5101',evaluation_summary='outputs/reports/v4/decision.json')
    registry=PROJECT/'docs/router_versions.json';prior=json_file(registry);save(ROOT/'registry_before.json',prior)
    if 'v4' in prior.get('versions',{}) or 'v4' in prior.get('candidates',{}):raise ValueError('V4 registry conflict')
    prior.setdefault('candidates',{})['v4']=data;atomic_json(registry,prior);save(ROOT/'build.json',data);save(PROJECT/'build/router_v4.build.json',data)
    dev=PROJECT/'outputs/development/v4_ablations';dev.mkdir(parents=True,exist_ok=False)
    for name,macros in VARIANTS.items():
        row=compile_binary(dev/name,FLAGS+['-D'+m for m in macros],ROOT/(name+'_build.log'));row['experimental_only']=True;save(dev/(name+'.json'),row)
    print(json.dumps({'prepared':True,'binary_sha256':data['sha256']}),flush=True)

def launch(cmd,target,name):
    with (target/(name+'.log')).open('xb') as log,(target/(name+'_windows_memory.jsonl')).open('x',encoding='utf8') as mem:
        started=time.monotonic();p=subprocess.Popen(cmd,cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT)
        while True:
            mem.write(json.dumps({'timestamp':utc(),'elapsed_seconds':time.monotonic()-started,**windows_memory()})+'\n');mem.flush()
            rc=p.poll()
            if rc is not None:break
            time.sleep(10)
    save(target/(name+'_execution.json'),{'command':cmd,'returncode':rc,'wall_seconds':time.monotonic()-started})
    return rc

def experiment(case,variant,scorer):
    directory=ROOT/'ablations'/case/variant;directory.mkdir(parents=True,exist_ok=False)
    binary=PROJECT/'build'/('router_v3_6' if variant=='baseline' else 'router_v4') if variant in ['baseline','joint'] else PROJECT/'outputs/development/v4_ablations'/variant
    inputs={k:Path(catalog()[case][k]) for k in ['def','lef','thermal']};budget=600 if case=='open_3d_case001' else 3600 if case=='open_3d_case003' else 180
    guide=directory/'route.guide';runid=uuid.uuid4().hex
    args=[linux_path(binary),'--def',linux_path(inputs['def']),'--lef',linux_path(inputs['lef']),'--thermal-report',linux_path(inputs['thermal']),'--config',linux_path(PROJECT/'configs/v4.json'),'--output',linux_path(guide),'--metrics',linux_path(directory/'result.json'),'--time-limit',str(budget),'--progress-dir',linux_path(directory)]
    args=['python3',linux_path(PROJECT/'scripts/linux_supervisor.py'),'--directory',linux_path(directory),'--run-id',runid,'--interval','5','--lease-seconds','60','--',*args]
    command=backend_command(args,'wsl',cwd=PROJECT,timeout=budget+10,time_file=directory/'resource_usage.txt')
    identity={'case':case,'variant':variant,'experimental_only':True,'router_sha256':sha256(binary),'config_sha256':sha256(PROJECT/'configs/v4.json'),'input_sha256':{k:sha256(v) for k,v in inputs.items()},'command':command,'flags':json_file(PROJECT/'outputs/development/v4_ablations'/(variant+'.json')).get('flags',FLAGS if variant=='joint' else FLAGS[:-1])}
    save(directory/'identity.json',identity)
    result=timed_process(command,directory/'router.log',budget+45,monitor_dir=directory,monitor_interval=5,stall_after=30,native_resources=False)
    save(directory/'execution.json',result)
    if result['returncode']!=0 or not guide.exists():raise ValueError('Experimental routing failed: '+str(directory))
    evaluation=directory/'reference';evaluation.mkdir()
    try:report,cmd=reference(scorer,inputs,guide,evaluation,'wsl',1536 if case!='open_3d_case003' else 3072,600 if case!='open_3d_case003' else 3600)
    except Exception as e:save(evaluation/'analysis_error.json',{'status':'ANALYSIS_ERROR','error':str(e)});raise
    save(evaluation/'identity.json',dict(identity,guide_sha256=sha256(guide),reference_source_sha256=source_identity()[0],reference_binary_sha256=sha256(scorer)))
    if report['static_legality']!='REFERENCE_VALID':raise ValueError('Experimental Guide invalid: '+str(directory))
    row={'case':case,'variant':variant,'status':report['static_legality'],'wirelength_um':report['wirelength_dbu']/report['dbu_per_micron'],'overflow':report['overflow'],'potential':report['normalized_overflow_squared'],'thermal_correlation':report['thermal_correlation'],'M1':report['by_layer']['1']['overflow'],'M10':report['by_layer']['10']['overflow'],'M11':report['by_layer']['11']['overflow'],'runtime_seconds':result['runtime_seconds'],'algorithm':small_metadata(directory/'result.json'),'guide_sha256':sha256(guide)}
    save(directory/'summary.json',row);print('ABLATION',json.dumps({k:v for k,v in row.items() if k!='algorithm'}),flush=True);return row

def ablations(case=None,variant=None):
    rows=[]
    with tempfile.TemporaryDirectory(prefix='v4_reference_') as temp:
        scorer=Path(temp)/'reference';build_reference(scorer,'wsl')
        cases=[case] if case else ['toy','sample001','open_3d_case001']
        for case in cases:
            choices=[variant] if variant else ['baseline','escape','tsv','thermal','joint']
            # Formal case001_v4 supplies the joint measurement: no duplicate
            # complete routing of the formal binary merely for the table.
            if case=='open_3d_case001' and not variant:choices.remove('joint')
            for name in choices:rows.append(experiment(case,name,scorer))
    return rows

def formal(case):
    suffix=case.removeprefix('open_3d_');label=suffix+'_v4';target=PROJECT/'outputs/runs'/case/label
    report=ROOT/(suffix+'_execution');report.mkdir(exist_ok=False)
    build=json_file(ROOT/'build.json')
    if sha256(PROJECT/'build/router_v4')!=build['sha256'] or sha256(PROJECT/'configs/v4.json')!=build['config_sha256']:
        raise ValueError('V4 binary/config identity mismatch; no routing started')
    baseline=json_file(PROJECT/'outputs/runs'/case/(suffix+'_v3_6')/'run.json')
    actual={k:sha256(catalog()[case][k]) for k in ['def','lef','thermal']}
    if actual!=baseline.get('input_sha256'):raise ValueError('Inputs differ from frozen V3.6 baseline; no routing started')
    save(report/'identity_before.json',{'case':case,'label':label,'router_sha256':build['sha256'],'config_sha256':build['config_sha256'],'input_sha256':actual})
    before=windows_memory();save(report/'resources_before.json',before)
    if case=='open_3d_case005' and before.get('commit_available_bytes',0)<6*1024**3:
        save(report/'resource_gate.json',{'status':'NOT_STARTED_RESOURCE_GATE','required_commit_bytes':6*1024**3,'actual':before});raise ValueError('case005 Windows Commit headroom <6GiB; no Router launched')
    probe=subprocess.run(['wsl','-d','Ubuntu','--','cat','/proc/meminfo','/proc/sys/kernel/random/boot_id'],capture_output=True,timeout=30)
    save(report/'wsl_before.json',{'returncode':probe.returncode,'stdout':probe.stdout.decode('utf8','replace')})
    if probe.returncode:raise ValueError('WSL resource probe failed')
    mem={}
    for line in probe.stdout.decode('utf8','replace').splitlines():
        fields=line.split()
        if len(fields)>1 and fields[1].isdigit():mem[fields[0].rstrip(':')]=int(fields[1])*1024
    if case=='open_3d_case005' and mem.get('MemAvailable',0)<6*1024**3:raise ValueError('WSL available memory <6GiB; no Router launched')
    if shutil.disk_usage(PROJECT).free<20*1024**3:raise ValueError('Insufficient disk for complete results and audit')
    processes=subprocess.run(['wsl','-d','Ubuntu','--','ps','-eo','comm,args'],capture_output=True,timeout=20)
    save(report/'processes_before.json',{'returncode':processes.returncode,'stdout':processes.stdout.decode('utf8','replace')})
    for line in processes.stdout.decode('utf8','replace').splitlines()[1:]:
        fields=line.split()
        if fields and (fields[0].startswith('router') or fields[0] in ['score','reference']):raise ValueError('Another Router/scorer process is active')
    budgets={'open_3d_case001':600,'open_3d_case003':3600,'open_3d_case005':14400};timeout=budgets[case]
    if target.exists():
        run=json_file(target/'run.json')
        if run.get('status')!='ROUTED' or run.get('returncode')!=0 or run.get('router_sha256')!=build['sha256'] or run.get('config_sha256')!=build['config_sha256']:raise ValueError('Existing formal run conflict; not overwritten')
    else:
        cmd=[sys.executable,'-B','scripts/run_case.py','--case',case,'--backend','wsl','--router','build/router_v4','--config','configs/v4.json','--label',label,'--time-limit',str(timeout)]
        if launch(cmd,report,'router')!=0:raise ValueError('Router failed; preserve evidence, no retry')
    run=json_file(target/'run.json')
    if run.get('status')!='ROUTED' or run.get('returncode')!=0 or not (target/'route.guide').exists():raise ValueError('No complete Guide; scoring not started')
    mb={'open_3d_case001':1536,'open_3d_case003':3072,'open_3d_case005':4096}[case]
    if case=='open_3d_case005' and windows_memory().get('commit_available_bytes',0)<5*1024**3:raise ValueError('Guide preserved; reference scoring resource gate <5GiB')
    cmd=[sys.executable,'-B','scripts/score_case.py','--case',case,'--label',label,'--scorer','reference_20261029','--backend','wsl','--memory-mb',str(mb),'--timeout',str(min(timeout,7200))]
    if launch(cmd,report,'reference')!=0:raise ValueError('Reference ANALYSIS_ERROR; not official FAIL; no retry')
    refdir=reference_directory(case,label);ref=json_file(refdir/'reference_report.json')
    if ref['static_legality']!='REFERENCE_VALID':raise ValueError('Reference invalid; stop larger runs')
    inputs=catalog()[case];audit=ROOT/'audit'/case;audit.parent.mkdir(exist_ok=True)
    cmd=[sys.executable,'-B','scripts/audit_submission_tsv.py','--def',str(inputs['def']),'--lef',str(inputs['lef']),'--guide',str(target/'route.guide'),'--case',case,'--label',label,'--output',str(audit)]
    if launch(cmd,report,'audit')!=0:raise ValueError('Independent audit incomplete; stop larger runs')
    a=json_file(audit/'summary.json');counts=a.get('counts',{})
    if a.get('rule_matrix_conflicting_candidates',{}).get('A_D',1) or counts.get('ambiguous_nets',0) or counts.get('missing_tsv_nets',0):raise ValueError('Confirmed center/unique TSV violation; stop')
    save(report/'summary.json',{'case':case,'run_directory':str(target),'reference_directory':str(refdir),'reference':ref,'audit_classification':a['classification'],'center_conflicts':a['rule_matrix_conflicting_candidates']['A_D'],'guide_sha256':sha256(target/'route.guide'),'algorithm':small_metadata(target/'result.json'),'router':run})
    print('FORMAL_COMPLETED',case,ref['static_legality'],flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('phase',choices=['prepare','ablations','formal']);parser.add_argument('--case',choices=['toy','sample001','open_3d_case001','open_3d_case003','open_3d_case005']);parser.add_argument('--variant',choices=['baseline','escape','tsv','thermal','joint']);args=parser.parse_args()
    if args.phase=='prepare':prepare()
    elif args.phase=='ablations':ablations(args.case,args.variant)
    elif args.case in ['open_3d_case001','open_3d_case003','open_3d_case005']:formal(args.case)
    else:parser.error('Formal requires one explicit public Case')
