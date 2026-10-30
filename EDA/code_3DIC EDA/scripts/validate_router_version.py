"""Validate one immutable numeric build; optionally run case001 exactly once.

Numeric V3.2/V3.3 builds. No retries, sweeps, Router recompilation
after publication, automatic registration, Git writes, or large Case runs.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
from common import PROJECT,OUTPUTS,backend_command,linux_path,sha256,catalog,formal_run_label
from manage_router_versions import publish_json,registry_lock
from router_versions import REGISTRY,load_registry

FLAGS=['-std=c++17','-O3','-DNDEBUG','-Wall','-Wextra','-I','include']
SOURCES=['src/main.cpp','src/parser.cpp','src/router.cpp']

def save_new(path,value):
    with Path(path).open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--version',required=True,choices=['v3_2','v3_3'])
    parser.add_argument('--run-case001',action='store_true',help='After all lightweight checks, one Router run and at most one latest evaluation, 600s each')
    a=parser.parse_args();version=a.version;report=OUTPUTS/'reports'/version;report.mkdir(parents=True,exist_ok=True)
    label=formal_run_label('open_3d_case001',version)
    binary=PROJECT/'build'/('router_'+version);config=PROJECT/'configs'/(version+'.json');directory=OUTPUTS/'runs/open_3d_case001'/label
    if binary.exists() or directory.exists() or (report/'build.json').exists():parser.error('Version binary/record already exists. Never overwrite or silently retry a numeric version.')
    if not config.is_file():parser.error('Missing matching numeric config')
    backend='wsl' if os.name=='nt' else 'native'
    pinned={str(f.relative_to(PROJECT)):sha256(f) for folder in ['src','include','tests'] for f in (PROJECT/folder).glob('*') if f.is_file()}
    config_sha=sha256(config);checks=[];micro=[]
    def run(args,name,timeout=240,env=None):
        with (report/name).open('xb') as log:call=subprocess.run(backend_command(args,backend,cwd=PROJECT,env=env),stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
        checks.append({'log':name,'returncode':call.returncode});print(name,call.returncode,flush=True)
        if call.returncode:raise RuntimeError(name+' failed; no version published, preserved log must be reviewed')
    (OUTPUTS/'development').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.'+version+'_validate_',dir=OUTPUTS/'development') as temp:
        work=Path(temp);core=work/'core';candidate=work/'router';research=work/'research';old_architecture=work/'old_architecture'
        run(['g++',*FLAGS,'tests/core_tests.cpp','src/parser.cpp','src/router.cpp','-o',linux_path(core)],'validation_core_build.log')
        run([linux_path(core)],'validation_core.log',120)
        command=['g++',*FLAGS,'-DGR_V3_FINAL','-DGR_V3_3' if version=='v3_3' else '-DGR_V3_2',*SOURCES,'-o',linux_path(candidate)]
        run(command,'validation_router_build.log')
        run(['g++',*FLAGS,*SOURCES,'-o',linux_path(research)],'validation_legacy_build.log')
        run(['g++',*FLAGS,'-DGR_V3_FINAL',*SOURCES,'-o',linux_path(old_architecture)],'validation_old_architecture_build.log')
        environment={'ROUTER_EXE':linux_path(research),'V3_FINAL_EXE':linux_path(old_architecture),'V3_2_EXE':linux_path(PROJECT/'build/router_v3_2' if version=='v3_3' else candidate),'PYTHONDONTWRITEBYTECODE':'1'}
        for pattern in ['test_workflow.py','test_router_versions.py','test_v3_final.py','test_ui_store.py','test_congestion_analysis.py','test_diagnostics.py']:
            run(['python3','-B','-m','unittest','discover','-s','tests','-p',pattern,'-v'],pattern+'.log',180,environment)
        for case in ['toy','sample001']:
            inputs=catalog()[case];folder=work/case;folder.mkdir()
            run([linux_path(candidate),'--def',linux_path(inputs['def']),'--lef',linux_path(inputs['lef']),'--thermal-report',linux_path(inputs['thermal']),
                 '--config',linux_path(config),'--time-limit','60','--output',linux_path(folder/'route.guide'),'--metrics',linux_path(folder/'result.json'),'--trace',linux_path(folder/'trace.jsonl')],case+'_temporary.log',75)
            data=json.loads((folder/'result.json').read_text(encoding='utf8'))
            expected='v3_3_calibrated_alternative_batch' if version=='v3_3' else 'v3_2_hotspot_multi_net'
            if data['metrics']['routed_nets']!=len(data['nets']) or data['v3_algorithm']['generation']!=expected:raise RuntimeError('Small-case completeness/architecture mismatch')
            from compare_router_v3 import replay
            micro.append({'case':case,'net_count':len(data['nets']),'metrics':data['metrics'],'trace_replay':replay(folder,data),'guide_sha256':sha256(folder/'route.guide'),'official_status':'NOT_EVALUATED','temporary_outputs':True})
        if sha256(config)!=config_sha or any(sha256(PROJECT/name)!=digest for name,digest in pinned.items()):raise RuntimeError('Source/test/config changed during validation')
        compiler=subprocess.check_output(backend_command(['g++','--version'],backend)).decode('utf8',errors='replace')
        receipt={'version':version,'path':'build/router_'+version,'config_path':'configs/'+version+'.json','label':label,'sha256':sha256(candidate),'config_sha256':config_sha,
            'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT).decode().strip(),'verified':False,'status':'CANDIDATE','release_status':'NOT_RELEASED',
            'source_sha256':{name:digest for name,digest in pinned.items() if name.startswith(('src','include'))},'test_sha256':{name:digest for name,digest in pinned.items() if name.startswith('tests')},
            'build_command':command,'compiler':compiler,'source_state':'UNCOMMITTED_ALGORITHM; immutable source snapshot, not an independent Git rebuild','checks':checks,'micro':micro}
        with zipfile.ZipFile(report/'sources.zip','x',zipfile.ZIP_DEFLATED) as archive:
            for name in pinned:archive.write(PROJECT/name,name.replace('\\','/'))
            archive.writestr('receipt.json',json.dumps(receipt,ensure_ascii=False,indent=2))
        # Same-volume, exclusive hard link publishes a closed complete binary.
        # TemporaryDirectory later removes only the temporary source link.
        with registry_lock():
            reg=REGISTRY;data=load_registry()
            if version in data['versions'] or version in data.get('candidates',{}):raise RuntimeError('Version identity already reserved')
            os.link(candidate,binary);binary.chmod(0o755)
            if sha256(binary)!=receipt['sha256']:raise RuntimeError('Publication hash mismatch')
            data.setdefault('candidates',{})[version]=receipt
            publish_json(reg,data)
        save_new(report/'build.json',receipt)
    print('Validated one candidate; temporary builds and toy/sample outputs removed',flush=True)
    if not a.run_case001:return 0
    # This exclusive receipt is a durable one-attempt fuse, also on host errors.
    save_new(report/'case001_attempt.json',{'case':'open_3d_case001','label':label,'router_sha256':receipt['sha256'],'config_sha256':config_sha,'router_budget_seconds':600,'evaluation_budget_seconds':600,'attempt':1})
    args=[sys.executable,'-B',str(PROJECT/'scripts/run_case.py'),'--case','open_3d_case001','--backend',backend,'--router',str(binary),'--config',str(config),'--label',label,'--time-limit','600']
    with (report/'case001_run.log').open('xb') as log:call=subprocess.run(args,cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT,timeout=690)
    run_data=json.loads((directory/'run.json').read_text(encoding='utf8'))
    if call.returncode or run_data.get('status')!='ROUTED' or run_data.get('returncode')!=0:
        print('Unique case001 run failed; preserved without retries or scoring',flush=True);return 2
    print('Unique case001 ROUTED; run one latest evaluation on the same Guide',flush=True)
    with (report/'case001_evaluation.log').open('xb') as log:call=subprocess.run([sys.executable,'-B',str(PROJECT/'scripts/evaluate.py'),'--case','open_3d_case001','--label',label,'--backend',backend,'--engine','latest','--timeout','600'],cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT,timeout=675)
    print('Unique case001 evaluation helper returncode:',call.returncode,flush=True)
    return call.returncode

if __name__=='__main__':raise SystemExit(main())
