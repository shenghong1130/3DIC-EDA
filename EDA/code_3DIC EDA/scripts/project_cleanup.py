"""Local, file-by-file cleanup with external verified recovery archives.

Never removes tracked files, executables, experimental results, V5/Trace source,
current UI caches, or directories. Audit first, then apply only its exact list.
"""
import argparse
import ast
import collections
import ctypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import zipfile
from contextlib import closing

PROJECT=Path(__file__).resolve().parents[1]
WORKSPACE=PROJECT.parents[1]
REPORT=PROJECT/'outputs/reports/project_cleanup'
ZONE=dt.timezone(dt.timedelta(hours=8))
TEXT={'.py','.ps1','.sh','.bat','.cmd','.cmake','.md','.txt','.json','.jsonl','.cpp','.hpp','.h','.js','.html','.css','.toml','.yaml','.yml','.log'}
REQUIRED_SCRIPTS={'common.py','run_case.py','run_v5_case.py','score_v5_case.py','v5_resource_guard.py','linux_supervisor.py','router_versions.py'}
PROTECTED_PREFIXES=('v5/','v5_trace/','outputs/runs/','outputs/history/','outputs/submissions/','outputs/reports/','outputs/evaluator_sidecars/','reference_scorer/')
_source_digest_cache={}


