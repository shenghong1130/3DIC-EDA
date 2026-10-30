#!/usr/bin/env python3
"""Conservative, offline outputs maintenance (standard library only).

Default/--dry-run only previews. --apply records a fresh plan before deletion.
Only the reviewed browser profiles and three checksum-pinned synthetic JSONs
below may be deleted. New/unknown paths, runs, scores, ZIPs, caches and binaries
are retained. No Router, evaluator, build or browser process is launched/killed.
Use --report-dir outputs/reports/<new-name> to save a preview audit as well.
The separate --case001-prune opt-in uses a checksum-pinned historical summary
and exact reviewed retention policy; ordinary --apply still protects all runs.
"""
from pathlib import Path
import argparse
import collections
import ctypes
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parents[1]
OUTPUTS = PROJECT / 'outputs'
STREAM = 'stream_result_validation_0becc040'
LARGE_UI = 'ui_large_validation_69d060d4'
# Exact reviewed locations, not a broad glob over future validation directories.
PROFILES = {
    'browser_profile': 'reports/ui_browser_test.json',
    'doc_reorganization/browser_profile': 'doc_reorganization/mermaid.json',
    STREAM + '/ui_profile_1793179677598176500': STREAM + '/ui_check.json',
    **{LARGE_UI + '/profile_' + n: LARGE_UI + '/browser_check.json' for n in (
        '1793192266893434300', '1793193331216264400',
        '1793193854809376800', '1793194219839532600')},
}
SYNTHETIC = {
    STREAM + '/synthetic_old/result.json': '033cddfe355e6bff3a453ecccfc2183710a3f8126252d2ec8eddb34e69125c20',
    STREAM + '/synthetic_new/result.json': '86ad084a579e8a5629b734ed67d3c633ba397f8957163424d06e482480c7ba06',
    STREAM + '/synthetic_supervised/result.json': '9bda338175c2fcb45a56ce565f63a0b0f8e2874fd148cefa94c818b497631058',
}
PROTECTED_ROOTS = {'runs', 'submissions', 'reports', 'ui_cache', 'development',
                   'evaluator_sidecars', 'unpacked_submission'}
PROTECTED_NAMES = {'.gitkeep', 'run.json', 'route.guide', 'invocation.json',
    'evaluation_report.json', 'progress.log', 'progress.jsonl', 'router_exit.json',
    'host_monitor.jsonl', 'resource_monitor.csv', 'resource_usage.txt'}
KEEP_ROOT = {
    'cmake_build': 'Existing .routing_work/drive.py cmaketest build path; CMake caches contain absolute paths.',
    'core_tests': 'Existing .routing_work/drive.py build/test executable path.',
    'observability_build': 'Historical reproducible CMake build, absolute cache paths and test evidence retained.',
    'router.exe': 'Windows native Router default in run_case.py and workflow/diagnostics tests.',
    'router_observability.exe': 'Retain explicitly protected native diagnostic Router.',
    'submission_v1.zip': 'Default package_submission.py target; historical archive, never overwrite.',
    'submission_v1.manifest.json': 'Archive absolute path and historical validation metadata.',
    'original_input_sha256.json': 'Original input integrity baseline referenced by documentation.',
    'package_check.guide': 'Package validation Guide read by .routing_work/drive.py finalaudit.',
    'ui_server_pid.txt': 'Read by existing UI management helper; may be stale, do not trust as current PID.',
    'ui_server.log': 'UI management/uistatus helper path; existing UI server is left untouched.',
    'browser.log': 'Read by existing uistatus helper.',
    'ui_routing.png': 'Historical screenshot named in reports/ui_browser_test.json.',
    'ui_trace.png': 'Historical screenshot named in reports/ui_browser_test.json.',
    'ui_thermal.png': 'Historical screenshot named in reports/ui_browser_test.json.',
    'ui_case001.png': 'Historical browser verification screenshot.',
    'tests': 'test_workflow.py/test_diagnostics.py create TemporaryDirectory children here; preserve base and unknown children.',
}

