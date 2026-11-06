"""Sequential local V4.5 experiments; exclusive outputs, no Git/official writes."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import threading
import ctypes
from ctypes import wintypes
from common import PROJECT, catalog, sha256, timed_process
from diagnostics import windows_memory
from validate_v44_peer import source_hashes, save, FLAGS

ROOT = PROJECT / 'outputs/reports/v4_5'
DEV = PROJECT / 'outputs/development/v4_5'


class NativeMemory:
    """Read-only Windows working-set sampling of the exact launched child PID."""
    def __init__(self, folder):
        self.folder=folder;self.stop=threading.Event();self.peak=None;self.error=None
        self.thread=threading.Thread(target=self.sample,daemon=True)
    def sample(self):
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(k,ctypes.c_size_t) for k in ['peak','rss','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile']]
        handle=None
        try:
            kernel=ctypes.WinDLL('kernel32',use_last_error=True);psapi=ctypes.WinDLL('psapi',use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
            with (self.folder/'windows_process_memory.jsonl').open('x',encoding='utf8') as f:
                while not self.stop.wait(.25):
                    if handle is None:
                        p=self.folder/'host_monitor.jsonl'
                        if not p.exists():continue
                        records=[json.loads(s) for s in p.read_text(encoding='utf8').splitlines() if s.strip()]
                        pid=next((r['subprocess_pid'] for r in reversed(records) if r.get('subprocess_pid')),None)
                        if pid is None:continue
                        handle=kernel.OpenProcess(0x410,False,pid)
                        if not handle:raise OSError(ctypes.get_last_error(),'OpenProcess')
                    data=Counters();data.cb=ctypes.sizeof(data)
                    if psapi.GetProcessMemoryInfo(handle,ctypes.byref(data),data.cb):
                        self.peak=max(self.peak or 0,data.peak)
                        f.write(json.dumps({'time':time.time(),'pid':pid,'working_set_bytes':data.rss,'peak_working_set_bytes':data.peak,'windows_memory':windows_memory()})+'\n');f.flush()
        except Exception as e:self.error=str(e)
        finally:
            if handle:kernel.CloseHandle(handle)
    def __enter__(self):self.thread.start();return self
    def __exit__(self,*args):self.stop.set();self.thread.join(timeout=5)


def gate():
    memory = windows_memory()
    if memory.get('commit_available_bytes', 0) < 2*2**30:
        save(ROOT/'resource_gates'/(str(time.time_ns())+'.json'), {'status':'BLOCKED_RESOURCE','memory':memory})
        raise RuntimeError('BLOCKED_RESOURCE: 2 GiB native compile/small-run headroom required')
    return memory


def build(attempt):
    folder = DEV/attempt
    folder.mkdir(parents=True, exist_ok=False)
    logs = ROOT/'builds'/attempt
    logs.mkdir(parents=True, exist_ok=False)
    identity = source_hashes()
    flags = ['-D'+s for s in FLAGS+['GR_V4_5']]
    def launch(name, command):
        before = gate();began = time.monotonic()
        with (logs/(name+'.log')).open('xb') as f:
            code = subprocess.run(command, cwd=PROJECT, stdout=f, stderr=subprocess.STDOUT, timeout=600).returncode
        save(logs/(name+'.json'), {'command':command,'returncode':code,'seconds':time.monotonic()-began,'memory_before':before,'memory_after':windows_memory()})
        print(name, code, flush=True)
        if code:raise RuntimeError('Build/test failed: '+str(logs/(name+'.log')))
    def obj(name, source, hooks=()):
        path = str((folder/(name+'.o')).relative_to(PROJECT))
        launch(name,['g++','-std=c++17','-O1','-Iinclude',*flags,*hooks,'-c',source,'-o',path])
        return path
    parser = obj('parser','src/parser.cpp')
    router = obj('router','src/router.cpp')
    main = obj('main','src/main.cpp')
    binary = folder/'router_v4_5.exe'
    launch('link',['g++',main,parser,router,'-lshell32','-o',str(binary.relative_to(PROJECT))])
    launch('version',[str(binary),'--version'])
    for name in ['v45_tsv_assignment_tests','sparse_tsv_assignment_tests']:
        target = str((folder/(name+'.exe')).relative_to(PROJECT))
        launch(name+'_build',['g++','-std=c++17','-O1','-Iinclude','tests/'+name+'.cpp','-o',target])
        launch(name,[target])
    test_router = obj('test_router','src/router.cpp',['-DGR_V45_TEST'])
    test_main = obj('test_main','tests/v45_regression.cpp',['-DGR_V45_TEST'])
    test = str((folder/'v45_regression.exe').relative_to(PROJECT))
    launch('test_link',['g++',test_main,parser,test_router,'-lshell32','-o',test])
    launch('regression',[test])
    if identity != source_hashes():raise RuntimeError('Source changed during build')
    save(logs/'complete.json', {'status':'NATIVE_COMPILED_TESTED','source':identity,'binary':str(binary.relative_to(PROJECT)), 'router_sha256':sha256(binary),'flags':FLAGS+['GR_V4_5'],'platform':'Windows MinGW O1','linux_validation':'NOT_RUN','release_status':'EXPERIMENTAL'})


def experiment(case, variant, attempt, wire, tag=None):
    from compare_v36 import small_metadata
    from validate_v4_3 import output_audit
    receipt = json.loads((ROOT/'builds'/attempt/'complete.json').read_text(encoding='utf8'))
    if receipt['source'] != source_hashes():raise ValueError('Source changed since build')
    binary = PROJECT/receipt['binary']
    if sha256(binary) != receipt['router_sha256']:raise ValueError('Changed binary')
    memory = gate()
    label = variant if wire is None else variant+'_wire_'+str(wire).replace('.','p')
    if tag:
        if any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for c in tag):raise ValueError('Invalid experiment tag')
        label+='_'+tag
    folder = ROOT/'experiments'/case/label
    folder.mkdir(parents=True,exist_ok=False)
    cfg = json.loads((PROJECT/'configs/v4_5.json').read_text(encoding='utf8'))
    switches = {'disabled':(0,0,0,0),'path':(1,0,0,0),'thermal':(0,1,0,0),'phi':(0,0,0,1),'repair':(0,0,1,0),'combined':(1,1,1,2),'strict':(1,1,1,0),'phi_combined':(1,1,1,1)}
    for k,v in zip(['v45_path','v45_thermal','v45_repair','v45_mode'],switches[variant]):cfg[k]=v
    if wire is not None:cfg['v45_wire_growth']=wire
    config = folder/'config.json';save(config,cfg)
    inputs = {k:Path(catalog()[case][k]) for k in ['def','lef','thermal']}
    budget = 600 if case=='open_3d_case001' else 180
    command = [str(binary),'--def',str(inputs['def']),'--lef',str(inputs['lef']),'--thermal-report',str(inputs['thermal']),'--config',str(config),'--output',str(folder/'route.guide'),'--metrics',str(folder/'result.json'),'--time-limit',str(budget),'--progress-dir',str(folder)]
    save(folder/'identity.json',{'case':case,'variant':variant,'router_sha256':sha256(binary),'config_sha256':sha256(config),'config':cfg,'input_sha256':{k:sha256(p) for k,p in inputs.items()},'command':command,'platform':'Windows native MinGW O1','memory_before':memory,'build':str(ROOT/'builds'/attempt/'complete.json')})
    with NativeMemory(folder) as monitor:
        execution = timed_process(command,folder/'router.log',budget+30,cwd=PROJECT,monitor_dir=folder,native_resources=False)
    execution['windows_peak_working_set_bytes']=monitor.peak
    execution['windows_memory_error']=monitor.error
    save(folder/'execution.json',execution)
    row = {'case':case,'variant':variant,'router_status':'FAILED','reference_status':'NOT_EVALUATED','execution':execution}
    if execution['returncode']==0 and (folder/'route.guide').is_file():
        row.update(router_status='ROUTED',guide_sha256=sha256(folder/'route.guide'),algorithm=small_metadata(folder/'result.json'),audit=output_audit(folder/'result.json'))
    save(folder/'summary.json',row)
    print(case,label,row['router_status'],flush=True)
    if row['router_status']!='ROUTED':raise RuntimeError('Router failed; preserve evidence, no automatic retry')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',action='store_true');p.add_argument('--attempt',default='native_01')
    p.add_argument('--case',choices=['toy','sample001','open_3d_case001'])
    p.add_argument('--variant',choices=['disabled','path','thermal','phi','repair','combined','strict','phi_combined'],default='combined')
    p.add_argument('--wire',type=float,choices=[0,.005,.01,.02,.05])
    a=p.parse_args()
    if not a.attempt or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for c in a.attempt):p.error('Invalid attempt')
    if a.build:build(a.attempt)
    elif a.case:experiment(a.case,a.variant,a.attempt,a.wire)
    else:p.error('Choose --build or --case')
