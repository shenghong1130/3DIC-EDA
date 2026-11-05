"""Exclusive local peer-study builds. No official records, Git or remote writes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from common import PROJECT
from diagnostics import windows_memory

REPORT = PROJECT / 'outputs/reports/v4_4_peer_analysis'
DEV = PROJECT / 'outputs/development/v4_4_peer_analysis'
FLAGS = ['GR_V3_FINAL', 'GR_V3_5', 'GR_V3_6', 'GR_V4', 'GR_V4_2', 'GR_V4_3', 'GR_V4_4']


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def source_hashes():
    return {p.relative_to(PROJECT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ['include', 'src', 'tests'] for p in sorted((PROJECT/folder).glob('*')) if p.is_file()}


def native_tests(attempt):
    """Compile serially on Windows; does not start WSL or claim Linux validation."""
    if not attempt or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for c in attempt):
        raise ValueError('Invalid build attempt name')
    folder = DEV / attempt
    folder.mkdir(parents=True, exist_ok=False)
    logs = REPORT / attempt
    logs.mkdir(parents=True, exist_ok=False)
    identity = source_hashes()
    save(logs/'source_identity.json', identity)

    def launch(name, command):
        memory = windows_memory()
        if memory.get('commit_available_bytes', 0) < 2*2**30:
            save(logs/(name+'.json'), {'status': 'BLOCKED_RESOURCE', 'memory': memory})
            raise RuntimeError('BLOCKED_RESOURCE: no compiler/test launched')
        began = time.monotonic()
        with (logs/(name+'.log')).open('xb') as f:
            try:
                proc = subprocess.run(command, cwd=PROJECT, stdout=f, stderr=subprocess.STDOUT, timeout=600)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                code = None
        save(logs/(name+'.json'), {'command': command, 'returncode': code, 'seconds': time.monotonic()-began,
                                  'windows_before': memory, 'windows_after': windows_memory(), 'platform': 'Windows MinGW; not Linux'})
        print(name, code, flush=True)
        if code != 0:
            raise RuntimeError('Build/test failed; see '+str(logs/(name+'.log')))

    flags = ['-D'+f for f in FLAGS]
    def compile(name, source, extra=()):
        target = (folder/(name+'.o')).relative_to(PROJECT)
        launch(name, ['g++', '-std=c++17', '-O1', '-Iinclude', *flags, *extra, '-c', source, '-o', str(target)])
        return str(target)

    parser = compile('parser', 'src/parser.cpp')
    base_router = compile('baseline_router', 'src/router.cpp')
    base_main = compile('baseline_main', 'src/main.cpp')
    baseline = (folder/'router_v44_baseline.exe').relative_to(PROJECT)
    launch('baseline_link', ['g++', base_main, parser, base_router, '-lshell32', '-o', str(baseline)])
    exp = ['-DGR_V44_CONGESTION_EXP01']
    # Test hooks are compiled into BOTH the Router and test translation units.
    hooks = ['-DGR_V43_TEST', '-DGR_V44_TEST']
    candidate_router = compile('candidate_router', 'src/router.cpp', exp)
    candidate_main = compile('candidate_main', 'src/main.cpp', exp)
    candidate = (folder/'router_v4_4_congestion_exp01.exe').relative_to(PROJECT)
    launch('candidate_link', ['g++', candidate_main, parser, candidate_router, '-lshell32', '-o', str(candidate)])
    test_router = compile('test_router', 'src/router.cpp', exp+hooks)
    test_main = compile('test_main', 'tests/v44_regression.cpp', exp+hooks)
    tests = (folder/'v44_exp01_regression.exe').relative_to(PROJECT)
    launch('tests_link', ['g++', test_main, parser, test_router, '-lshell32', '-o', str(tests)])
    launch('regression', [str(tests)])
    launch('baseline_version', [str(baseline), '--version'])
    launch('candidate_version', [str(candidate), '--version'])
    if identity != source_hashes():
        raise RuntimeError('Source changed during compilation; reject build identity')
    save(logs/'complete.json', {'status': 'NATIVE_COMPILED_TESTED', 'source': identity,
                               'binary': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [baseline,candidate]},
                               'linux_validation': 'NOT_RUN'})


def experiment(case, variant, build_attempt):
    from common import catalog, sha256, timed_process
    from compare_v36 import small_metadata
    from validate_v4_3 import output_audit
    if case not in ['toy','sample001','open_3d_case001'] or variant not in ['baseline','initial_tsv','disabled']:
        raise ValueError('Only explicitly scoped small studies supported')
    receipt = json.loads((REPORT/build_attempt/'complete.json').read_text(encoding='utf8'))
    if receipt['source'] != source_hashes(): raise ValueError('Source changed after build')
    memory = windows_memory()
    if memory.get('commit_available_bytes',0) < 2*2**30:
        print('BLOCKED_RESOURCE', memory);return
    binary = DEV/build_attempt/('router_v44_baseline.exe' if variant=='baseline' else 'router_v4_4_congestion_exp01.exe')
    if sha256(binary) != receipt['binary'][binary.name]:raise ValueError('Binary changed')
    folder = REPORT/'experiments'/case/variant
    folder.mkdir(parents=True,exist_ok=False)
    config = PROJECT/'configs'/('v4_4.json' if variant=='baseline' else 'v4_4_congestion_exp01.json')
    if variant=='disabled':
        value=json.loads(config.read_text());value['exp01_initial_tsv']=0
        config=folder/'config.json';save(config,value)
    inputs={k:Path(catalog()[case][k]) for k in ['def','lef','thermal']}
    budget=600 if case=='open_3d_case001' else 180
    command=[str(binary),'--def',str(inputs['def']),'--lef',str(inputs['lef']),'--thermal-report',str(inputs['thermal']),
             '--config',str(config),'--output',str(folder/'route.guide'),'--metrics',str(folder/'result.json'),
             '--time-limit',str(budget),'--progress-dir',str(folder)]
    save(folder/'identity.json',{'case':case,'variant':variant,'router_sha256':sha256(binary),'config_sha256':sha256(config),
         'config':json.loads(config.read_text()),'input_sha256':{k:sha256(p) for k,p in inputs.items()},'command':command,
         'platform':'Windows native MinGW O1','source_identity':str(REPORT/build_attempt/'complete.json'),'memory_before':memory})
    # The C++ progress monitor owns resource_monitor.csv. Host ResourceMonitor
    # would race that exclusive file and disable C++ progress on Windows.
    execution=timed_process(command,folder/'router.log',budget+30,cwd=PROJECT,monitor_dir=folder,native_resources=False)
    save(folder/'execution.json',execution)
    row={'case':case,'variant':variant,'execution':execution,'router_status':'FAILED','reference_status':'NOT_EVALUATED'}
    if execution['returncode']==0 and (folder/'route.guide').is_file():
        row.update(router_status='ROUTED',guide_sha256=sha256(folder/'route.guide'),
                   algorithm=small_metadata(folder/'result.json'),audit=output_audit(folder/'result.json'))
    save(folder/'summary.json',row)
    print(case,variant,row['router_status'],flush=True)


def score_existing():
    """256 MiB RLIMIT_AS scoring only; never builds or starts a Router."""
    from common import catalog, sha256, linux_path, backend_command
    from evaluate_reference_20261103 import source_identity
    from validate_v4_3 import gate
    binary=PROJECT/'outputs/development/v4_3/reference_20261103'
    source='9a33e788a847f356bdea51fc596ea6fe66e1d22f606c049bf7eb62021caf8b51'
    expected='8ab0ed59a85ac7003cdacfe31bac93412591ee1717d46b2ab0ba8b44a4fdd271'
    if source_identity()[0]!=source or sha256(binary)!=expected:raise ValueError('Frozen reference identity mismatch')
    before=windows_memory()
    gate_path=REPORT/'reference_gates'/(str(time.time_ns())+'.json')
    # This is not the full-build/Router gate: reference has a hard 256 MiB AS
    # limit and historical case001 peak RSS 19,091,456 B. Reserve 768 MiB on
    # the host AFTER cold WSL startup, beyond its entire allowed address space.
    if before.get('commit_available_bytes',0)<3*2**30:
        save(gate_path,{'status':'BLOCKED_RESOURCE','windows':before,'reason':'3 GiB cold-WSL headroom unavailable'})
        print('REFERENCE BLOCKED_RESOURCE before WSL');return
    check=gate('open_3d_case001');after=windows_memory()
    allowed=check['allowed'] and after.get('commit_available_bytes',0)>=2**30
    save(gate_path,{'allowed':allowed,'probe':check,'windows_after':after,
         'memory_limit_mb':256,'post_start_host_minimum':2**30,'scope':'bounded scorer only; no compiler/Router',
         'historical_reference_peak_rss_bytes':19091456})
    if not allowed:print('REFERENCE BLOCKED_RESOURCE after WSL');return
    for case in ['toy','sample001','open_3d_case001']:
        for variant in ['baseline','initial_tsv']:
            folder=REPORT/'experiments'/case/variant
            original=json.loads((folder/'identity.json').read_text(encoding='utf8'))
            run=json.loads((folder/'summary.json').read_text(encoding='utf8'))
            inputs={k:Path(catalog()[case][k]) for k in ['lef','def','thermal']}
            if run['router_status']!='ROUTED' or sha256(folder/'route.guide')!=run['guide_sha256']:raise ValueError('Incomplete/changed Guide')
            if original['input_sha256']!={k:sha256(p) for k,p in inputs.items()}:raise ValueError('Input identity changed')
            output=folder/'reference'
            if output.exists():
                receipt=json.loads((output/'invocation.json').read_text(encoding='utf8'))
                if receipt.get('returncode')==0 and receipt.get('guide_sha256')==run['guide_sha256'] and receipt.get('reference_source_sha256')==source and receipt.get('reference_scorer_sha256')==expected and receipt.get('input_sha256')==original['input_sha256'] and (output/'reference_report.json').is_file():
                    print('Reusing verified reference receipt',case,variant);continue
                raise ValueError('Existing incomplete/conflicting reference directory preserved: '+str(output))
            m=windows_memory()
            if m.get('commit_available_bytes',0)<2**30:print('REFERENCE BLOCKED_RESOURCE',case,variant);return
            output.mkdir(exist_ok=False)
            args=[linux_path(binary),linux_path(inputs['lef']),linux_path(inputs['def']),linux_path(inputs['thermal']),
                  linux_path(folder/'route.guide'),linux_path(output),'--memory-mb','256','--pin-assignment','nearest_in_cell']
            command=backend_command(args,'wsl',cwd=PROJECT,timeout=120)
            began=time.monotonic();code=None;error=None
            with (output/'reference.log').open('xb') as f:
                try:code=subprocess.run(command,cwd=PROJECT,stdout=f,stderr=subprocess.STDOUT,timeout=135).returncode
                except subprocess.TimeoutExpired:error='TIMEOUT_ANALYSIS_ERROR'
            save(output/'invocation.json',{'command':command,'returncode':code,'seconds':time.monotonic()-began,'error':error,
                 'windows_before':m,'windows_after':windows_memory(),'reference_source_sha256':source,
                 'reference_scorer_sha256':expected,'input_sha256':original['input_sha256'],'guide_sha256':run['guide_sha256'],
                 'official':False,'memory_limit_mb':256})
            print(case,variant,'REFERENCE exit',code,flush=True)
            if code!=0:return  # preserve evidence; do not retry or misclassify Guide


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--native-tests', action='store_true')
    p.add_argument('--score-existing', action='store_true')
    p.add_argument('--attempt', default='native_build')
    p.add_argument('--case', choices=['toy','sample001','open_3d_case001'])
    p.add_argument('--variant', choices=['baseline','initial_tsv','disabled'])
    args = p.parse_args()
    if args.score_existing:
        score_existing()
    elif args.native_tests:
        native_tests(args.attempt)
    elif args.case and args.variant:
        experiment(args.case,args.variant,args.attempt)
    else:
        p.error('Choose --native-tests or --case and --variant')