def checked(root, path):
    """Reject escapes and every reparse/symlink ancestor before any mutation."""
    root = Path(root).absolute()
    path = Path(path).absolute()
    if path == root or not path.is_relative_to(root):
        raise ValueError('Unsafe target: ' + str(path))
    if root.resolve() != root or root.is_symlink() or root.is_junction():
        raise ValueError('Root must not redirect through a link')
    for p in [path, *path.parents]:
        if p.is_symlink() or p.is_junction():
            raise ValueError('Link/reparse point: ' + str(p))
        if p == root:
            break
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Resolved target escaped outputs')
    return path

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def load(path):
    return json.loads(path.read_text(encoding='utf8'))

def scan(root):
    rows = []
    def visit(base):
        for p in sorted(base.iterdir()):
            s = p.lstat()
            link = p.is_symlink() or p.is_junction()
            kind = 'link' if link else 'directory' if p.is_dir() else 'file'
            rows.append({'path': p.relative_to(root).as_posix(), 'kind': kind,
                'bytes': s.st_size if kind == 'file' else 0,
                'mtime_ns': s.st_mtime_ns, 'type': p.suffix or '[none]'})
            if kind == 'directory':
                visit(p)
    visit(root)
    return rows

def tracked_files():
    r = subprocess.run(['git', '-C', str(WORKSPACE), 'ls-files', '-z'],
        capture_output=True, check=True, timeout=30)
    return {str((WORKSPACE / p).absolute()).casefold()
            for p in r.stdout.decode('utf8').split('\0') if p}

def source_references():
    """Actual source/docs only; never scan huge results or browser caches."""
    sources = list(WORKSPACE.glob('*.md')) + list(PROJECT.glob('*.sh')) + list(PROJECT.glob('*.txt'))
    for name in ['scripts', 'tests', 'src', 'include', 'ui', 'configs', 'docs']:
        sources.extend(p for p in (PROJECT / name).rglob('*') if p.is_file())
    sources.extend((WORKSPACE / '.routing_work').glob('*.py'))
    out = []
    for p in sources:
        if p.name in ['cleanup_outputs.py', 'test_cleanup_outputs.py'] or p.suffix not in {
                '.md', '.py', '.cpp', '.hpp', '.sh', '.ps1', '.json', '.txt', '.html', '.js', '.css'}:
            continue
        if p.stat().st_size > 2 * 1024 * 1024:
            continue
        for i, line in enumerate(p.read_text(encoding='utf8', errors='replace').splitlines(), 1):
            if 'output' in line.lower() or any(n in line for n in KEEP_ROOT):
                out.append({'file': str(p.relative_to(WORKSPACE)), 'line': i, 'text': line[:1500]})
    return out