def dump(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')


def load(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()


def stamp(ns):return dt.datetime.fromtimestamp(ns/1e9,ZONE).isoformat()


def checked(root,path):
    root=Path(root).absolute();path=Path(path).absolute()
    if path==root or not path.is_relative_to(root):raise ValueError('Path outside intended root: '+str(path))
    for parent in [path,*path.parents]:
        if parent.is_symlink() or parent.is_junction():raise ValueError('Reparse/link path: '+str(parent))
        if parent==root:break
    if not path.resolve().is_relative_to(root.resolve()):raise ValueError('Resolved path escaped root')
    return path


def stat_key(p):
    s=Path(p).stat();return [s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_ino]


def fingerprint(p):
    p=Path(p);return [str(p.resolve()),*stat_key(p)]


def inventory(hash_files=True):
    rows=[];read_bytes=0;next_progress=2*1024**3
    checkpoint=REPORT/'inventory_checkpoint.jsonl';previous={}
    if checkpoint.is_file():
        with checkpoint.open(encoding='utf8') as f:
            for line in f:
                try:
                    saved=json.loads(line);previous[saved['path']]=saved
                except (ValueError,KeyError):pass
    checkpoint_stream=checkpoint.open('w',encoding='utf8',buffering=65536)
    for base,dirs,files in os.walk(PROJECT,followlinks=False):
        dirs.sort();files.sort()
        for d in list(dirs):
            p=Path(base)/d
            if p.is_symlink() or p.is_junction():
                rows.append({'path':p.relative_to(PROJECT).as_posix(),'kind':'link','bytes':0,'classification':'KEEP','reason':'Reparse point: never traverse'})
                dirs.remove(d)
        for name in files:
            p=Path(base)/name;rel=p.relative_to(PROJECT).as_posix()
            # Report grows during the audit. It is protected and counted only
            # in the final disk-size snapshot, not its own recursive inventory.
            if rel.startswith('outputs/reports/project_cleanup/'):continue
            s=p.lstat();birth=getattr(s,'st_birthtime_ns',s.st_ctime_ns)
            row={'path':rel,'kind':'link' if p.is_symlink() else 'file','bytes':s.st_size,'mtime_ns':s.st_mtime_ns,'creation_ns':birth,'ctime_ns':s.st_ctime_ns,'atime_ns':s.st_atime_ns,'inode':s.st_ino,'mode':s.st_mode,'links':s.st_nlink,'created':stamp(birth),'modified':stamp(s.st_mtime_ns)}
            try:
                # Hash every cleanup candidate, source, binary, receipt, Guide
                # and ordinary result. Very large protected evidence is never
                # selected for cleanup; record its complete metadata instead of
                # repeatedly streaming multi-GB diagnostic CSVs while UI runs.
                old=previous.get(rel)
                reusable=old and old.get('stable') and [old.get('bytes'),old.get('mtime_ns'),old.get('ctime_ns'),old.get('inode')]==[s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_ino]
                large_evidence=s.st_size>64*1024**2 and not rel.startswith('outputs/ui_cache/')
                if rel.startswith('outputs/ui_cache/') and s.st_size>64*1024**2:
                    # Current namespace caches remain protected while UI runs.
                    large_evidence=True
                    if p.suffix=='.sqlite':
                        try:
                            with closing(sqlite3.connect(p.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
                                sig=json.loads(db.execute('SELECT v FROM meta WHERE k=?',('signature',)).fetchone()[0])
                            large_evidence=sig[0]>=4
                        except Exception:pass
                if reusable:
                    row.update(sha256=old.get('sha256'),stable=True,hash_scope=old.get('hash_scope','COMPLETE_SHA256_REUSED_UNCHANGED_IDENTITY'))
                elif large_evidence:
                    row.update(sha256=None,stable=True,hash_scope='PROTECTED_LARGE_EVIDENCE_METADATA_ONLY_NOT_A_CANDIDATE')
                elif hash_files and row['kind']=='file':
                    row['sha256']=sha(p);row['stable']=stat_key(p)==[s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_ino]
                    read_bytes+=s.st_size
                    if read_bytes>=next_progress:
                        print(json.dumps({'phase':'hashing','files':len(rows)+1,'GiB':round(read_bytes/1024**3,2)}),flush=True)
                        next_progress=read_bytes+2*1024**3
            except OSError as exc:row.update(stable=False,error=str(exc))
            rows.append(row)
            checkpoint_stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    checkpoint_stream.close()
    return rows


def size_snapshot():
    total=count=0
    for base,dirs,files in os.walk(PROJECT,followlinks=False):
        dirs[:]=[d for d in dirs if not (Path(base)/d).is_symlink() and not (Path(base)/d).is_junction()]
        for name in files:
            p=Path(base)/name
            if p.is_symlink():continue
            try:total+=p.stat().st_size;count+=1
            except FileNotFoundError:pass
    return {'bytes':total,'files':count,'disk_free_bytes':shutil.disk_usage(PROJECT).free}


def git_state():
    files=subprocess.run(['git','-C',str(WORKSPACE),'ls-files','-z'],capture_output=True,check=True,timeout=45).stdout.decode('utf8').split('\0')
    prefix=PROJECT.relative_to(WORKSPACE).as_posix()+'/'
    tracked={name[len(prefix):] for name in files if name.startswith(prefix)}
    status=subprocess.run(['git','-C',str(WORKSPACE),'status','--porcelain=v1','-z','--untracked-files=all'],capture_output=True,check=True,timeout=45).stdout.decode('utf8')
    return {'tracked':sorted(tracked),'status_records':status.split('\0'),'commands':['git ls-files -z','git status --porcelain=v1 -z --untracked-files=all'],'write_operations':False}


def processes():
    sys.path.insert(0,str(PROJECT/'scripts'))
    from cleanup_outputs import processes as inspect_processes
    rows=inspect_processes()
    relevant=[]
    for row in rows:
        name=row['name'].lower();cmd=row['command'].lower()
        role=None
        if name.startswith(('router','score','reference_202')):role='ROUTER_OR_SCORER'
        elif ('python' in name or name in ('bash','sh')) and re.search(r'(final_router|portable|run_v5|run_case|score_case|score_v5|evaluate_reference|supervisor|resource_guard)',cmd):role='ROUTER_TRACE_OR_SCORER'
        elif 'ui/app.py' in cmd or re.search(r'ui[\\/]app.py',cmd):role='UI'
        elif name in ('cmake','cmake.exe','ninja','ninja.exe','g++','g++.exe','clang++','clang++.exe','cl.exe','msbuild.exe'):role='BUILD'
        if role:relevant.append(dict(row,role=role))
    # Inspection failure propagates and prohibits mutation.
    return {'all_process_count':len(rows),'relevant':relevant,'running_wsl_environments':sorted({r['environment'] for r in rows if r['environment'].startswith('WSL/')}),'observed_at':dt.datetime.now(ZONE).isoformat()}


def exclusive_probe(path):
    if os.name!='nt':return
    from cleanup_outputs import exclusive_probe as probe
    probe(path)


def ui_snapshot():
    def get(path):
        with urllib.request.urlopen('http://127.0.0.1:8501'+path,timeout=90) as f:return json.load(f)
    try:
        catalog=get('/api/catalog')
        key='toy/toy_v5_trace_release'
        if not any(r['id']==key for r in catalog['runs']):return {'available':True,'run_count':len(catalog['runs']),'selected_check':None}
        summary=get('/api/summary?id='+key)
        # Never request an indexing retry or wait for a missing index to rebuild.
        if summary.get('state')=='indexing':return {'available':True,'indexing':True,'run_count':len(catalog['runs'])}
        net=get('/api/net?id='+key+'&net=vlink0')
        return {'available':True,'run_count':len(catalog['runs']),'id':key,'summary_version':summary.get('version'),'metrics':summary.get('metrics'),'capabilities':summary.get('capabilities'),'net':net['net'],'net_provenance':net.get('path_provenance'),'net_edges':len(net['edges']),'net_guide_sha256':net.get('trace_guide_sha256')}
    except Exception as exc:return {'available':False,'error':str(exc)}


def sqlite_cache(path,current_version):
    result={'eligible':False}
    try:
        if not re.fullmatch('[0-9a-f]{24}\\.sqlite',path.name):return dict(result,reason='Not an established disposable index name')
        if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-shm','-journal')):return dict(result,reason='SQLite sidecar present: possible active work')
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as c:
            row=c.execute('SELECT v FROM meta WHERE k=?',('signature',)).fetchone()
            result['signature']=sig=json.loads(row[0])
        if not isinstance(sig,list) or len(sig)!=3 or not isinstance(sig[0],int):return dict(result,reason='Unknown cache schema')
        result['current_version']=current_version
        if sig[0]>=current_version:return dict(result,reason='Current/new cache namespace: preserve while UI is active')
        # Only retire an old namespace after identifying every original source.
        # Old migration aliases are metadata hints, never rewrite those sources.
        sources=[]
        current_fingerprints=[]
        sys.path.insert(0,str(PROJECT/'scripts'))
        from common import remap_record_paths
        for value in sig[1:]:
            if value is None:
                current_fingerprints.append(None);continue
            if not isinstance(value,list) or len(value)!=5:return dict(result,reason='Unknown source fingerprint')
            original=Path(value[0]);resolved=Path(remap_record_paths(value[0]))
            if not resolved.exists():return dict(result,reason='Original source missing; retain cache as possible unique surviving evidence')
            if not resolved.resolve().is_relative_to(PROJECT.parent.resolve()):return dict(result,reason='Source outside verified EDA data root')
            signature=stat_key(resolved)
            if signature!=value[1:]:return dict(result,reason='Original source fingerprint changed; preserve potentially unique cached evidence')
            key=(str(resolved.resolve()),*signature)
            current_fingerprints.append(fingerprint(resolved))
            if key not in _source_digest_cache:_source_digest_cache[key]=sha(resolved)
            sources.append({'recorded_path':str(original),'current_path':str(resolved),'bytes':resolved.stat().st_size,'source_sha256':_source_digest_cache[key]})
        current_token=hashlib.sha256(json.dumps([current_version,*current_fingerprints],ensure_ascii=False).encode()).hexdigest()[:24]
        result['current_token']=current_token
        if current_token==path.stem:return dict(result,reason='Filename belongs to the current cache namespace: retain')
        result.update(eligible=True,reason=f'Obsolete UI index namespace v{sig[0]} cannot be requested by current Store VERSION={current_version}; original sources verified present',sources=sources)
        exclusive_probe(path)
    except Exception as exc:result.update(eligible=False,reason='Retain uncertain/in-use cache: '+str(exc))
    return result


def references(rows):
    names={Path(r['path']).name for r in rows if r['kind']=='file'}
    scripts={p.name:p for p in (PROJECT/'scripts').glob('*.py')}
    # All Python sources, including history and parameter tuning, are indexed.
    script_paths={r['path'] for r in rows if r['path'].endswith('.py')}
    tokens={};edges=[];scanned=0;oversize=[]
    candidates=[PROJECT/r['path'] for r in rows if r['kind']=='file' and (Path(r['path']).suffix.lower() in TEXT or Path(r['path']).name=='CMakeLists.txt')]
    candidates += list(WORKSPACE.glob('*.md'))+list((WORKSPACE/'.routing_work').glob('*.py'))+list((WORKSPACE/'.routing_work').glob('*.ps1'))
    pattern=re.compile(r"(?<![A-Za-z0-9_@.+-])[A-Za-z0-9_@+-]+(?:\.[A-Za-z0-9_+-]+)+(?![A-Za-z0-9_@.+-])")
    for path in candidates:
        if path.is_relative_to(REPORT):continue
        if path.name=='result.json' or path.suffix=='.jsonl' or path.stat().st_size>16*1024*1024:
            oversize.append(str(path));continue
        scanned+=1
        try:text=path.read_text(encoding='utf8',errors='replace')
        except OSError:continue
        for lineno,line in enumerate(text.splitlines(),1):
            for token in set(pattern.findall(line)) & names:
                entry=tokens.setdefault(token,{'count':0,'examples':[]});entry['count']+=1
                if len(entry['examples'])<18:entry['examples'].append({'file':str(path.relative_to(WORKSPACE)),'line':lineno,'text':line[:350]})
        if path.suffix=='.py':
            try:tree=ast.parse(text)
            except SyntaxError:continue
            for node in ast.walk(tree):
                modules=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
                for module in modules:
                    name=module.split('.')[0]+'.py'
                    if name in scripts:edges.append({'from':str(path.relative_to(WORKSPACE)),'to':'scripts/'+name,'kind':'import','line':node.lineno})
            for name in scripts:
                if name in text and name!=path.name:edges.append({'from':str(path.relative_to(WORKSPACE)),'to':'scripts/'+name,'kind':'literal invocation/documented path'})
    return {'scanned_text_files':scanned,'oversized_or_geometry_sources_not_text_scanned':oversize,'by_basename':tokens,'script_edges':edges,'all_python_sources':sorted(script_paths)}


def registry_report(by_path):
    registry=load(PROJECT/'docs/router_versions.json');found=[]
    def visit(value,key='registry'):
        if isinstance(value,dict):
            if isinstance(value.get('path'),str) and isinstance(value.get('sha256'),str):
                p=Path(value['path']);p=p if p.is_absolute() else PROJECT/p
                exists=p.is_file();actual=sha(p) if exists else None
                found.append({'key':key,'path':value['path'],'exists':exists,'expected_sha256':value['sha256'],'actual_sha256':actual,'hash_matches':actual==value['sha256'],'verified_record':value.get('verified'),'status':value.get('status')})
            for k,v in value.items():visit(v,key+'.'+k)
        elif isinstance(value,list):
            for i,v in enumerate(value):visit(v,key+'.'+str(i))
    visit(registry)
    build=[]
    for rel,row in by_path.items():
        if rel.startswith('build/') and row['kind']=='file':
            p=PROJECT/rel
            with p.open('rb') as f:header=f.read(4)
            build.append({'path':rel,'sha256':row.get('sha256'),'bytes':row['bytes'],'format':'ELF' if header==b'\x7fELF' else 'PE/MZ' if header.startswith(b'MZ') else 'receipt/document','registry_references':[r['key'] for r in found if r['path']==rel],'action':'KEEP'})
    return {'registered_paths':found,'build_files':build,'registry_sha256':sha(PROJECT/'docs/router_versions.json')}


def audit():
    REPORT.mkdir(parents=True,exist_ok=True)
    before=size_snapshot();proc=processes();git=git_state()
    dump(REPORT/'processes_before.json',proc);dump(REPORT/'git_before.json',git)
    print(json.dumps({'phase':'inventory','before':before,'processes':proc['relevant']},ensure_ascii=False),flush=True)
    rows=inventory();by_path={r['path']:r for r in rows};tracked=set(git['tracked'])
    refs=references(rows);dump(REPORT/'dependency_analysis.json',refs)
    registry=registry_report(by_path);dump(REPORT/'version_audit.json',registry)
    tree=ast.parse((PROJECT/'ui/result_store.py').read_text(encoding='utf8'))
    version=next(n.value.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='VERSION' for t in n.targets))
    ui=ui_snapshot();dump(REPORT/'ui_before.json',ui)
    caches={};scripts=[];groups=collections.defaultdict(list)
    for r in rows:
        rel=r['path'];p=PROJECT/rel;name=p.name
        reason='Historical/source/configuration evidence; no proof of disposability'
        classification='KEEP'
        if r['kind']=='link':reason='Reparse point: retain'
        elif rel in tracked:reason='Git tracked: retain path and content'
        elif rel.startswith(PROTECTED_PREFIXES):reason='Protected source, Trace, formal run/history, submission, scorer or report'
        elif not r.get('stable',False):reason='File unstable/unreadable: retain'
        elif r.get('links',1)>1:reason='Multiple hardlinks: retain'
        elif rel.startswith('outputs/ui_cache/') and p.parent==PROJECT/'outputs/ui_cache' and p.suffix=='.sqlite':
            # Active algorithm/build tasks prohibit all cache retirement.
            if any(x['role']!='UI' for x in proc['relevant']):reason='Active Router/scorer/build: retain all caches'
            else:
                caches[rel]=decision=sqlite_cache(p,version)
                reason=decision['reason']
                if decision['eligible'] and ui.get('available') and not ui.get('indexing'):classification='DELETE_SAFE'
        elif '__pycache__' in p.parts and p.suffix=='.pyc':
            source=p.parent.parent/(name.split('.cpython-')[0]+'.py')
            if rel.startswith(('scripts/','ui/')) and any(x['role']=='UI' for x in proc['relevant']):reason='Active UI dependency bytecode: conservatively retain'
            elif source.is_file() and not refs['by_basename'].get(name):classification='DELETE_SAFE';reason='Reproducible Python bytecode; source exists, untracked and unreferenced'
        elif rel.startswith('build/'):reason='Historical representative binary/build receipt: retain even if duplicate'
        elif p.suffix in ('.o','.obj') or p.name=='CMakeCache.txt':reason='Build/reproduction paths or unique compile configuration may reference this intermediate; retain'
        elif p.suffix in ('.tmp','.partial'):reason='Temporary extension alone cannot exclude failure/interruption evidence; retain'
        if classification=='DELETE_SAFE' and not r.get('sha256'):
            classification='KEEP';reason='No complete candidate SHA256: retain'
        r.update(classification=classification,action='REMOVE_AFTER_VERIFIED_EXTERNAL_BACKUP' if classification=='DELETE_SAFE' else 'KEEP',reason=reason,git_tracked=rel in tracked,reference_count=refs['by_basename'].get(name,{}).get('count',0))
        if r.get('sha256') and r['bytes']>0:groups[r['sha256']].append(rel)
        if rel.startswith('scripts/') and p.suffix=='.py' and p.parent==PROJECT/'scripts':
            incoming=[e for e in refs['script_edges'] if e['to']==rel]
            role='CURRENT_OR_REPRODUCTION'
            if name.startswith(('analyze_','research_','report_','compare_','diagnose_')):role='HISTORICAL_RESEARCH_OR_REPORT'
            scripts.append({'path':rel,'classification':'KEEP','role':role,'sha256':r.get('sha256'),'git_tracked':rel in tracked,'protected':name in REQUIRED_SCRIPTS or 'v5_trace' in name or name.startswith(('validate_','manage_router')),'incoming_edges':incoming,'text_reference_count':r['reference_count'],'reason':'Preserve current entry, dependencies and historical reproducibility; lack of static references does not prove no dynamic invocation'})
    dump(REPORT/'cache_audit.json',caches);dump(REPORT/'script_classification.json',scripts)
    duplicates=[{'sha256':h,'paths':paths,'reason':'Equal bytes alone do not prove equal experiment purpose; preserve all except independently proven disposable caches/bytecode'} for h,paths in groups.items() if len(paths)>1]
    dump(REPORT/'duplicate_groups.json',duplicates)
    run_rows=[]
    for r in rows:
        if r['path'].endswith('/run.json') and r['path'].startswith(('outputs/runs/','outputs/history/')):
            try:
                record=load(PROJECT/r['path'])
                run_rows.append({'path':r['path'],'sha256':r['sha256'],'case':record.get('case'),'label':record.get('label'),'status':record.get('status'),'router_version':record.get('router_version'),'guide_sha256':record.get('guide_sha256'),'input_sha256':record.get('input_sha256'),'config_sha256':record.get('config_sha256'),'scorer_status':record.get('official_status'),'decision':'KEEP'})
            except Exception as exc:run_rows.append({'path':r['path'],'decision':'KEEP','error':str(exc)})
    dump(REPORT/'run_evidence_audit.json',run_rows)
    with (REPORT/'inventory_before.jsonl').open('w',encoding='utf8') as f:
        for r in rows:f.write(json.dumps(r,ensure_ascii=False)+'\n')
    selected=[r for r in rows if r['classification']=='DELETE_SAFE']
    key=dt.datetime.now(ZONE).strftime('%Y%m%d_%H%M%S')
    external=WORKSPACE.parent/'3DIC_EDA_cleanup_archives'/('project_cleanup_'+key)
    assert not external.resolve().is_relative_to(WORKSPACE.resolve())
    plan={'schema_version':1,'project':str(PROJECT),'workspace':str(WORKSPACE),'archive_directory':str(external),'before':before,'candidate_files':selected,'candidate_bytes':sum(r['bytes'] for r in selected),'tracked_files':len(tracked),'scripts':len(scripts),'registry_sha256':registry['registry_sha256'],'ui_store_version':version,'v5_original_files':{r['path']:r['sha256'] for r in rows if r['path'].startswith('v5/engine/')},'trace_files':{r['path']:r['sha256'] for r in rows if r['path'].startswith('v5_trace/')},'audit_only':True}
    dump(REPORT/'audit_plan.json',plan)
    cache_size=sum(r['bytes'] for r in selected if r['path'].startswith('outputs/ui_cache/'))
    text=f'''# 工程安全清理计划（删除前审计）

工程总逻辑大小：{before['bytes']:,} bytes，{before['files']} 个文件。全部候选、源码、二进制、回执和 Guide 的 SHA256，以及全量文件大小、创建/修改时间、Git 状态及逐文件分类见 inventory_before.jsonl。超过 64 MiB 且不属于 UI 缓存的受保护大证据仅记录元数据，不列入删除候选；不声称对这些多 GB CSV 做完整内容去重。
候选 {len(selected)} 文件，共 {plan['candidate_bytes']:,} bytes；其中旧版 UI 索引 {cache_size:,} bytes。

## DELETE_SAFE 的证明

只清理：① SQLite meta.signature 明确属于旧 Store namespace，当前代码 VERSION={version} 无法命中，且原始数据仍在、无 WAL/SHM、独占读取探测通过的旧索引；② 非活动任务依赖、源 .py 仍在且没有路径引用的 Python 字节码。
当前版本索引、全部原始 Guide/result/Trace、所有报告、失败诊断和活动 UI 依赖缓存均保留。不会停止任何进程，不启动 WSL 停止的发行版，不重跑 Router/评分器。WSL 活动环境：{proc['running_wsl_environments']}。

## KEEP / ARCHIVE

全部 build Router、.exe、.build.json 和现有 CMake 中间文件保留；现有路径可能被历史入口/构建回执引用。全部 {len(scripts)} 个 scripts/*.py 保留，包括旧研究脚本；静态无引用不构成可删除证明。ARCHIVE 移出脚本/实验数量预计 0。所有重复历史实验按 Guide/配置/输入/评分身份分别保留，不因分数或失败状态删除。大 TSV CSV 是唯一诊断证据，保留。

## 外部可恢复归档与风险

归档目标：`{external}`（工程及 Git 工作区之外）。先写完整恢复清单，逐文件压缩备份并重新读取校验 SHA256；全部成功后才按原路径、哈希、文件身份和最新进程状态逐文件删除，不递归删除目录。build/README.md 的旧版本也备份后才更新。
若归档校验失败、源文件变化、进程/句柄变忙、原始数据缺失或引用出现变化，则跳过清理。UI 当前可命中的缓存不删除；旧版索引已不可由新 UI 请求，原始结果始终保留。压缩归档位于同一 D 盘，实际磁盘净释放需扣除归档压缩占用，不能把工程体积减少当成整盘净释放。

## 逐文件删除候选

| 原路径 | bytes | 理由 |
|---|---:|---|
'''
    text+=''.join(f"| {r['path']} | {r['bytes']} | {r['reason']} |\n" for r in selected)
    (REPORT/'cleanup_plan.md').write_text(text,encoding='utf8')
    print(json.dumps({'phase':'AUDIT_COMPLETE','files':len(rows),'candidates':len(selected),'candidate_bytes':plan['candidate_bytes'],'report':str(REPORT),'archive':str(external)},ensure_ascii=False),flush=True)


RESTORE_SCRIPT=r'''"""Restore verified files without overwriting user changes. Default is preview."""
import argparse,hashlib,json,os
from pathlib import Path
import zipfile

def digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()

def main():
 p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,default=Path(__file__).with_name('restore_manifest.json'));p.add_argument('--target',type=Path);p.add_argument('--only');p.add_argument('--apply',action='store_true');p.add_argument('--include-modified-documents',action='store_true');a=p.parse_args()
 m=json.loads(a.manifest.read_text(encoding='utf8'));root=(a.target or Path(m['project'])).absolute();archive=Path(m['archive_file'])
 if digest(archive)!=m['archive_sha256']:raise ValueError('Archive SHA256 mismatch')
 restored=[];skipped=[]
 root.mkdir(parents=True,exist_ok=True)
 if root.is_symlink() or root.is_junction():raise ValueError('Target root redirects')
 with zipfile.ZipFile(archive) as z:
  for e in m['entries']:
   if a.only and e['path']!=a.only:continue
   if e['action']=='MODIFY_DOCUMENT' and not a.include_modified_documents:continue
   relative=Path(e['path']);target=root/relative
   if relative.is_absolute() or '..' in relative.parts or target==root or not target.resolve().is_relative_to(root.resolve()):raise ValueError('Unsafe restore path')
   for ancestor in [target,*target.parents]:
    if ancestor.is_symlink() or ancestor.is_junction():raise ValueError('Restore reparse path')
    if ancestor==root:break
   if target.exists():
    current=digest(target)
    if current==e['sha256']:skipped.append([e['path'],'already original']);continue
    if not(e['action']=='MODIFY_DOCUMENT' and current==e.get('replacement_sha256')):
     skipped.append([e['path'],'existing different file: retained']);continue
   if not a.apply:restored.append([e['path'],'PREVIEW']);continue
   target.parent.mkdir(parents=True,exist_ok=True);temporary=target.with_name(target.name+'.restore-'+m['id']+'.tmp')
   if temporary.exists():raise ValueError('Restore temporary already exists')
   h=hashlib.sha256()
   with z.open(e['archive_entry']) as src,temporary.open('xb') as dst:
    for chunk in iter(lambda:src.read(8*1024*1024),b''):h.update(chunk);dst.write(chunk)
   if h.hexdigest()!=e['sha256']:temporary.unlink();raise ValueError('Restored content mismatch')
   temporary.replace(target);os.chmod(target,e['mode']);os.utime(target,ns=(e['atime_ns'],e['mtime_ns']))
   restored.append([e['path'],'RESTORED'])
 print(json.dumps({'restored':restored,'skipped':skipped},ensure_ascii=False))

if __name__=='__main__':main()
'''


def verify_sources(plan):
    for key,prefix in [('v5_original_files','v5/engine/'),('trace_files','v5_trace/')]:
        current={p.relative_to(PROJECT).as_posix():sha(p) for p in (PROJECT/prefix).rglob('*') if p.is_file()}
        if current!=plan[key]:raise ValueError('Protected source identity changed: '+prefix)
    if sha(PROJECT/'docs/router_versions.json')!=plan['registry_sha256']:raise ValueError('Version registry changed')
    # Dependencies must not change between audit, backup and removal. The new
    # maintenance driver itself is excluded because it is developed during this
    # task; all pre-existing project source/configuration remains pinned.
    if (REPORT/'inventory_before.jsonl').is_file():
        for row in read_inventory():
            rel=row['path']
            if row['kind']!='file' or rel.startswith('outputs/') or rel in ('build/README.md',Path(__file__).relative_to(PROJECT).as_posix()):continue
            # Disposable bytecode is checked by candidate_guard before removal;
            # it is not source that must still exist after successful cleanup.
            if row.get('classification')=='DELETE_SAFE' and '__pycache__' in Path(rel).parts and rel.endswith('.pyc'):continue
            if row.get('sha256') and sha(PROJECT/rel)!=row['sha256']:raise ValueError('Dependency/source changed since audit: '+rel)


def candidate_guard(row,tracked,plan):
    rel=row['path'];path=checked(PROJECT,PROJECT/rel)
    if rel in tracked or rel.startswith(PROTECTED_PREFIXES):raise ValueError('Protected/tracked path')
    if stat_key(path)!=[row['bytes'],row['mtime_ns'],row['ctime_ns'],row['inode']]:raise ValueError('File identity changed since audit')
    if sha(path)!=row['sha256']:raise ValueError('File SHA256 changed since audit')
    if time.time_ns()-path.stat().st_mtime_ns<300*10**9:raise ValueError('Recent modification: possible task activity')
    exclusive_probe(path)
    if rel.startswith('outputs/ui_cache/'):
        decision=sqlite_cache(path,plan['ui_store_version'])
        if not decision['eligible']:raise ValueError('Cache no longer proven disposable: '+decision['reason'])
    elif '__pycache__' in path.parts and path.suffix=='.pyc':
        source=path.parent.parent/(path.name.split('.cpython-')[0]+'.py')
        if not source.is_file():raise ValueError('Bytecode source disappeared: retain possible surviving evidence')
    else:raise ValueError('Unknown deletion category')
    return path


def read_inventory():
    with (REPORT/'inventory_before.jsonl').open(encoding='utf8') as f:return [json.loads(line) for line in f]


def functional_reference_guard(names):
    """Fresh, BOM-aware scan including Windows PowerShell helper sources."""
    candidates=[]
    for base,dirs,files in os.walk(PROJECT,followlinks=False):
        dirs[:]=[d for d in dirs if d!='outputs' and not (Path(base)/d).is_symlink() and not (Path(base)/d).is_junction()]
        candidates.extend(Path(base)/n for n in files if Path(n).suffix.lower() in TEXT or n=='CMakeLists.txt')
    candidates+=list(WORKSPACE.glob('*.md'))
    helpers=WORKSPACE/'.routing_work'
    if helpers.exists():
        candidates.extend(p for p in helpers.rglob('*') if p.is_file() and p.suffix.lower() in ('.py','.ps1','.cmd','.bat','.sh','.js','.md','.json'))
    matches=collections.defaultdict(list);identity={}
    for path in candidates:
        if path.stat().st_size>16*1024**2:continue
        raw=path.read_bytes();identity[str(path)]=hashlib.sha256(raw).hexdigest()
        encoding='utf-16' if raw[:2] in (b'\xff\xfe',b'\xfe\xff') else 'utf-8-sig'
        text=raw.decode(encoding,errors='replace').casefold()
        for name in names:
            if name.casefold() in text:matches[name].append(str(path))
    return {'matches':dict(matches),'source_sha256':identity,'decoded_utf16_bom':True}


def build_readme(rows):
    registry=load(REPORT/'version_audit.json');by_path={r['path']:r for r in rows}
    matches=collections.defaultdict(list)
    for path in (PROJECT/'outputs/runs').glob('*/*/run.json'):
        try:
            value=load(path)
            if value.get('router_sha256'):matches[value['router_sha256']].append(path.relative_to(PROJECT).as_posix())
        except (ValueError,OSError):pass
    content='''# Router 构建与历史身份

本目录保留全部现存历史 Router 和构建回执；不按旧版本名称或文件大小删除。
V1、V2、V3.1 的权威登记见 [router_versions.json](../docs/router_versions.json)。后续数字版本不都在该文件的 versions 项中；必须结合具体 .build.json、源码哈希、run.json 和验证报告判断身份，不能仅靠文件名认定已正式发布。

| 文件 | 格式 / 用途 | 实际 SHA256 | 运行回执匹配数 |
|---|---|---|---:|
'''
    for r in registry['build_files']:
        p=Path(r['path'])
        if r['format']=='receipt/document':continue
        if p.name=='router_v1':role='ELF；已登记 V1 独立基准，历史对照'
        elif p.name=='router_v2':role='ELF；已登记 V2 性能优化版本，历史对照'
        elif p.name=='router_v3_1':role='ELF；已登记 V3.1 研究基准'
        elif p.name=='router_v4_5.exe':role='Windows PE；native V4.5，原回执标为 EXPERIMENTAL / NATIVE_COMPILED_TESTED；Linux验证未运行'
        else:role=r['format']+'；保留的数字版本历史构建，以哈希和具体实验回执为准'
        content+=f"| `{p.name}` | {role} | `{r['sha256']}` | {len(matches[r['sha256']])} |\n"
    content+='''
## 构建回执

`router_v4.build.json`、`router_v4_2.build.json`、`router_v4_3.build.json`、`router_v4_5.native.build.json` 全部保留，包含编译配置、源码和二进制身份。native V4.5 还引用 outputs/development/v4_5/native_05、artifact_history 的不同二进制，不能将其视为重复垃圾。

## 其他版本与入口

V4.4 / 拥塞研究变体以及性能测试二进制还保存在 outputs/development/ 等路径，由 CMake、历史脚本或回执引用，未移动。旧别名按 router_versions.json 的 router_aliases 解析；不在这里建立新副本或重写登记。
V5 正式入口为 scripts/run_v5_case.py，其 79 文件 Python 引擎在 v5/engine/。V5 Trace 独立位于 v5_trace/engine/，入口 scripts/run_v5_trace_case.py；两者不与本目录 C++ 构建混用。

本次清理仅验证现存二进制 SHA256、ELF/PE 头及历史 CLI/运行记录，没有重新编译或运行 Router。ELF 需要已有 Linux/WSL 环境；格式和历史回执验证不等于在当前 Windows 上重新执行验证。
详细审计、依赖及恢复清单见 outputs/reports/project_cleanup/。
'''
    return content


def verify_archive(archive,entries):
    with zipfile.ZipFile(archive) as z:
        for i,e in enumerate(entries,1):
            h=hashlib.sha256();size=0
            with z.open(e['archive_entry']) as f:
                for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk);size+=len(chunk)
            if h.hexdigest()!=e['sha256'] or size!=e['bytes']:raise ValueError('Archive entry failed verification: '+e['path'])
            print(json.dumps({'phase':'archive_verified','file':i,'total':len(entries),'path':e['path']}),flush=True)


def apply():
    plan=load(REPORT/'audit_plan.json');rows=read_inventory();by_path={r['path']:r for r in rows}
    if plan['project']!=str(PROJECT):raise ValueError('Plan belongs to another project')
    if (REPORT/'restore_manifest.json').exists():raise ValueError('A cleanup has already started; retain manifest, do not overwrite')
    verify_sources(plan);proc=processes();dump(REPORT/'processes_pre_archive.json',proc)
    if any(p['role']!='UI' for p in proc['relevant']):raise ValueError('Active Router/scorer/build prevents cleanup')
    tracked=set(git_state()['tracked']);selected=[];skipped=[]
    fresh_refs=functional_reference_guard({Path(r['path']).name for r in plan['candidate_files']})
    dump(REPORT/'functional_reference_guard.json',fresh_refs)
    refs=load(REPORT/'dependency_analysis.json')['by_basename']
    for row in plan['candidate_files']:
        try:
            candidate_guard(row,tracked,plan)
            # History inventories may name old caches, but a functional source
            # or document hardcoding one of these paths must retain it.
            functional=[x for x in refs.get(Path(row['path']).name,{}).get('examples',[]) if not x['file'].replace('\\','/').startswith(PROJECT.relative_to(WORKSPACE).as_posix()+'/outputs/')]
            if functional:raise ValueError('A source/document explicitly references this filename')
            if fresh_refs['matches'].get(Path(row['path']).name):raise ValueError('Fresh functional source/PowerShell reference found')
            selected.append(row)
        except Exception as exc:skipped.append({'path':row['path'],'reason':str(exc)})
    external=Path(plan['archive_directory']).absolute()
    if external.resolve().is_relative_to(WORKSPACE.resolve()):raise ValueError('Archive must be outside entire workspace')
    checked(external.parent,external);external.mkdir(parents=True,exist_ok=False)
    archive=external/'recovery.zip';entries=[]
    # Back up README before the only intentional edit to an existing document.
    doc=by_path['build/README.md'];expected=checked(PROJECT,PROJECT/doc['path'])
    if sha(expected)!=doc['sha256']:raise ValueError('README changed since audit')
    all_rows=[*selected,dict(doc,action='MODIFY_DOCUMENT')]
    manifest={'schema_version':1,'id':external.name,'project':str(PROJECT),'archive_file':str(archive),'archive_sha256':None,'entries':[],'status':'PLANNED','created_at':dt.datetime.now(ZONE).isoformat(),'skipped':skipped}
    for r in all_rows:
        e={k:r[k] for k in ('path','bytes','sha256','mtime_ns','creation_ns','atime_ns','mode')}
        e.update(original_absolute_path=str(PROJECT/r['path']),archive_entry='files/'+r['path'],action='MODIFY_DOCUMENT' if r['path']=='build/README.md' else 'DELETE_SAFE',status='PLANNED')
        entries.append(e)
    manifest['entries']=entries
    dump(external/'restore_manifest.json',manifest);dump(REPORT/'restore_manifest.json',manifest)
    (external/'restore_cleanup.py').write_text(RESTORE_SCRIPT,encoding='utf8',newline='\n')
    shutil.copyfile(REPORT/'cleanup_plan.md',external/'cleanup_plan.md')
    print(json.dumps({'phase':'ARCHIVE_BEGIN','directory':str(external),'files':len(entries),'bytes':sum(e['bytes'] for e in entries)},ensure_ascii=False),flush=True)
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
        for i,e in enumerate(entries,1):
            source=checked(PROJECT,PROJECT/e['path'])
            if source.stat().st_size!=e['bytes']:raise ValueError('Source size changed before backup: '+e['path'])
            h=hashlib.sha256()
            info=zipfile.ZipInfo.from_file(source,e['archive_entry']);info.compress_type=zipfile.ZIP_DEFLATED
            with source.open('rb') as src,z.open(info,'w',force_zip64=True) as dst:
                for chunk in iter(lambda:src.read(8*1024*1024),b''):h.update(chunk);dst.write(chunk)
            if h.hexdigest()!=e['sha256']:raise ValueError('Source changed during backup')
            e['status']='BACKED_UP'
            print(json.dumps({'phase':'archived','file':i,'total':len(entries),'path':e['path']}),flush=True)
    verify_archive(archive,entries)
    manifest.update(archive_sha256=sha(archive),status='BACKUP_VERIFIED',archive_bytes=archive.stat().st_size)
    dump(external/'restore_manifest.json',manifest);dump(REPORT/'restore_manifest.json',manifest)
    # A fresh inventory and integrity gate after the potentially lengthy backup.
    verify_sources(plan);proc=processes();dump(REPORT/'processes_pre_delete.json',proc)
    for path,expected_hash in fresh_refs['source_sha256'].items():
        if sha(path)!=expected_hash:raise ValueError('Functional reference source changed during backup: '+path)
    if any(p['role']!='UI' for p in proc['relevant']):raise ValueError('New active Router/scorer/build; backup retained, no deletion')
    tracked=set(git_state()['tracked'])
    for i,e in enumerate(entries,1):
        if e['action']!='DELETE_SAFE':continue
        if any(p['role']!='UI' for p in processes()['relevant']):
            for remaining in entries[i-1:]:
                if remaining['action']=='DELETE_SAFE':remaining.update(status='RETAINED_AFTER_BACKUP',skip_reason='New active task: stopped all further cleanup')
            break
        try:
            target=candidate_guard(by_path[e['path']],tracked,plan)
            # Exactly one checked file. No recursive deletion, moving, chmod or
            # shell expansion is used, and every original byte is archived.
            target.unlink();e['status']='REMOVED'
        except Exception as exc:e.update(status='RETAINED_AFTER_BACKUP',skip_reason=str(exc))
        dump(REPORT/'restore_manifest.json',manifest);dump(external/'restore_manifest.json',manifest)
    readme=PROJECT/'build/README.md'
    doc_entry=next(e for e in entries if e['action']=='MODIFY_DOCUMENT')
    if sha(readme)==doc_entry['sha256']:
        readme.write_text(build_readme(rows),encoding='utf8',newline='\n')
        doc_entry.update(status='DOCUMENT_UPDATED',replacement_sha256=sha(readme))
    else:doc_entry['status']='RETAINED_USER_CHANGED_DOCUMENT'
    manifest['status']='CLEANUP_APPLIED'
    dump(REPORT/'restore_manifest.json',manifest);dump(external/'restore_manifest.json',manifest)
    finalize(plan,rows,manifest)


def verify_existing():
    """Finish read-only integrity checks/reports; never repeat a removal."""
    plan=load(REPORT/'audit_plan.json');manifest=load(REPORT/'restore_manifest.json')
    if manifest['status']!='CLEANUP_APPLIED' or manifest['project']!=str(PROJECT):raise ValueError('No completed cleanup to verify')
    archive=Path(manifest['archive_file'])
    if load(archive.parent/'restore_manifest.json')!=manifest:raise ValueError('External/project recovery manifests differ')
    if sha(archive)!=manifest['archive_sha256']:raise ValueError('Recovery archive identity changed')
    for e in manifest['entries']:
        path=checked(PROJECT,PROJECT/e['path'])
        if e['status']=='REMOVED' and path.exists():raise ValueError('Removed path was recreated: preserve it and review '+e['path'])
        if e['status']=='DOCUMENT_UPDATED' and sha(path)!=e['replacement_sha256']:raise ValueError('Updated README changed after cleanup')
    finalize(plan,read_inventory(),manifest)


def finalize(plan,rows,manifest):
    archive=Path(manifest['archive_file']);entries=manifest['entries'];proc=processes()
    verify_sources(plan)
    # Full retained-file hash comparison catches accidental history or binary
    # changes; active server logs and the explicit README edit are separated.
    removed={e['path'] for e in entries if e['status']=='REMOVED'};changes=[];verified=0;metadata_verified=0
    for row in rows:
        rel=row['path']
        if row['kind']!='file' or rel in removed or rel=='build/README.md' or rel==Path(__file__).relative_to(PROJECT).as_posix():continue
        path=PROJECT/rel
        if not path.is_file():changes.append({'path':rel,'reason':'Unexpected missing file'});continue
        if not row.get('sha256'):
            metadata_verified+=1
            if stat_key(path)!=[row['bytes'],row['mtime_ns'],row['ctime_ns'],row['inode']]:changes.append({'path':rel,'reason':'Protected large evidence metadata changed'})
            continue
        actual=sha(path);verified+=1
        if actual!=row.get('sha256'):
            active_log=('ui_server' in path.name or path.name=='browser.log') and any(p['role']=='UI' for p in proc['relevant'])
            changes.append({'path':rel,'reason':'Active UI log append' if active_log else 'Unexpected content change','expected':row.get('sha256'),'actual':actual,'active_log':active_log})
    after_ui=ui_snapshot();dump(REPORT/'ui_after.json',after_ui)
    before_ui=load(REPORT/'ui_before.json')
    ui_equal=all(after_ui.get(k)==before_ui.get(k) for k in ('available','run_count','summary_version','net_provenance','net_edges','net_guide_sha256'))
    verification={'retained_files_hashed':verified,'protected_large_files_metadata_verified':metadata_verified,'retained_changes':changes,'protected_sources':'PASS','registry':'PASS','ui_identity_unchanged':ui_equal,'ui_after':after_ui,'unexpected_changes':[c for c in changes if not c.get('active_log')]}
    dump(REPORT/'verification.json',verification)
    dump(REPORT/'processes_after.json',processes());dump(REPORT/'git_after.json',git_state())
    report_results(plan,rows,manifest,verification)
    if verification['unexpected_changes'] or not ui_equal:raise ValueError('Post-cleanup verification needs review; all removed bytes remain recoverable')
    print(json.dumps({'phase':'CLEANUP_COMPLETE','removed':len(removed),'removed_bytes':sum(e['bytes'] for e in entries if e['status']=='REMOVED'),'archive':str(archive),'verification':'PASS'},ensure_ascii=False),flush=True)


def report_results(plan,rows,manifest,verification):
    removed=[e for e in manifest['entries'] if e['status']=='REMOVED'];removed_paths={e['path'] for e in removed}
    kept=[r for r in rows if r['path'] not in removed_paths]
    scripts=load(REPORT/'script_classification.json')
    text='# 保留文件与理由\n\n全部逐文件分类见 inventory_before.jsonl；完整哈希身份和依赖图另见各 JSON 报告。\n\n'
    text+='所有正式/历史运行、Guide、result、真实 Trace、评分与失败证据、提交包、源身份、版本登记、V5/Trace 全部保留。全部 Router/评分器/测试可执行文件及构建回执保留；没有重跑任何 Case。\n\n## 脚本审计\n\n| 脚本 | 分类/用途 | 依赖边 | Git 跟踪 |\n|---|---|---:|---|\n'
    text+=''.join(f"| {s['path']} | KEEP / {s['role']} | {len(s['incoming_edges'])} | {s['git_tracked']} |\n" for s in scripts)
    text+='\n没有把旧 report/validate/research 脚本批量删除或移动。静态引用缺失也不能排除手工命令、动态调用或历史复现用途。\n\n## 其余保留文件\n\n| 路径 | bytes | 理由 |\n|---|---:|---|\n'
    text+=''.join(f"| {r['path']} | {r['bytes']} | {r.get('reason','KEEP')} |\n" for r in kept)
    (REPORT/'files_kept.md').write_text(text,encoding='utf8')
    text='# 外部恢复归档\n\n归档位于工程和 Git 工作区之外：`'+str(Path(manifest['archive_file']).parent)+'`。\n\n所有备份逐文件重新读取并校验 SHA256；整个 ZIP SHA256 为 `'+manifest['archive_sha256']+'`。\n\n这里的“归档”是删除前可恢复备份，没有移出任何脚本或独立实验目录。\n\n| 原路径 | action | bytes | SHA256 | 最终状态 |\n|---|---|---:|---|---|\n'
    text+=''.join(f"| {e['path']} | {e['action']} | {e['bytes']} | {e['sha256']} | {e['status']} |\n" for e in manifest['entries'])
    text+='\n恢复预览：\n\n```powershell\npython -B "'+str(Path(manifest['archive_file']).parent/'restore_cleanup.py')+'"\n```\n\n添加 `--apply` 才写回原路径；现存不同内容的文件会跳过。恢复旧 build/README.md 需再加 `--include-modified-documents`，且当前文件必须仍等于本次更新的哈希。`--target <目录>` 支持隔离恢复核验；`--only <相对路径>` 支持单文件恢复。\n'
    (REPORT/'files_archived.md').write_text(text,encoding='utf8')
    text='# 实际删除文件\n\n只删除下列已完成外部备份及校验的明确可再生文件，未删除目录。原始完整路径、创建/修改时间和 SHA256 见 restore_manifest.json。\n\n| 原路径 | bytes | SHA256 |\n|---|---:|---|\n'
    text+=''.join(f"| {e['path']} | {e['bytes']} | {e['sha256']} |\n" for e in removed)
    text+='\n跳过/保留：\n\n'+json.dumps(manifest.get('skipped',[]),ensure_ascii=False,indent=2)+'\n'
    (REPORT/'files_deleted.md').write_text(text,encoding='utf8')
    result={'status':'PASS' if not verification['unexpected_changes'] and verification['ui_identity_unchanged'] else 'REVIEW','before':plan['before'],'removed_files':len(removed),'removed_bytes':sum(e['bytes'] for e in removed),'archive_files':len(manifest['entries']),'archive_bytes':manifest['archive_bytes'],'moved_scripts_or_experiments':0,'archive_file':manifest['archive_file'],'v5_original_file_count':len(plan['v5_original_files']),'v5_trace_files':len(plan['trace_files']),'retained_files_hashed':verification['retained_files_hashed'],'protected_large_files_metadata_verified':verification['protected_large_files_metadata_verified'],'ui_identity_unchanged':verification['ui_identity_unchanged'],'archive_sha256':manifest['archive_sha256']}
    for _ in range(3):
        result['after']=size_snapshot();result['project_size_reduction']=result['before']['bytes']-result['after']['bytes'];result['observed_disk_free_increase']=result['after']['disk_free_bytes']-result['before']['disk_free_bytes']
        dump(REPORT/'cleanup_result.json',result)
        summary=f'''# 安全清理结果

状态：{result['status']}。未停止进程，未重新运行 Router/评分器，未修改算法/参数/Guide/DEF/LEF、V5 或 V5 Trace，未进行 Git 写操作或网络提交。

| 项目 | 数值 |
|---|---:|
| 清理前工程总逻辑大小 | {result['before']['bytes']:,} bytes ({result['before']['bytes']/1024**3:.3f} GiB) |
| 清理后工程总逻辑大小（含本次报告） | {result['after']['bytes']:,} bytes ({result['after']['bytes']/1024**3:.3f} GiB) |
| 工程净减少 | {result['project_size_reduction']:,} bytes |
| 实际删除文件内容大小 | {result['removed_bytes']:,} bytes |
| 删除文件数量 | {result['removed_files']} |
| 外部归档备份文件数（含 README 旧版） | {result['archive_files']} |
| 脚本/实验目录移出数量 | 0 |
| 压缩恢复 ZIP 大小 | {result['archive_bytes']:,} bytes |
| 观察到的 D 盘空闲空间增加 | {result['observed_disk_free_increase']:,} bytes |

归档在同一 D 盘、工程/Git 工作区之外，所以整盘净回收小于工程体积减少；磁盘空闲还受其他应用活动影响，不是严格独占基准。报告自身计入清理后体积。

## 保留的重要内容

build 下全部现存 V1～V4.5 Router、.build.json，outputs/development 的旧可执行和对象文件、CMake 构建路径、全部脚本、正式 case001/003/005 运行、失败/OOM/INCOMPLETE 证据、真实 Trace、评分报告、版本/调参/提交历史均保留。只有旧 namespace 且原始数据仍在的派生 UI SQLite 索引，以及非活动任务的字节码被删除。当前 Store 版本缓存和所有 Guide 快照保留，活动 UI 不会用到被移除的旧 namespace。

## 高风险未清理

大型 tsv_conflicts.csv（含约 4.67 GB 的 case005 诊断）可能是唯一失败证据，保留。重复 Guide/评分数据可能来自不同输入、配置或评分器，保留。中断 result.json.tmp 可能是失败测试的核心证据，保留。旧二进制或 .o 可能包含唯一源码配置、被历史链接/测试命令引用，保留。没有证据证明任何源脚本已失去复现价值，全部保留。

## 功能与身份验证

原始 v5/engine 的 {result['v5_original_file_count']} 文件 SHA256 一致；v5_trace 全部 {result['v5_trace_files']} 文件 SHA256 一致。版本登记内容/目标哈希不变，保留文件重新 SHA256 核验 {result['retained_files_hashed']} 个。活动 UI 的 run 数、概要版本、vlink0 最终来源、63 条真实边和 Guide 身份保持一致：{verification['ui_identity_unchanged']}。活动 UI 日志正常追加单独记录，不当作清理改写。build/README.md 是唯一有意更新的已有文档，旧版已经归档。

外部归档：`{manifest['archive_file']}`。
详见 cleanup_plan.md、files_kept.md、files_archived.md、files_deleted.md、restore_manifest.json、verification.json。
'''
        summary+=f'\n核验范围：另有 {verification["protected_large_files_metadata_verified"]} 个大型保留文件仅比较大小、修改/创建时间及文件身份，没有宣称这些文件全部重算 SHA256。候选文件、V5/Trace 源码、登记身份均完整校验。\n'
        summary+='\n安全测试、收尾修复记录及真实归档的隔离恢复抽查结果见 safety_tests.md、restore_smoke.json；恢复不覆盖现有不同内容文件。\n'
        (REPORT/'cleanup_summary.md').write_text(summary,encoding='utf8')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['audit','apply','verify']);a=p.parse_args()
    if a.action=='audit':audit()
    elif a.action=='apply':apply()
    else:verify_existing()
