"""Bounded, staged V3 experiments. Never publish, register, or run case003/005.

Native Windows checks rebuild historical V2/Final for the same compiler; these
are labelled native rebuilds, never confused with registered Linux binaries.
Official scoring requires a working Linux backend and explicit --score.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zipfile
from common import PROJECT,OUTPUTS,backend_command,linux_path,safe_label,sha256,catalog
from manage_router_versions import git_sources


def save_new(path,data):
    with Path(path).open('x',encoding='utf8') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n')


class WindowsPeak:
    """Observe only the Router PID launched by this experiment's run_case."""
    def __init__(self,exe):self.exe=exe.resolve();self.handle=None;self.peak=None
    def sample(self,directory):
        if os.name!='nt':return
        from ctypes import wintypes as w
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=w.HANDLE;kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]
        kernel.CloseHandle.argtypes=[w.HANDLE]
        if self.handle is None:
            try:
                entries=(directory/'host_monitor.jsonl').read_text(encoding='utf8').splitlines()
                pid=next((json.loads(line).get('subprocess_pid') for line in reversed(entries) if json.loads(line).get('subprocess_pid')),None)
            except (OSError,ValueError,IndexError,KeyError):return
            if not isinstance(pid,int) or pid<=0:return
            handle=kernel.OpenProcess(0x410,False,pid)
            if not handle:return
            name=ctypes.create_unicode_buffer(32768);size=w.DWORD(len(name))
            query=kernel.QueryFullProcessImageNameW;query.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
            if not query(handle,0,name,ctypes.byref(size)) or Path(name.value).resolve()!=self.exe:
                kernel.CloseHandle(handle);return
            self.handle=handle
        class Counters(ctypes.Structure):
            _fields_=[('cb',w.DWORD),('faults',w.DWORD)]+[(name,ctypes.c_size_t) for name in ['peak_working_set','working_set','quota_peak_paged','quota_paged','quota_peak_nonpaged','quota_nonpaged','pagefile','peak_pagefile']]
        counters=Counters();counters.cb=ctypes.sizeof(counters)
        get=ctypes.WinDLL('psapi').GetProcessMemoryInfo;get.argtypes=[w.HANDLE,ctypes.POINTER(Counters),w.DWORD]
        if get(self.handle,ctypes.byref(counters),counters.cb):self.peak=max(self.peak or 0,counters.peak_working_set)
    def close(self):
        if self.handle:
            from ctypes import wintypes as w
            f=ctypes.WinDLL('kernel32').CloseHandle;f.argtypes=[w.HANDLE];f(self.handle);self.handle=None


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--experiment',required=True)
    p.add_argument('--stage',type=int,choices=[1,2,3],required=True)
    p.add_argument('--backend',choices=['wsl','native'],default='wsl' if os.name=='nt' else 'native')
    p.add_argument('--score',action='store_true');p.add_argument('--router-budget',type=int,default=180);p.add_argument('--evaluation-budget',type=int,default=180)
    p.add_argument('--reference',type=Path,help='Reuse only hash-identical, completed Linux V2/old-Final controls from an earlier stage results.json')
    a=p.parse_args();safe_label(a.experiment)
    if not 1<=a.router_budget<=900 or not 1<=a.evaluation_budget<=900:p.error('Budgets must be 1..900 seconds')
    if a.score and a.backend=='native' and os.name=='nt':p.error('Official Linux evaluator cannot execute natively on Windows')
    if a.reference and a.backend=='native' and os.name=='nt':p.error('Native PE rebuilds have separate identities; Linux references only')
    report=OUTPUTS/'reports/v3_generation'/a.experiment;report.mkdir(parents=True,exist_ok=False)
    config=PROJECT/'configs/v3_final.json';config_sha=sha256(config)
    if a.backend=='wsl':
        check=subprocess.run(['wsl','-d','Ubuntu','--','/bin/true'],capture_output=True,timeout=25)
        (report/'wsl_preflight.log').write_bytes(check.stdout+check.stderr)
        if check.returncode:
            save_new(report/'preflight.json',{'status':'BLOCKED_ENVIRONMENT','returncode':check.returncode,'official_status':'NOT_EVALUATED','published':False});return 2
    windows=a.backend=='native' and os.name=='nt';rows=[]
    reference=json.loads(a.reference.read_text(encoding='utf8'))['rows'] if a.reference else []
    flags=['-std=c++17','-O3','-DNDEBUG','-Wall','-Wextra','-I','include'];sources=['src/main.cpp','src/parser.cpp','src/router.cpp']
    def compile_at(cwd,exe,macros,log):
        args=['g++',*flags,*macros,*sources,'-o',str(exe) if windows else linux_path(exe),*(['-lshell32'] if windows else [])]
        command=args if windows else backend_command(args,a.backend,cwd=cwd)
        with (report/log).open('xb') as f:result=subprocess.run(command,cwd=cwd,stdout=f,stderr=subprocess.STDOUT,timeout=240)
        if result.returncode:raise RuntimeError('Build failed: '+log)
        return command
    # ASCII system-temp paths also avoid MinGW's narrow Unicode linker argv.
    with tempfile.TemporaryDirectory(prefix='gr_v3_research_') as temp:
        work=Path(temp);variants=[];suffix='.exe' if windows else ''
        refs=[('v2','a0f8e717ebbeca32ef999c8caf398fec46ba41c2',[]),('old_final','e30e0ac26f1e8542077bac24158586043bd2aec5',['-DGR_V3_FINAL'])]
        for name,ref,macros in refs:
            if not windows:
                exe=PROJECT/'build'/('router_v2' if name=='v2' else 'router_v3_final');variants.append((name,exe));continue
            folder=work/name;folder.mkdir();values=git_sources(ref)
            for relative,data in values.items():path=folder/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            exe=work/(name+suffix);command=compile_at(folder,exe,macros,name+'_build.log');variants.append((name,exe))
            save_new(report/(name+'_build.json'),{'commit':ref,'native_rebuild':True,'binary_sha256':sha256(exe),'command':command})
        exe=work/('stage_'+str(a.stage)+suffix)
        source_hashes={str(f.relative_to(PROJECT)):sha256(f) for folder in ['src','include'] for f in (PROJECT/folder).glob('*') if f.is_file()}
        command=compile_at(PROJECT,exe,['-DGR_V3_FINAL','-DGR_V3_STAGE='+str(a.stage)],'candidate_build.log')
        manifest={'stage':a.stage,'binary_sha256':sha256(exe),'config_sha256':config_sha,'sources':source_hashes,'command':command,'native_windows':windows,'registered':False,'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT).decode().strip()}
        save_new(report/'build.json',manifest)
        with zipfile.ZipFile(report/'sources.zip','x',zipfile.ZIP_DEFLATED) as z:
            for name in source_hashes:z.write(PROJECT/name,name.replace('\\','/'))
            z.writestr('manifest.json',json.dumps(manifest,indent=2))
        variants.append(('new_v3',exe))
        for case in ['toy','sample001','open_3d_case001']:
            for name,executable in variants:
                if sha256(config)!=config_sha or any(sha256(PROJECT/k)!=v for k,v in source_hashes.items()):raise RuntimeError('Source/config changed during stage')
                if reference and name!='new_v3':
                    matches=[r for r in reference if r['case']==case and r['variant']==name]
                    if len(matches)!=1:raise RuntimeError('Reference control missing/ambiguous')
                    old=matches[0];inputs=catalog()[case];identity={k:sha256(inputs[k]) for k in ['def','lef','thermal']}
                    if old['status']!='ROUTED' or old['router_sha256']!=sha256(executable) or old['config_sha256']!=config_sha or old['input_sha256']!=identity or old['backend']!=a.backend:raise RuntimeError('Reference control identity changed')
                    if a.score and (old['official_status']!='PASS' or old.get('evaluator_sha256')!=sha256(PROJECT.parent/'\u8bc4\u4f30\u7a0b\u5e8f/score')):raise RuntimeError('Reference official evaluation missing or evaluator changed')
                    row=old|{'reference_report':str(a.reference.resolve()),'reused_control':True};save_new(report/(case+'_'+name+'.json'),row);rows.append(row);continue
                label=safe_label('gen_'+a.experiment+'_'+name);directory=OUTPUTS/'runs'/case/label
                if directory.exists():raise RuntimeError('Historical label conflict: '+label)
                args=[sys.executable,'-B',str(PROJECT/'scripts/run_case.py'),'--case',case,'--label',label,'--router',str(executable),'--backend',a.backend,'--config',str(config),'--time-limit',str(a.router_budget)]
                if case!='open_3d_case001':args+=['--trace']
                print(case,name,flush=True);peak=WindowsPeak(executable)
                with (report/(case+'_'+name+'.log')).open('xb') as f:
                    child=subprocess.Popen(args,cwd=PROJECT,stdout=f,stderr=subprocess.STDOUT);end=time.monotonic()+a.router_budget+90
                    try:
                        while child.poll() is None:
                            if time.monotonic()>end:child.terminate();raise TimeoutError('Host experiment budget exceeded')
                            if windows:peak.sample(directory)
                            time.sleep(.1)
                        if windows:peak.sample(directory)
                    finally:peak.close()
                run=json.loads((directory/'run.json').read_text(encoding='utf8'));row={'case':case,'variant':name,'label':label,'status':run['status'],'returncode':run['returncode'],'backend':a.backend,'official_status':'NOT_EVALUATED','runtime_seconds':run.get('runtime_seconds'),'native_observed_peak_working_set_bytes':peak.peak,'router_sha256':sha256(executable),'config_sha256':config_sha,'input_sha256':run['input_sha256']}
                row['peak_rss_bytes']=(run.get('supervisor_exit') or {}).get('child_peak_rss_bytes') or run.get('router_peak_rss_bytes')
                if run['status']=='ROUTED':
                    data=json.loads((directory/'result.json').read_text(encoding='utf8'));row.update(net_count=len(data['nets']),metrics=data['metrics'],algorithm=data.get('v3_algorithm'),guide_sha256=sha256(directory/'route.guide'))
                    if name=='new_v3' and (data.get('v3_algorithm') or {}).get('stage')!=a.stage:raise RuntimeError('Candidate did not execute selected stage')
                    if case!='open_3d_case001':
                        from compare_router_v3 import replay
                        row['trace_replay']=replay(directory,data)
                    if a.score:
                        with (report/(case+'_'+name+'_score.log')).open('xb') as f:subprocess.run([sys.executable,'-B',str(PROJECT/'scripts/evaluate.py'),'--case',case,'--label',label,'--backend',a.backend,'--engine','latest','--timeout',str(a.evaluation_budget)],cwd=PROJECT,stdout=f,stderr=subprocess.STDOUT,timeout=a.evaluation_budget+75)
                        invocation=json.loads((directory/'official_latest/invocation.json').read_text(encoding='utf8'));row.update(official_status=invocation.get('official_status','NOT_EVALUATED'),official_report=invocation.get('report'),evaluator_sha256=invocation.get('evaluator_sha256'))
                save_new(report/(case+'_'+name+'.json'),row);rows.append(row)
                if run['status']!='ROUTED' or a.score and row['official_status']!='PASS':
                    save_new(report/'results.json',{'rows':rows,'published':False,'incomplete':True});return 2
        save_new(report/'results.json',{'rows':rows,'published':False,'official_scoring_requested':a.score,'stage':a.stage})
    return 0


if __name__=='__main__':raise SystemExit(main())
