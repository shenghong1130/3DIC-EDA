"""Explicit V4.2 candidate validation. Sequential, hash checked, no retries."""
import argparse,json,os,shutil,subprocess,sys,tempfile,time
from pathlib import Path
from common import PROJECT,catalog,sha256,linux_path,backend_command,load_run
from diagnostics import json_file,windows_memory,atomic_json
from evaluate_reference_20261103 import source_identity
from scoring import REFERENCE_LATEST,reference_directory,summary

ROOT=PROJECT/'outputs/reports/v4_2'
FLAGS=['GR_V3_FINAL','GR_V3_5','GR_V3_6','GR_V4','GR_V4_2']

def launch(command,log,timeout=None):
    log.parent.mkdir(parents=True,exist_ok=True)
    if log.exists():raise ValueError('Existing invocation log is protected: '+str(log))
    print('START',log.name,flush=True);started=time.monotonic()
    with log.open('xb') as f:
        proc=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
    row={'command':command,'returncode':proc.returncode,'seconds':time.monotonic()-started}
    atomic_json(log.with_suffix('.receipt.json'),row)
    print('END',log.name,proc.returncode,round(row['seconds'],2),flush=True)
    if proc.returncode:raise ValueError('Validation failed; preserve log: '+str(log))
    return row

def prepare():
    binary=PROJECT/'build/router_v4_2';tests=ROOT/'tests';tests.mkdir(exist_ok=True)
    if binary.exists():raise ValueError('Candidate binary already exists; never overwrite')
    with tempfile.TemporaryDirectory(prefix='v42_cmake_') as tmp:
        folder=Path(tmp);build=folder/'build'
        launch(backend_command(['cmake','-S',linux_path(PROJECT),'-B',linux_path(build),'-DCMAKE_BUILD_TYPE=Release','-DCMAKE_CXX_FLAGS_RELEASE=-O2','-DBUILD_TESTING=ON'],'wsl'),tests/'cmake_configure.log',120)
        for target in ['rules_20261103','v42_regression','v4_regression','v36_regression','v35_regression','physical_resources_tests','core_tests']:
            launch(backend_command(['cmake','--build',linux_path(build),'--target',target,'--parallel','1'],'wsl'),tests/(target+'_build.log'),300)
            launch(backend_command([linux_path(build/target)],'wsl',cwd=PROJECT),tests/(target+'.log'),240)
        launch(backend_command(['cmake','--build',linux_path(build),'--target','router_v4_2','--parallel','1'],'wsl'),tests/'router_build.log',300)
        exe=build/'router_v4_2'
        launch(backend_command([linux_path(exe),'--version'],'wsl'),tests/'router_version.log',30)
        with exe.open('rb') as src,binary.open('xb') as dst:shutil.copyfileobj(src,dst)
    compiler=subprocess.check_output(['wsl','-d','Ubuntu','--','g++','--version']).decode('utf8','replace').splitlines()[0]
    sources={p.relative_to(PROJECT).as_posix():sha256(p) for d in ['src','include'] for p in sorted((PROJECT/d).glob('*')) if p.is_file()}
    receipt={'path':'build/router_v4_2','config_path':'configs/v4_2.json','sha256':sha256(binary),'config_sha256':sha256(PROJECT/'configs/v4_2.json'),'source_sha256':sources,'build_command':['cmake','--build','<isolated build>','--target','router_v4_2','--parallel','1'],'flags':FLAGS,'compiler':compiler,'base_version':'v4','base_commit':'3f6cedd2318e0fded47fa2b9efd84e7d23554b6f','status':'CANDIDATE','release_status':'NOT_RELEASED','verified':False,'label':'case001_v4_2','formal_labels':['case001_v4_2','case003_v4_2','case005_v4_2'],'validation_path':'outputs/reports/v4_2/decision.json','ruleset':'REFERENCE_20261103','official_track_assignment_confirmed':False}
    atomic_json(ROOT/'build.json',receipt);atomic_json(PROJECT/'build/router_v4_2.build.json',receipt)
    registry=PROJECT/'docs/router_versions.json';data=json_file(registry)
    if 'v4_2' in data.get('candidates',{}) or 'v4_2' in data['versions']:raise ValueError('Existing V4.2 registration protected')
    data.setdefault('candidates',{})['v4_2']=receipt;atomic_json(registry,data)

