#!/usr/bin/env python3
"""Register immutable router_vN copies using an independent Git-source rebuild.

--list / --verify never compile. --register explicitly rebuilds in a temporary
directory, compares SHA256 and checks --help; no routing or Git writes occur.
"""
import argparse,contextlib,datetime,hashlib,json,os,shutil,subprocess,tempfile
from pathlib import Path
from common import PROJECT,backend_command,linux_path,sha256,resolve_router
from router_versions import REGISTRY,VERSION,METHOD,load_registry,verified_record,display_path,version_key

FLAGS=['-std=c++17','-O3','-DNDEBUG','-Wall','-Wextra','-I','include']
SOURCES=['src/main.cpp','src/parser.cpp','src/router.cpp']

def git(*args):return subprocess.check_output(['git',*args],cwd=PROJECT)
def commit_id(ref):return git('rev-parse','--verify',ref+'^{commit}').decode().strip()
def git_sources(commit):
    prefix=git('rev-parse','--show-prefix').decode().strip()
    paths=git('ls-tree','-rz','--full-tree','--name-only',commit,'--',prefix+'src/',prefix+'include/').split(b'\0')
    result={}
    for raw in paths:
        if not raw:continue
        name=raw.decode('utf8');relative=name[len(prefix):]
        if Path(relative).suffix not in {'.cpp','.hpp','.h'}:continue
        result[relative]=git('show',commit+':'+name)
    if not all(p in result for p in SOURCES):raise ValueError('Git commit lacks the Router production sources')
    return result

def source_hashes(values):return {p:hashlib.sha256(b).hexdigest() for p,b in sorted(values.items())}
def elf(path):
    with Path(path).open('rb') as f:return f.read(4)==b'\x7fELF'

def help_check(path):
    cmd=backend_command([linux_path(path),'--help'],'wsl',cwd=PROJECT) if os.name=='nt' else [str(path),'--help']
    result=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
    required=[b'--def',b'--lef',b'--thermal-report',b'--output']
    if result.returncode or not all(k in result.stdout for k in required):raise ValueError('Binary does not expose the standard Router CLI: '+str(path))
    return result.stdout.decode('utf8',errors='replace')

