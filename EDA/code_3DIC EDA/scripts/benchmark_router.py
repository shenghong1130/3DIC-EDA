#!/usr/bin/env python3
"""Build/profile small phase-two fixtures, never start public large Cases.

Each new --stage uses new files. Existing stages/binaries are not overwritten.
Uses the same C++17 -O3 -DNDEBUG options as build.sh; GR_PROFILE is opt-in.
"""
import argparse,json,subprocess,time
from pathlib import Path
from common import PROJECT,OUTPUTS,backend_command,linux_path,sha256,peak_rss,safe_label

REPORT=OUTPUTS/'reports/algorithm_optimization'
FLAGS=['-std=c++17','-O3','-DNDEBUG','-Wall','-Wextra','-DGR_PROFILE','-I','include']

def save(path,value):
    with path.open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2)

def compile_binary(name,main,report):
    binary=PROJECT/'build'/name
    if binary.exists():raise RuntimeError('Build output exists; choose a new stage: '+str(binary))
    cmd=['g++',*FLAGS,main,'src/parser.cpp','src/router.cpp','-o',str(binary.relative_to(PROJECT)).replace('\\','/')]
    with (report/(name+'.build.log')).open('xb') as log:
        subprocess.run(backend_command(cmd,'wsl',cwd=PROJECT),cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=240)
    return {'path':str(binary),'sha256':sha256(binary),'command':cmd}

def normalized(path):
    import hashlib
    data=json.loads(path.read_text(encoding='utf8'));data['metrics'].pop('runtime_seconds',None)
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf8')).hexdigest()

def measure(stage,binary,report,repeats,compare=None):
    rows=[]
    for rep in range(repeats):
        modes=['astar','tsv','reroute'] if rep%2==0 else ['reroute','tsv','astar']
        for mode in modes:
            variants=[(stage,binary)] if compare is None else [('baseline',compare),(stage,binary)]
            if compare is not None and rep%2:variants.reverse()
            for variant,exe in variants:
                prefix=report/(mode+'_'+str(rep)+('_'+variant if compare is not None else ''));profile=prefix.with_suffix('.profile.json');usage=prefix.with_suffix('.time.txt')
                cmd=backend_command([linux_path(Path(exe)),mode,linux_path(prefix)],'wsl',cwd=PROJECT,
                    time_file=usage,env={'GR_PROFILE_PATH':linux_path(profile)},timeout=90)
                with prefix.with_suffix('.log').open('xb') as log:
                    started=time.perf_counter();subprocess.run(cmd,cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120);wall=time.perf_counter()-started
                output=None
                for line in prefix.with_suffix('.log').read_bytes().splitlines():
                    try:
                        # WSL may prefix its UTF-8 payload with a UTF-16LE proxy
                        # warning. Preserve raw log bytes; parse only the JSON line.
                        clean=line.replace(b'\0',b'');start=clean.find(b'{')
                        if start<0:continue
                        item=json.loads(clean[start:])
                        if isinstance(item,dict) and item.get('fixture')==mode:output=item
                    except (ValueError,UnicodeError):pass
                if output is None:raise RuntimeError('Benchmark completion record missing')
                row={'stage':variant,'fixture':mode,'repeat':rep,'wall_seconds_with_wsl_and_output':wall,
                    'peak_rss_bytes':peak_rss(usage),**output,'profile':json.loads(profile.read_text(encoding='utf8')),
                    'guide_sha256':sha256(prefix.with_suffix('.guide')),'normalized_json_sha256':normalized(prefix.with_suffix('.result.json'))}
                rows.append(row);print(json.dumps({k:row[k] for k in ['stage','fixture','repeat','routing_seconds','peak_rss_bytes']},ensure_ascii=False),flush=True)
    save(report/'measurements.json',rows);return rows

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',required=True);p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--build-only',action='store_true');p.add_argument('--reuse-stage',help='Reuse and hash-check an existing stage binary in a fresh report directory')
    p.add_argument('--compare-stage',help='Interleave the selected stage with this existing benchmark binary (AB/BA order)')
    a=p.parse_args();safe_label(a.stage)
    if not 1<=a.repeats<=20:p.error('repeats must be between 1 and 20')
    report=REPORT/'stages'/a.stage;report.mkdir(parents=True,exist_ok=False)
    (PROJECT/'build').mkdir(exist_ok=True)
    builds={};source_hashes=None
    if a.reuse_stage:
        safe_label(a.reuse_stage);previous=json.loads((REPORT/'stages'/a.reuse_stage/'builds.json').read_text(encoding='utf8'))
        builds=previous['builds'];source_hashes=previous['source_sha256']
        for record in builds.values():
            if sha256(Path(record['path']))!=record['sha256']:raise RuntimeError('Reused binary hash changed')
    else:
        for suffix,source in [('profile','src/main.cpp'),('bench','tests/router_benchmark.cpp'),('core','tests/core_tests.cpp')]:
            builds[suffix]=compile_binary('router_'+a.stage+'_'+suffix,source,report)
    with (report/'core_test.log').open('xb') as log:
        subprocess.run(backend_command([linux_path(Path(builds['core']['path']))],'wsl',cwd=PROJECT),stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
    save(report/'builds.json',{'stage':a.stage,'reused_from':a.reuse_stage,'builds':builds,'source_sha256':source_hashes or {str(f.relative_to(PROJECT)):sha256(f) for folder in ['src','include'] for f in (PROJECT/folder).glob('*') if f.is_file()}})
    compare=None
    if a.compare_stage:
        safe_label(a.compare_stage);old=json.loads((REPORT/'stages'/a.compare_stage/'builds.json').read_text(encoding='utf8'))['builds']['bench']
        if sha256(Path(old['path']))!=old['sha256']:raise RuntimeError('Comparison binary hash changed')
        save(report/'comparison_binary.json',old);compare=old['path']
    if not a.build_only:measure(a.stage,builds['bench']['path'],report,a.repeats,compare)
    return 0
if __name__=='__main__':raise SystemExit(main())