def resource_check(case,folder):
    before=windows_memory();atomic_json(folder/'windows_before.json',before)
    proc=subprocess.run(['wsl','-d','Ubuntu','--','cat','/proc/meminfo','/proc/sys/kernel/random/boot_id'],capture_output=True,timeout=30)
    atomic_json(folder/'wsl_before.json',{'returncode':proc.returncode,'stdout':proc.stdout.decode('utf8','replace'),'stderr':proc.stderr.decode('utf8','replace')})
    if proc.returncode:raise ValueError('WSL unavailable; no Router started')
    if shutil.disk_usage(PROJECT).free<20*2**30:raise ValueError('Disk free space <20 GiB')
    proc=subprocess.run(['wsl','-d','Ubuntu','--','ps','-eo','comm,args'],capture_output=True,timeout=30)
    text=proc.stdout.decode('utf8','replace');atomic_json(folder/'processes_before.json',{'returncode':proc.returncode,'text':text})
    if proc.returncode:raise ValueError('Cannot verify active WSL processes')
    for line in text.splitlines()[1:]:
        f=line.split()
        if f and (f[0].startswith('router') or f[0] in {'score','reference','reference_20261103'}):raise ValueError('Another Router/scorer is active')
    if case=='open_3d_case005':
        mem={}
        for line in json_file(folder/'wsl_before.json')['stdout'].splitlines():
            f=line.split()
            if len(f)>1 and f[1].isdigit():mem[f[0].rstrip(':')]=int(f[1])*1024
        if before.get('commit_available_bytes',0)<6*2**30 or mem.get('MemAvailable',0)<6*2**30:
            atomic_json(folder/'resource_gate.json',{'status':'NOT_STARTED_RESOURCE_GATE','required_commit_bytes':6*2**30,'windows':before,'wsl':mem});raise ValueError('case005 resource gate: >=6 GiB host Commit and WSL available memory required')

def formal(case):
    stem=case.removeprefix('open_3d_');label=stem+'_v4_2';directory=PROJECT/'outputs/runs'/case/label
    evidence=ROOT/'runtime'/case;evidence.mkdir(parents=True,exist_ok=False)
    receipt=json_file(ROOT/'build.json');inputs=catalog()[case]
    if sha256(PROJECT/'build/router_v4_2')!=receipt['sha256'] or sha256(PROJECT/'configs/v4_2.json')!=receipt['config_sha256']:raise ValueError('Binary/config identity conflict')
    if any(sha256(PROJECT/k)!=v for k,v in receipt['source_sha256'].items()):raise ValueError('Source changed after build')
    hashes={k:sha256(inputs[k]) for k in ['def','lef','thermal']}
    if case.startswith('open_3d_'):
        baseline=json_file(PROJECT/'outputs/runs'/case/(stem+'_v3_6')/'run.json')
        if hashes!=baseline.get('input_sha256'):raise ValueError('Inputs differ from protected V3.6 history')
    atomic_json(evidence/'identity_before.json',{'router_sha256':receipt['sha256'],'config_sha256':receipt['config_sha256'],'input_sha256':hashes,'reference_source_sha256':source_identity()[0]})
    resource_check(case,evidence)
    budget={'toy':180,'sample001':180,'open_3d_case001':1800,'open_3d_case003':3600,'open_3d_case005':14400}[case]
    if directory.exists():
        r=load_run(directory)
        if r.get('status')!='ROUTED' or r.get('returncode')!=0 or r.get('router_sha256')!=receipt['sha256'] or r.get('config_sha256')!=receipt['config_sha256'] or r.get('input_sha256')!=hashes:raise ValueError('Existing formal record conflicts; preserve it without retries')
    else:
        launch([sys.executable,'-B','scripts/run_case.py','--case',case,'--backend','wsl','--router','build/router_v4_2','--config','configs/v4_2.json','--label',label,'--time-limit',str(budget)],evidence/'router.log')
    r=load_run(directory)
    if r.get('status')!='ROUTED' or r.get('returncode')!=0 or not (directory/'result.json').is_file() or not (directory/'route.guide').is_file():raise ValueError('Router incomplete: do not score or advance')
    mb=4096 if case.endswith('005') else 3072 if case.endswith('003') else 1536
    if case.endswith('005') and windows_memory().get('commit_available_bytes',0)<5*2**30:raise ValueError('Guide preserved; insufficient Commit for reference scoring')
    launch([sys.executable,'-B','scripts/score_case.py','--case',case,'--label',label,'--scorer',REFERENCE_LATEST,'--backend','wsl','--memory-mb',str(mb),'--timeout',str(min(budget,7200))],evidence/'reference.log')
    score=summary(case,label,REFERENCE_LATEST)
    if score['status']!='REFERENCE_VALID':raise ValueError('Reference invalid/incomplete: stop larger Case')
    audit=ROOT/'audit'/case
    launch([sys.executable,'-B','scripts/audit_submission_tsv.py','--def',inputs['def'],'--lef',inputs['lef'],'--guide',str(directory/'route.guide'),'--case',case,'--label',label,'--output',str(audit)],evidence/'audit.log')
    a=json_file(audit/'summary.json');c=a.get('counts',{})
    if a.get('rule_matrix_conflicting_candidates',{}).get('A_D',1) or c.get('ambiguous_nets',0) or c.get('missing_tsv_nets',0):raise ValueError('Independent TSV center/unique conflict')
    atomic_json(evidence/'summary.json',{'router':r,'scoring':score,'guide_sha256':sha256(directory/'route.guide'),'audit':a})
    print('COMPLETE',case,score['status'],flush=True)

