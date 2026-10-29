"""Compile/test a temporary Final, compare it with V2, and optionally publish.

One fixed config. No Router runs for case003/005. No version registration or Git
writes. Temporary binaries are automatically removed on success and failure.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile
from common import PROJECT, OUTPUTS, backend_command, linux_path, sha256, safe_label

FLAGS=['-std=c++17','-O3','-DNDEBUG','-Wall','-Wextra','-I','include']
SOURCES=['src/main.cpp','src/parser.cpp','src/router.cpp']


def save_new(path,value):
    with path.open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')


def quality_gate(rows):
    reasons=[];strict=False
    for case in ['toy','sample001','open_3d_case001']:
        pair={r['variant']:r for r in rows if r['case']==case}
        if set(pair)!= {'v2','v3'}:reasons.append(case+': missing pair');continue
        a,b=pair['v2'],pair['v3']
        if any(r['status']!='ROUTED' or r['official_status']!='PASS' for r in [a,b]):reasons.append(case+': routing/evaluation incomplete');continue
        if b['wirelength_dbu']>a['wirelength_dbu']+1e-6:reasons.append(case+': official wirelength regression')
        if b['wirelength_score']<a['wirelength_score']-1e-9:reasons.append(case+': wirelength score regression')
        if b['official_overflow']>a['official_overflow']:reasons.append(case+': official overflow regression')
        if b['congestion_score']<a['congestion_score']-1e-9:reasons.append(case+': congestion score regression')
        if b['thermal_score']<a['thermal_score']-1e-9:reasons.append(case+': thermal score regression')
        if case=='open_3d_case001':strict=b['wirelength_dbu']<a['wirelength_dbu']-1e-6 or b['official_overflow']<a['official_overflow']
    if not strict:reasons.append('No strict case001 wirelength/overflow improvement')
    return not reasons,reasons


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--experiment',required=True)
    p.add_argument('--apply',action='store_true');p.add_argument('--router-budget',type=int,default=180);p.add_argument('--evaluation-budget',type=int,default=180)
    a=p.parse_args();safe_label(a.experiment)
    if not 1<=a.router_budget<=900 or not 1<=a.evaluation_budget<=900:p.error('Budgets must be 1..900 seconds')
    report=OUTPUTS/'reports/v3_final'/a.experiment;report.mkdir(parents=True,exist_ok=True)
    if (report/'comparisons').exists() or (report/'validated_build.json').exists():p.error('Experiment already exists; use a new ID')
    backend='wsl' if os.name=='nt' else 'native'
    def run(args,log,timeout=240,env=None):
        cmd=backend_command(args,backend,cwd=PROJECT,env=env)
        with (report/log).open('xb') as f:result=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
        if result.returncode:raise RuntimeError(log+': command failed; see preserved log')
        return cmd
    config=PROJECT/'configs/v3_final.json';expected=sha256(config)
    sources={str(f.relative_to(PROJECT)):sha256(f) for f in [*list((PROJECT/'src').glob('*.cpp')),*list((PROJECT/'include').glob('*.hpp'))]}
    with tempfile.TemporaryDirectory(prefix='validate_',dir=report) as tmp:
        work=Path(tmp);core=work/'core';candidate=work/'router';research=work/'research'
        print('Compile original and Final correctness checks',flush=True)
        run(['g++',*FLAGS,'tests/core_tests.cpp','src/parser.cpp','src/router.cpp','-o',linux_path(core)],'core_build.log')
        run([linux_path(core)],'core_test.log',120)
        command=run(['g++',*FLAGS,'-DGR_V3_FINAL',*SOURCES,'-o',linux_path(candidate)],'final_build.log')
        run(['g++',*FLAGS,*SOURCES,'-o',linux_path(research)],'research_build.log')
        for pattern in ['test_workflow.py','test_router_versions.py','test_v3_final.py','test_ui_store.py','test_congestion_analysis.py']:
            run(['python3','-B','-m','unittest','discover','-s','tests','-p',pattern,'-v'],pattern+'.log',150,
                {'ROUTER_EXE':linux_path(research),'V3_FINAL_EXE':linux_path(candidate),'PYTHONDONTWRITEBYTECODE':'1'})
        manifest={'binary_sha256':sha256(candidate),'config_sha256':expected,'source_sha256':sources,'build_command':command,
            'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT).decode().strip(),'strategy':'v3_final_guide_guard','registered':False}
        save_new(report/'validated_build.json',manifest)
        with zipfile.ZipFile(report/'validated_sources.zip','x',compression=zipfile.ZIP_DEFLATED) as z:
            for name,digest in sources.items():
                if sha256(PROJECT/name)!=digest:raise RuntimeError('Production source changed during validation')
                z.write(PROJECT/name,name.replace('\\','/'))
            z.writestr('build_manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        print('Run V2/Final with identical v3_final.json parameters',flush=True)
        with (report/'comparison.log').open('xb') as f:
            result=subprocess.run([sys.executable,'-B',str(PROJECT/'scripts/compare_router_v3.py'),'--suite','final','--candidate',str(candidate),'--experiment',a.experiment,
                '--router-budget',str(a.router_budget),'--evaluation-budget',str(a.evaluation_budget)],cwd=PROJECT,stdout=f,stderr=subprocess.STDOUT,timeout=3000)
        if result.returncode:raise RuntimeError('Comparison failed; see comparison.log; Final not published')
        rows=json.loads((report/'comparisons/results.json').read_text(encoding='utf8'))['rows'];passed,reasons=quality_gate(rows)
        if sha256(config)!=expected or any(sha256(PROJECT/name)!=digest for name,digest in sources.items()):raise RuntimeError('Config/source changed; Final not published')
        decision={'passed':passed,'reasons':reasons,'published':False,'binary_sha256':manifest['binary_sha256']}
        if a.apply and passed:
            target=PROJECT/'build/router_v3_final';metadata=PROJECT/'build/router_v3_final.build.json'
            if target.exists() or metadata.exists():raise RuntimeError('Final already exists; preserve the release and review this validated report before replacing it')
            # Publish a freshly compiled, closed and hash-checked file atomically
            # without overwriting another process's release.
            with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.v3_final_',delete=False) as f:
                staging=Path(f.name)
                with candidate.open('rb') as source:shutil.copyfileobj(source,f)
                f.flush();os.fsync(f.fileno())
            try:
                staging.chmod(0o755)
                if sha256(staging)!=manifest['binary_sha256']:raise RuntimeError('Publication copy differs')
                os.link(staging,target)
            finally:staging.unlink(missing_ok=True)
            save_new(metadata,manifest|{'validation_report':str(report),'status':'VERIFIED_LOCAL_NOT_REGISTERED'})
            decision['published']=True
        save_new(report/'release_decision.json',decision)
        print(json.dumps(decision,ensure_ascii=False),flush=True)
        return 0 if passed else 2


if __name__=='__main__':raise SystemExit(main())