def rebuild(commit,source,final=False,batch=False):
    values=git_sources(commit);hashes=source_hashes(values)
    # TemporaryDirectory owns only its newly created child of build/. The
    # workspace's source files, old binaries and results are never moved.
    build=PROJECT/'build';build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.router_verify_',dir=build) as name:
        temp=Path(name).resolve()
        if temp.parent!=build.resolve():raise ValueError('Temporary build escaped build/')
        for relative,data in values.items():
            f=temp/relative;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(data)
        (temp/'build').mkdir();candidate=temp/'build/candidate'
        cmd=['g++',*FLAGS,*(['-DGR_V3_FINAL'] if final or batch else []),*(['-DGR_V3_2'] if batch else []),*SOURCES,'-o','build/candidate']
        backend='wsl' if os.name=='nt' else 'native'
        version=subprocess.run(backend_command(['g++','--version'],backend),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=20).stdout.decode('utf8')
        target=subprocess.run(backend_command(['g++','-dumpmachine'],backend),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=20).stdout.decode().strip()
        compiled=subprocess.run(backend_command(cmd,backend,cwd=temp),cwd=temp,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=240)
        if compiled.returncode:raise ValueError('Independent build failed: '+compiled.stderr.decode('utf8',errors='replace'))
        digest=sha256(candidate)
        if digest!=sha256(source):raise ValueError('Rebuilt SHA256 differs: candidate is not this committed production build (or compiler/options differ)')
        help_output=help_check(source)
        return {'compiler':version,'compiler_target':target,'build_command':cmd,'verification':{
            'method':METHOD,'rebuilt_sha256':digest,'source_sha256':hashes,
            'cli_help_returncode':0,'cli_required_options_verified':True,'cli_help':help_output,
            'build_stdout':compiled.stdout.decode('utf8',errors='replace'),'build_stderr':compiled.stderr.decode('utf8',errors='replace'),
            'verified_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}}

def inspect(version,record):
    try:
        if not verified_record(version,record):return False,'Incomplete registration proof'
        file=PROJECT/record['path']
        if not file.is_file():return False,'NOT_BUILT'
        if not elf(file) or sha256(file)!=record['sha256']:return False,'Binary hash/type mismatch'
        if source_hashes(git_sources(record['git_commit']))!=record['verification']['source_sha256']:return False,'Git source proof mismatch'
        return True,'SHA256 and Git source proof match'
    except (OSError,ValueError,subprocess.SubprocessError) as e:return False,str(e)

def publish_json(path,data):
    path.parent.mkdir(exist_ok=True,parents=True)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf8',dir=path.parent,prefix='.'+path.name+'_',suffix='.tmp',delete=False) as f:
        tmp=Path(f.name);json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    try:os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()

@contextlib.contextmanager
def registry_lock():
    REGISTRY.parent.mkdir(exist_ok=True)
    lock=REGISTRY.parent/'.router_versions.lock'
    with lock.open('x') as f:f.write(str(os.getpid()))
    try:yield
    finally:lock.unlink()

def register(version,source,ref,description,final=False,architecture=False,batch=False):
    architecture=architecture or batch
    if not VERSION.fullmatch(version):raise ValueError('Version must be v1, v2, v3_1, v3_2, ...')
    if architecture and not version.startswith('v3_'):raise ValueError('V3 architecture requires a numeric v3_N version')
    if final and version!='v3':raise ValueError('Final entry is only valid for v3')
    source=resolve_router(source);dest=PROJECT/'build'/('router_v3_final' if final else 'router_'+version)
    with registry_lock():
        data=load_registry() if REGISTRY.exists() else {'schema_version':1,'versions':{}}
        if version in data['versions'] or dest.is_symlink() or (dest.exists() and not ((final or architecture) and source==dest.resolve())):raise ValueError('Version/file already exists; never overwrite: '+version)
        if not source.is_file() or not elf(source):raise ValueError('Registration requires an existing Linux ELF production Router')
        digest=sha256(source);commit=commit_id(ref);hashes=source_hashes(git_sources(commit))
        for name,candidate in data.get('candidates',{}).items():
            if name!=version and digest==candidate.get('sha256'):raise ValueError('This binary is already identified as candidate '+name+'; do not create an alias version')
            if name==version and digest!=candidate.get('sha256'):raise ValueError('Candidate bytes changed; never reuse its immutable version identity')
        for name,record in data['versions'].items():
            if digest==record.get('sha256') or hashes==record.get('verification',{}).get('source_sha256'):
                raise ValueError('This binary/source is already registered as '+name+'; do not relabel it as '+version)
        proof=rebuild(commit,source,final=True,batch=True) if batch else rebuild(commit,source,final=True) if final or architecture else rebuild(commit,source)
        entry={'path':dest.relative_to(PROJECT).as_posix(),'source':display_path(source),'git_commit':commit,
               'sha256':digest,'description':description,'verified':True,**proof}
        # Exclusive destination creation, even if another writer raced the lock.
        if source!=dest.resolve():
            with source.open('rb') as src,dest.open('xb') as target:shutil.copyfileobj(src,target)
            shutil.copymode(source,dest)
        if final:entry['build_variant']='v3_final'
        if architecture:entry.update(build_variant='v3_architecture',release_status='VERIFIED_RESEARCH_BASELINE_NOT_DEFAULT',config_path='configs/'+version+'.json',run_label_pattern='{case_key}_'+version)
        if batch:entry['build_variant']='v3_hotspot_batch'
        previous_candidate=data.get('candidates',{}).get(version,{})
        if previous_candidate.get('validation_report'):entry['validation_report']=previous_candidate['validation_report']
        if previous_candidate.get('release_status','').startswith('NOT_RELEASED'):entry['release_status']=previous_candidate['release_status']
        if sha256(dest)!=digest:raise ValueError('Copied file changed; registry not published; inspect '+str(dest))
        help_check(dest);data['versions'][version]=entry
        data.get('candidates',{}).pop(version,None)
        publish_json(REGISTRY,data)
        print('Registered '+version.upper()+' '+entry['path']+' '+digest)

def main():
    p=argparse.ArgumentParser(description=__doc__);mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--list',action='store_true');mode.add_argument('--verify',action='store_true');mode.add_argument('--register',metavar='vN')
    p.add_argument('--source',type=Path);p.add_argument('--git-commit');p.add_argument('--description');p.add_argument('--rebuild',action='store_true',help='With --verify, independently rebuild again')
    p.add_argument('--final-entry',action='store_true',help='Register the already validated build/router_v3_final with its dedicated build flags')
    p.add_argument('--v3-architecture',action='store_true',help='Numeric version built with GR_V3_FINAL; independent committed-source rebuild required')
    p.add_argument('--hotspot-batch',action='store_true',help='Numeric hotspot architecture: GR_V3_FINAL + GR_V3_2; never bypasses committed rebuild proof')
    a=p.parse_args()
    if a.register:
        if not all([a.source,a.git_commit,a.description]):p.error('--register requires --source, --git-commit and --description')
        if a.rebuild:p.error('--rebuild is for --verify')
        if a.final_entry and (a.v3_architecture or a.hotspot_batch):p.error('Choose one build variant')
        register(a.register,a.source,a.git_commit,a.description,final=a.final_entry,architecture=a.v3_architecture,batch=a.hotspot_batch)
        return 0
    if a.source or a.git_commit or a.description or a.final_entry or a.v3_architecture or a.hotspot_batch or a.rebuild and not a.verify:p.error('Unexpected registration/verification option')
    try:data=load_registry()
    except (OSError,ValueError) as e:print('Registry unavailable: '+str(e));return 1
    valid=True
    print('| Version | File | SHA256 | Verified | Detail |\n|---|---|---|---|---|')
    versions=set(data['versions'])|set(data.get('candidates',{}))|{'v1','v2'}
    versions.update(p.name.removeprefix('router_') for p in (PROJECT/'build').glob('router_v*') if VERSION.fullmatch(p.name.removeprefix('router_')))
    if any(not VERSION.fullmatch(v) for v in versions):raise ValueError('Invalid registry version key')
    counts={}
    for record in data['versions'].values():
        if isinstance(record,dict):counts[record.get('sha256')]=counts.get(record.get('sha256'),0)+1
    for v in sorted(versions,key=version_key):
        if v not in data['versions']:
            if v in data.get('candidates',{}):
                record=data['candidates'][v];file=PROJECT/record['path'];match=file.is_file() and sha256(file)==record.get('sha256');valid=False
                print('| '+v.upper()+' | '+record['path']+' | '+(sha256(file) if file.is_file() else 'N/A')+' | NO | '+('CANDIDATE_NOT_RELEASED; SHA256 matches receipt' if match else 'Candidate binary missing/hash mismatch')+' |');continue
            file=PROJECT/'build'/('router_v3_final' if v=='v3' and (PROJECT/'build/router_v3_final').exists() else 'router_'+v);detail='UNREGISTERED' if file.exists() else 'NOT_BUILT'
            actual=sha256(file) if file.is_file() else 'N/A'
            if file.exists():valid=False
            print('| '+v.upper()+' | '+detail+' | '+actual+' | NO | No verified registration |');continue
        record=data['versions'][v];ok,reason=inspect(v,record)
        if counts.get(record.get('sha256'),0)>1:ok=False;reason='Ambiguous SHA256 shared by multiple version labels'
        if ok and a.verify:
            help_check(PROJECT/record['path'])
            if a.rebuild:
                if record.get('build_variant')=='v3_hotspot_batch':rebuild(record['git_commit'],PROJECT/record['path'],final=True,batch=True)
                elif record.get('build_variant') in {'v3_final','v3_architecture'}:rebuild(record['git_commit'],PROJECT/record['path'],final=True)
                else:rebuild(record['git_commit'],PROJECT/record['path'])
        valid=valid and ok
        print('| '+v.upper()+' | '+str(record.get('path'))+' | '+str(record.get('sha256'))+' | '+('YES' if ok else 'NO')+' | '+reason+' |')
    return 0 if valid else 1

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,subprocess.SubprocessError) as e:raise SystemExit('ERROR: '+str(e))