def models():
    """Three labelled assumptions, one unchanged physical input, no Router."""
    import evaluate_reference_20261103 as reference
    folder=ROOT/'models/case005';folder.mkdir(parents=True,exist_ok=True)
    identity,inputs,_=reference.identity('open_3d_case005',model_only=True)
    with tempfile.TemporaryDirectory(prefix='v42_model_') as tmp:
        exe=Path(tmp)/'reference';command=reference.build(exe,'wsl')
        for policy in ['nearest_in_cell','lower_in_cell','upper_in_cell']:
            out=folder/policy;out.mkdir(exist_ok=True)
            previous=json_file(out/'invocation.json')
            expected=dict(identity,pin_assignment_policy=policy,reference_source_sha256=reference.source_identity()[0])
            if previous.get('status')=='COMPLETED' and previous.get('identity')==expected and previous.get('compiled_binary_sha256')==sha256(exe):
                print('REUSE_MODEL',policy,flush=True);continue
            if (out/'reference_report.json').exists() or (out/'invocation.json').exists() or (out/'analysis_error.json').exists():
                raise ValueError('Existing model analysis protected; no automatic retry: '+str(out))
            samples=[];before=windows_memory()
            for _ in range(5):
                samples.append(before)
                if before.get('commit_available_bytes',0)>=2*2**30:break
                time.sleep(2);before=windows_memory()
            atomic_json(out/'precheck_samples.json',samples)
            if not (out/'windows_before.json').exists():atomic_json(out/'windows_before.json',before)
            else:
                if (out/'windows_after_cleanup.json').exists():raise ValueError('Precheck already resumed; inspect evidence')
                atomic_json(out/'windows_after_cleanup.json',before)
            if before.get('commit_available_bytes',0)<2*2**30:
                atomic_json(out/'resource_gate.json',{'status':'NOT_STARTED_RESOURCE_GATE','windows':before,'required_commit_bytes':2*2**30})
                raise ValueError('Model diagnostic requires >=2 GiB Commit; no analysis launched')
            try:
                report,call=reference.execute(exe,inputs,None,out,'wsl',1536,900,True,policy)
                atomic_json(out/'invocation.json',{'identity':dict(identity,pin_assignment_policy=policy,reference_source_sha256=reference.source_identity()[0]),'status':'COMPLETED','compiled_binary_sha256':sha256(exe),'command':call,'build_command':command})
            except Exception as e:atomic_json(out/'analysis_error.json',{'status':'ANALYSIS_ERROR','error':str(e)});raise
    print('MODEL_DIAGNOSTICS_COMPLETED',flush=True)

