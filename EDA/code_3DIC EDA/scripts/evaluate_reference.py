#!/usr/bin/env python3
"""Standalone REFERENCE_20261029; never invokes an official evaluator/Router."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from common import PROJECT, catalog, sha256, backend_command, linux_path, load_run, existing_run_dir
from diagnostics import atomic_json
from router_versions import identify_router

SOURCES = ('reference_scorer/main.cpp', 'include/physical_resources.hpp',
           'include/model.hpp', 'include/json.hpp', 'src/parser.cpp')
RULESET = 'REFERENCE_20261029'


def source_identity():
    sources = {name: sha256(PROJECT/name) for name in SOURCES}
    digest = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    return digest, sources


def build(executable, backend):
    command = ['g++', '-std=c++17', '-O2', '-I', 'include',
               'reference_scorer/main.cpp', 'src/parser.cpp', '-o', linux_path(executable)]
    if backend == 'native':command[-1] = str(executable)
    proc = subprocess.run(backend_command(command, backend, cwd=PROJECT),
                          capture_output=True, timeout=120)
    if proc.returncode:
        raise RuntimeError('Reference build failed: '+proc.stderr.decode('utf8', errors='replace'))
    return command


def identity(case, label=None, model_only=False):
    if case not in catalog():raise ValueError('Unknown Case')
    inputs = {k: Path(catalog()[case][k]) for k in ('lef','def','thermal')}
    result = {'case':case, 'ruleset':RULESET, 'official':False,
              'input_sha256':{k:sha256(p) for k,p in inputs.items()}, 'model_only':model_only}
    guide = None
    if not model_only:
        directory = existing_run_dir(case,label)
        run = load_run(directory)
        if run.get('status')!='ROUTED' or run.get('returncode')!=0:
            raise ValueError('A complete ROUTED historical Guide is required')
        guide = directory/'route.guide'
        if not guide.is_file():raise ValueError('Missing Guide')
        if result['input_sha256']!=run['input_sha256']:raise ValueError('Input identity mismatch')
        # Verify actual binary identity by registered hash, not historical name.
        router = identify_router(run.get('router_sha256'))
        if router['router_version']=='UNKNOWN':raise ValueError('Unconfirmed historical Router identity: '+str(router))
        binary=PROJECT/'build'/('router_'+router['router_version'])
        if sha256(binary)!=run['router_sha256']:raise ValueError('Registered historical binary differs from run record')
        config = PROJECT/'configs'/(router['router_version']+'.json')
        config_sha = sha256(config)
        if run.get('config_sha256') and config_sha!=run['config_sha256']:
            raise ValueError('Historical Config hash mismatch')
        effective=json.loads(config.read_text(encoding='utf8'))
        historical=run.get('config',{})
        if effective!=historical:raise ValueError('Historical Config values mismatch')
        result.update(label=label,guide_sha256=sha256(guide),guide_path=str(guide),
                      router_sha256=run['router_sha256'],router_version=router['router_version'],
                      historical_router_status=run.get('router_version_status'),
                      config_sha256=config_sha,historical_config_sha256=run.get('config_sha256'),
                      config_identity_note='Current canonical config values match historical record; historical byte hash may be absent',
                      config=historical,config_overrides=run.get('config_overrides',{}))
    return result, inputs, guide


def execute(executable, inputs, guide, directory, backend, memory_mb=1536, timeout=600, model_only=False):
    conv=linux_path if backend=='wsl' else str
    command=[conv(executable),conv(inputs['lef']),conv(inputs['def']),conv(inputs['thermal']),
             conv(guide) if guide else '-',conv(directory),'--memory-mb',str(memory_mb)]
    if model_only:command+=['--model-only']
    proc=subprocess.run(backend_command(command,backend,cwd=PROJECT,timeout=timeout),capture_output=True,timeout=timeout+10)
    if proc.returncode:
        raise RuntimeError('Reference analysis incomplete (not an official FAIL): '+proc.stderr.decode('utf8',errors='replace'))
    report=json.loads((directory/'reference_report.json').read_bytes())
    report.update(reference_scorer_sha256=sha256(executable),reference_source_sha256=source_identity()[0])
    atomic_json(directory/'reference_report.json',report)
    return report,command


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case',required=True);p.add_argument('--label')
    p.add_argument('--output-dir',type=Path);p.add_argument('--model-only',action='store_true')
    p.add_argument('--backend',choices=['wsl','native'],default='wsl' if os.name=='nt' else 'native')
    p.add_argument('--memory-mb',type=int,default=1536);p.add_argument('--timeout',type=float,default=600)
    a=p.parse_args(argv)
    if not a.model_only and not a.label:p.error('--label is required for Guide evaluation')
    if not 256<=a.memory_mb<=8192 or not 0<a.timeout<=7200:p.error('Invalid resource budget')
    try:
        data,inputs,guide=identity(a.case,a.label,a.model_only)
        scorer,sources=source_identity();data.update(reference_source_sha256=scorer,source_sha256=sources)
        target=(a.output_dir or PROJECT/'outputs/reports/reference_20261029'/a.case/(a.label or 'physical_model')).resolve()
        if not target.is_relative_to((PROJECT/'outputs').resolve()):raise ValueError('Reports must stay under outputs/')
        if target.exists():
            prior=json.loads((target/'invocation.json').read_bytes())
            if prior.get('identity')!=data or prior.get('status')!='COMPLETED':raise ValueError('Existing report identity differs or is incomplete; preserve it, do not overwrite')
            report=json.loads((target/'reference_report.json').read_bytes())
            print(json.dumps({'reused':True,'report_directory':str(target),'report':report},ensure_ascii=False));return 0
        # Build BEFORE reserving the final directory; a failed build leaves no
        # apparently completed reference result. Exclusive mkdir prevents races.
        with tempfile.TemporaryDirectory(prefix='gr_reference_') as temp:
            executable=Path(temp)/'reference';build_command=build(executable,a.backend)
            target.mkdir(parents=True,exist_ok=False)
            receipt={'identity':data,'status':'RUNNING','official':False,
                     'compiled_binary_sha256':sha256(executable),'build_command':build_command}
            atomic_json(target/'invocation.json',receipt)
            started=time.monotonic()
            try:
                report,command=execute(executable,inputs,guide,target,a.backend,a.memory_mb,a.timeout,a.model_only)
                # Detect any input/Guide change during analysis before success.
                after,_,_=identity(a.case,a.label,a.model_only)
                if any(after[k]!=v for k,v in data.items() if k in after):raise ValueError('Inputs changed while evaluating')
                report.update(guide_sha256=data.get('guide_sha256'),
                              input_sha256=data['input_sha256'])
                atomic_json(target/'reference_report.json',report)
                receipt.update(status='COMPLETED',command=command)
            except (RuntimeError,OSError,ValueError,subprocess.TimeoutExpired) as e:
                receipt.update(status='ERROR',error=str(e));raise
            finally:
                receipt['host_runtime_seconds']=time.monotonic()-started
                atomic_json(target/'invocation.json',receipt)
        print(json.dumps({'report_directory':str(target),'report':report},ensure_ascii=False));return 0
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as e:
        p.error(str(e))


if __name__=='__main__':raise SystemExit(main())