def processes():
    """Failure to inspect a process environment blocks all mutations."""
    if os.name == 'nt':
        code = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-CimInstance Win32_Process | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
        r = subprocess.run(['powershell', '-NoProfile', '-Command', code],
            capture_output=True, timeout=30, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        raw = json.loads(r.stdout.decode('utf-8-sig'))
        rows = [{'environment': 'Windows', 'pid': p['ProcessId'], 'name': p['Name'],
                 'command': p.get('CommandLine') or ''} for p in raw if p['ProcessId'] != os.getpid()]
        # Inspect all currently running WSL distributions without starting a stopped one.
        if shutil.which('wsl'):
            r = subprocess.run(['wsl', '--list', '--running', '--quiet'], capture_output=True,
                timeout=30, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            names = r.stdout.decode('utf-16-le').replace('\x00', '').strip('\ufeff').splitlines()
            for distro in filter(None, (n.strip() for n in names)):
                r = subprocess.run(['wsl', '-d', distro, '--', 'ps', '-eo', 'pid=,comm=,args='],
                    capture_output=True, timeout=30, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
                for line in r.stdout.decode('utf8', errors='replace').splitlines():
                    a = line.strip().split(None, 2)
                    if len(a) == 3:
                        rows.append({'environment': 'WSL/' + distro, 'pid': int(a[0]), 'name': a[1], 'command': a[2]})
        return rows
    r = subprocess.run(['ps', '-eo', 'pid=,comm=,args='], capture_output=True, timeout=30, check=True, text=True)
    return [{'environment': 'native', 'pid': int(a[0]), 'name': a[1], 'command': a[2]}
        for a in (line.strip().split(None, 2) for line in r.stdout.splitlines()) if len(a) == 3 and int(a[0]) != os.getpid()]

def busy(rel, rows, root=OUTPUTS):
    full = str(root / rel).replace('\\', '/').casefold()
    linux = '/mnt/' + full[0] + full[2:] if len(full) > 1 and full[1] == ':' else full
    relative = 'outputs/' + rel.casefold()
    parent = rel.rsplit('/', 1)[0] if '/' in rel else rel
    for p in rows:
        command = p['command'].replace('\\', '/').casefold()
        if any(x in command for x in [full, linux, relative]):
            return 'Process uses path: ' + str(p['pid']) + ' ' + p['name']
        if parent in command or ('browser_test.py' in command and rel in PROFILES):
            return 'Process may use parent/test: ' + str(p['pid']) + ' ' + p['name']
    return ''

def exclusive_probe(path):
    # Open with no sharing on Windows to detect an active handle, even when its
    # path is absent from process arguments. Never chmod or stop a process.
    if os.name != 'nt':
        return
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    k.CreateFileW.restype = ctypes.c_void_p
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = k.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), 'File busy or inaccessible', str(path))
    k.CloseHandle(handle)

def protected(rel, tracked, root=OUTPUTS):
    p = Path(rel)
    return (p.parts[0] in PROTECTED_ROOTS or p.name in PROTECTED_NAMES
        or p.suffix.lower() == '.zip' or p.name.endswith('.manifest.json')
        or str((root / p).absolute()).casefold() in tracked)

def eligible(entry, inventory, tracked, rows, refs, root=OUTPUTS):
    rel = entry['path']
    target = checked(root, root / rel)
    children = [r for r in inventory if r['path'] == rel or r['path'].startswith(rel + '/')]
    for r in children:
        checked(root, root / r['path'])
        if r['kind'] == 'link' or protected(r['path'], tracked, root):
            raise ValueError('Protected/tracked/link descendant: ' + r['path'])
        if time.time_ns() - r['mtime_ns'] < 300 * 10**9:
            raise ValueError('Modified within 5 minutes: ' + r['path'])
    reason = busy(rel, rows, root)
    if reason:
        raise ValueError(reason)
    if rel in PROFILES:
        if load(checked(root, root / PROFILES[rel])).get('status') != 'PASS':
            raise ValueError('Completed browser verification report missing')
        if not (target / 'Local State').is_file() or not (target / 'Default').is_dir():
            raise ValueError('Not the reviewed generated browser-profile layout')
    elif rel in SYNTHETIC:
        report = load(checked(root, root / STREAM / 'stress.json'))
        if report['old']['normalized_sha256'] != report['new']['normalized_sha256']:
            raise ValueError('Synthetic equivalence proof missing')
        if not all((root / STREAM / name).is_file() for name in ['synthetic.cpp', 'stress_linux.py', 'final_checks.json']):
            raise ValueError('Reproduction source/report missing')
        if digest(target) != SYNTHETIC[rel]:
            raise ValueError('Synthetic output differs from reviewed checksum')
    elif rel == 'time_probe.txt':
        if 'Command being timed: "./router --help"' not in target.read_text(encoding='utf8'):
            raise ValueError('Not the reviewed help-only timing probe')
        if any(rel in r['text'] for r in refs):
            raise ValueError('Probe has an external source/document reference')
        dest = checked(root, root / entry['destination'])
        if dest.exists():
            raise ValueError('Move destination already exists; no overwrite')
    else:
        raise ValueError('Not on the explicit reviewed allowlist')
    for r in children:
        if r['kind'] == 'file':
            exclusive_probe(checked(root, root / r['path']))
    return children

def make_plan(inventory, tracked, rows, refs, root=OUTPUTS):
    plan = []
    for rel in [*PROFILES, *SYNTHETIC, 'time_probe.txt']:
        if not (root / rel).exists():
            continue
        move = rel == 'time_probe.txt'
        entry = {'path': rel, 'class': 'B' if move else 'C', 'action': 'move' if move else 'delete',
            'reason': 'Unreferenced help-only timing probe retained under development.' if move else
                'Closed, completed test browser profile; regenerated by its verification helper.' if rel in PROFILES else
                'Checksum-verified successful 50k-Net synthetic JSON; equivalence/metrics/logs, source and binaries retained; not a public-Case run or score.'}
        if move:
            entry['destination'] = 'development/probes/time_probe.txt'
        children = [r for r in inventory if r['path'] == rel or r['path'].startswith(rel + '/')]
        entry['bytes'] = sum(r['bytes'] for r in children)
        entry['files'] = sum(r['kind'] == 'file' for r in children)
        try:
            eligible(entry, inventory, tracked, rows, refs, root)
        except (OSError, ValueError, KeyError) as e:
            entry.update({'class': 'D', 'action': 'keep', 'skip_reason': str(e)})
        plan.append(entry)
    return plan

def classify(inventory, plan, tracked):
    for r in inventory:
        top = r['path'].split('/')[0]
        r['git_tracked'] = str((OUTPUTS / r['path']).absolute()).casefold() in tracked
        candidate = next((p for p in plan if r['path'] == p['path'] or r['path'].startswith(p['path'] + '/')), None)
        if candidate:
            r.update({k: candidate[k] for k in ['class', 'action', 'reason']})
            if 'skip_reason' in candidate:
                r['reason'] += ' SKIPPED: ' + candidate['skip_reason']
        else:
            r['class'] = 'A' if top in PROTECTED_ROOTS or top in KEEP_ROOT or protected(r['path'], tracked) else 'D'
            r['action'] = 'keep'
            r['reason'] = KEEP_ROOT.get(top, 'Protected history/report/cache/dependency.' if r['class'] == 'A'
                else 'Historical development evidence or unknown purpose; retain existing paths and reports.')

def write_new(path, value):
    with path.open('x', encoding='utf8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')

def execute(plan, inventory, tracked, refs, report):
    results = []
    for entry in plan:
        if entry['action'] == 'keep':
            continue
        item = {'path': entry['path'], 'action': entry['action'], 'deleted_bytes': 0, 'files': []}
        try:
            # Fresh process inspection and unchanged complete group membership.
            rows = processes()
            current = scan(OUTPUTS)
            children = eligible(entry, current, tracked, rows, refs, OUTPUTS)
            before = {r['path']: (r['kind'], r['bytes'], r['mtime_ns']) for r in inventory
                if r['path'] == entry['path'] or r['path'].startswith(entry['path'] + '/')}
            now = {r['path']: (r['kind'], r['bytes'], r['mtime_ns']) for r in children}
            if before != now:
                raise ValueError('Candidate changed since preview; skipped')
            if entry['action'] == 'move':
                src = checked(OUTPUTS, OUTPUTS / entry['path'])
                dst = checked(OUTPUTS, OUTPUTS / entry['destination'])
                dst.parent.mkdir(parents=True, exist_ok=True)
                checksum = digest(src)
                # Same-volume link+unlink is a non-overwriting file move on both platforms.
                os.link(src, dst)
                if digest(dst) != checksum:
                    raise ValueError('Destination hash mismatch; source retained')
                src.unlink()
                item.update({'destination': entry['destination'], 'sha256': checksum, 'moved_bytes': entry['bytes']})
            else:
                for r in children:
                    if r['kind'] != 'file':
                        continue
                    path = checked(OUTPUTS, OUTPUTS / r['path'])
                    s = path.stat()
                    if (s.st_size, s.st_mtime_ns) != (r['bytes'], r['mtime_ns']):
                        raise ValueError('File changed during cleanup: ' + r['path'])
                    exclusive_probe(path)
                    checksum = digest(path)
                    path.unlink()
                    item['deleted_bytes'] += r['bytes']
                    item['files'].append({'path': r['path'], 'bytes': r['bytes'], 'sha256': checksum})
                for r in sorted(children, key=lambda r: len(Path(r['path']).parts), reverse=True):
                    if r['kind'] == 'directory':
                        checked(OUTPUTS, OUTPUTS / r['path']).rmdir()  # Only empty dirs; never recursive delete.
            item['status'] = 'completed'
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
            item.update({'status': 'skipped_or_partial', 'error': str(e)})
        results.append(item)
        write_new(report / ('action_%02d.json' % len(results)), item)
    return results

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = p.add_mutually_exclusive_group()
    group.add_argument('--dry-run', action='store_true', help='Preview only (default)')
    group.add_argument('--apply', action='store_true', help='Apply the reviewed safe allowlist with an audit trail')
    p.add_argument('--report-dir', type=Path, help='New, non-existing directory under outputs/reports')
    p.add_argument('--case001-prune', action='store_true', help='Opt-in exact reviewed case001 manifest; tuning and other Cases remain protected')
    a = p.parse_args()
    if a.case001_prune:
        if a.report_dir:p.error('case001 pruning uses one fixed final audit; preview writes nothing')
        try:
            from cleanup_case001 import execute as prune_case001
            result=prune_case001(apply=a.apply)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0
        except (OSError,ValueError,KeyError,subprocess.SubprocessError) as e:
            p.exit(2,'Case001 cleanup aborted safely: '+str(e)+'\n')
    if not OUTPUTS.is_dir() or OUTPUTS.resolve() != OUTPUTS.absolute():
        p.error('outputs must be a real directory, not a redirected path')
    try:
        tracked = tracked_files()
        refs = source_references()
        process_error = None
        try:
            rows = processes()
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            rows = []
            process_error = str(e)
        inventory = scan(OUTPUTS)
        plan = make_plan(inventory, tracked, rows, refs)
        if process_error:
            for entry in plan:
                entry.update({'class': 'D', 'action': 'keep', 'skip_reason': 'Process inspection unavailable: ' + process_error})
        classify(inventory, plan, tracked)
        before_bytes = sum(r['bytes'] for r in inventory)
        preview = {'mode': 'apply' if a.apply else 'dry-run', 'output_root': str(OUTPUTS.resolve()),
            'total_bytes_before': before_bytes, 'file_count': sum(r['kind'] == 'file' for r in inventory),
            'delete_bytes_planned': sum(x['bytes'] for x in plan if x['action'] == 'delete'),
            'plan': plan, 'process_inspection_error': process_error,
            'preserved_in_use': [r for r in rows if 'code_3dic' in r['command'].lower() or
                ('python' in r['name'].lower() and any(s in r['command'] for s in ['ui/app.py', 'run_case.py']))]}
        report = None
        if a.apply or a.report_dir:
            report = a.report_dir or OUTPUTS / 'reports' / ('cleanup_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8])
            report = checked(OUTPUTS, report.absolute())
            if not report.is_relative_to(OUTPUTS / 'reports'):
                p.error('Audit directory must be inside outputs/reports')
            report.mkdir(parents=True, exist_ok=False)
            write_new(report / 'inventory_before.json', inventory)
            write_new(report / 'references.json', refs)
            write_new(report / 'processes.json', rows)
            write_new(report / 'dry_run.json', preview)
        if a.apply:
            actions = execute(plan, inventory, tracked, refs, report)
            for directory in ['submissions', 'development']:
                checked(OUTPUTS, OUTPUTS / directory).mkdir(exist_ok=True)
            after = scan(OUTPUTS)
            summary = {'before_bytes': before_bytes, 'after_bytes_at_scan': sum(r['bytes'] for r in after),
                'deleted_file_bytes': sum(x['deleted_bytes'] for x in actions),
                'moved_bytes': sum(x.get('moved_bytes', 0) for x in actions),
                'audit_directory': str(report), 'actions': [{k: v for k, v in x.items() if k != 'files'} for x in actions],
                'note': 'Logical file bytes; active run growth and audit records change total size independently. No history/cache/ZIP was deleted.'}
            write_new(report / 'inventory_after.json', after)
            write_new(report / 'summary.json', summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 2 if process_error else 1 if any(x['status'] != 'completed' for x in actions) else 0
        if report:
            preview['audit_directory'] = str(report)
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        p.exit(2, 'Cleanup aborted safely: ' + str(e) + '\n')

if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf8')
    raise SystemExit(main())