def source_package():
    """Validate a private staging ZIP before publishing its exact bytes."""
    import zipfile,hashlib
    target=PROJECT/'outputs/submissions/submission_v4_2_source.zip'
    manifest=target.with_suffix('.manifest.json')
    if target.exists() or manifest.exists():raise ValueError('Existing source submission protected')
    evidence=ROOT/'source_validation';evidence.mkdir(exist_ok=False)
    before=windows_memory();atomic_json(evidence/'windows_before.json',before)
    if before.get('commit_available_bytes',0)<2*2**30:raise ValueError('Source build requires >=2 GiB host Commit; no compiler launched')
    with tempfile.TemporaryDirectory(prefix='source stage ',dir=ROOT) as stage:
        stage=Path(stage);archive=stage/'source.zip';unpacked=stage/'clean source';unpacked.mkdir()
        launch([sys.executable,'-B','scripts/package_submission.py','--output',str(archive)],evidence/'package.log')
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:raise ValueError('Source ZIP CRC error')
            for i in z.infolist():
                p=Path(i.filename)
                if p.is_absolute() or '..' in p.parts or i.file_size>4*1024**2:raise ValueError('Unsafe/unexpected source archive member')
                if p.name=='router' or p.suffix=='.exe':raise ValueError('Source ZIP contains prebuilt Router')
            z.extractall(unpacked)
        if sha256(unpacked/'configs/v4_2.json')!=sha256(PROJECT/'configs/v4_2.json'):raise ValueError('Packaged config changed')
        launch(backend_command(['bash','build.sh'],'wsl',cwd=unpacked),evidence/'clean_build.log',180)
        launch(backend_command(['bash','run.sh','--version'],'wsl',cwd=unpacked),evidence/'version.log',30)
        identities={}
        for case in ['toy','sample001']:
            inputs=catalog()[case];out=stage/(case+'.guide')
            launch(backend_command(['bash',str(linux_path(unpacked/'run.sh')),'--def',linux_path(inputs['def']),'--lef',linux_path(inputs['lef']),'--thermal-report',linux_path(inputs['thermal']),'--output',linux_path(out),'--time-limit','180'],'wsl',cwd=stage),evidence/(case+'.log'),200)
            expected=PROJECT/'outputs/runs'/case/(case+'_v4_2')/'route.guide'
            if sha256(out)!=sha256(expected):raise ValueError('Clean source build Guide differs from validated candidate: '+case)
            identities[case]=sha256(out)
        stage_manifest=json_file(archive.with_suffix('.manifest.json'))
        target.parent.mkdir(parents=True,exist_ok=True)
        with archive.open('rb') as src,target.open('xb') as dst:shutil.copyfileobj(src,dst)
        stage_manifest.update(archive=str(target),sha256=sha256(target),candidate=True,clean_build_validated=True,router_binary_sha256=sha256(unpacked/'router'),clean_guides=identities,official_qualification=False)
        with manifest.open('x',encoding='utf8') as f:json.dump(stage_manifest,f,ensure_ascii=False,indent=2)
        atomic_json(ROOT/'source_validation.json',{'status':'LOCAL_SOURCE_VALIDATED','archive':str(target),'archive_sha256':sha256(target),'compiled_binary_sha256':sha256(unpacked/'router'),'formal_binary_sha256':sha256(PROJECT/'build/router_v4_2'),'same_config_sha256':sha256(unpacked/'configs/v4_2.json'),'clean_guide_sha256':identities,'temporary_tree_cleaned_on_exit':True,'not_official_submission_certification':True})

def compare_history():
    evidence=ROOT/'history_comparison';evidence.mkdir(exist_ok=True)
    for suffix in ['001','003','005']:
        case='open_3d_case'+suffix
        for version in ['v3_6','v4']:
            label='case'+suffix+'_'+version;folder=PROJECT/'outputs/runs'/case/label
            if not (folder/'route.guide').is_file():continue
            if (evidence/(label+'.log')).exists():
                if summary(case,label,REFERENCE_LATEST)['status'] in {'REFERENCE_VALID','REFERENCE_INVALID'}:continue
                raise ValueError('Previous scoring invocation incomplete; no automatic retry')
            memory=windows_memory();minimum=5 if suffix=='005' else 2
            samples=[]
            for _ in range(5):
                samples.append(memory)
                if memory.get('commit_available_bytes',0)>=minimum*2**30:break
                time.sleep(2);memory=windows_memory()
            atomic_json(evidence/(label+'_precheck_samples.json'),samples)
            if memory.get('commit_available_bytes',0)<minimum*2**30:
                path=evidence/(label+'_resource_gate.json')
                if not path.exists():atomic_json(path,{'status':'NOT_STARTED_RESOURCE_GATE','windows':memory,'required_commit_bytes':minimum*2**30})
                continue
            launch([sys.executable,'-B','scripts/score_case.py','--case',case,'--label',label,'--scorer',REFERENCE_LATEST,'--backend','wsl','--memory-mb','4096' if suffix=='005' else '1536','--timeout','7200' if suffix=='005' else '3600'],evidence/(label+'.log'))

def guide_package():
    """Publish three verified real Guides only; never substitute an older run."""
    import zipfile,hashlib
    decision=json_file(ROOT/'decision.json')
    if not decision.get('all_cases_passed'):raise ValueError('All three V4.2 Cases must pass before Guide packaging')
    build=json_file(ROOT/'build.json');files={}
    if sha256(PROJECT/'build/router_v4_2')!=build['sha256'] or sha256(PROJECT/'configs/v4_2.json')!=build['config_sha256']:raise ValueError('Current binary/config changed')
    target=PROJECT/'outputs/submissions/3DIC_guide_v4_2.zip';manifest=target.with_suffix('.manifest.json')
    if target.exists() or manifest.exists():raise ValueError('Existing Guide ZIP/Manifest protected')
    for suffix,total in [('001',3152),('003',151611),('005',179700)]:
        case='open_3d_case'+suffix;label='case'+suffix+'_v4_2';directory=PROJECT/'outputs/runs'/case/label
        run=load_run(directory);score=summary(case,label,REFERENCE_LATEST);audit=json_file(ROOT/'audit'/case/'summary.json')
        if run.get('router_sha256')!=build['sha256'] or run.get('config_sha256')!=build['config_sha256'] or score['status']!='REFERENCE_VALID' or score['scorer_source_sha256']!=source_identity()[0] or score['report'].get('evaluated_nets')!=total or audit.get('rule_matrix_conflicting_candidates',{}).get('A_D',1) or any(audit.get('counts',{}).get(k,0) for k in ['ambiguous_nets','missing_tsv_nets']):raise ValueError('Live run/scorer/audit identity mismatch: '+case)
        files[case+'.output.guide']={'path':str(directory/'route.guide'),'sha256':score['guide_sha256']}
    with tempfile.TemporaryDirectory(prefix='guide_stage_',dir=ROOT) as tmp:
        archive=Path(tmp)/'candidate.zip'
        with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
            for name,item in files.items():z.write(item['path'],name)
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:raise ValueError('Guide ZIP CRC failed')
            for name,item in files.items():
                h=hashlib.sha256()
                with z.open(name) as f:
                    for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
                if h.hexdigest()!=item['sha256'] or sha256(item['path'])!=item['sha256']:raise ValueError('Guide changed during packaging')
        with archive.open('rb') as src,target.open('xb') as dst:shutil.copyfileobj(src,dst)
        with manifest.open('x',encoding='utf8') as f:json.dump({'version':'v4_2','candidate':True,'official_status':'NOT_EVALUATED','sha256':sha256(target),'guides':files,'router_sha256':build['sha256'],'config_sha256':build['config_sha256'],'reference_source_sha256':source_identity()[0]},f,ensure_ascii=False,indent=2)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=['prepare','formal','models','source','compare','guide']);p.add_argument('--case');a=p.parse_args()
    if a.phase=='prepare':prepare()
    elif a.phase=='models':models()
    elif a.phase=='source':source_package()
    elif a.phase=='compare':compare_history()
    elif a.phase=='guide':guide_package()
    elif a.case in catalog():formal(a.case)
    else:p.error('formal requires a known --case')
