"""Explicit, reviewed case001 pruning; invoked only by --case001-prune.

The policy pins the historical summary and exact per-file snapshots. Unknown
directories, tuning, representatives, all other Cases and prior submissions
are outside this allowlist. Preview has no filesystem side effects.
"""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from common import PROJECT, OUTPUTS
from case001_history import SUMMARY
import cleanup_outputs as safe

POLICY = 'docs/case001_retention.json'
AUDIT = 'reports/case001_cleanup.json'


def snapshot(path, root):
    safe.checked(root, path)
    rows = []
    for p in sorted(path.rglob('*')):
        safe.checked(root, p)
        stat = p.lstat()
        if getattr(stat, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Reparse point rejected: ' + str(p))
        if not p.is_file() and not p.is_dir():
            raise ValueError('Unexpected file type: ' + str(p))
        rows.append(dict(path=p.relative_to(path).as_posix(), kind='file' if p.is_file() else 'directory',
                         bytes=stat.st_size if p.is_file() else 0, mtime_ns=stat.st_mtime_ns,
                         sha256=safe.digest(p) if p.is_file() else None))
    return rows


def process_guard(rows, label):
    # Inspect both host and already-running WSL distributions. A live writer or
    # experiment manager blocks pruning even if its current cwd is unknown.
    pattern = r'(run_case|evaluate|linux_supervisor|research_v3|compare_router_v3|validate_router_version|validate_v3_final|tune_parameters)\.py'
    for row in rows:
        name = row['name'].lower()
        command = row['command'].replace('\\', '/').lower()
        if name.startswith('router') or name in {'score', 'score.exe'} or (
                ('python' in name or name in {'bash', 'sh', 'wsl.exe'}) and re.search(pattern, command)):
            raise ValueError('Active routing/scoring/experiment process: ' + str(row['pid']))
        if label.lower() in command and name not in {'powershell.exe', 'pwsh.exe'}:
            raise ValueError('Process references target: ' + str(row['pid']))


def reference_files(project):
    workspace = project.parents[1]
    files = list(workspace.glob('*.md'))
    for name in ['docs', 'scripts', 'tests', 'ui', 'outputs/reports']:
        files.extend((project/name).rglob('*'))
    return [p for p in files if p.is_file() and p.suffix.lower() in {
            '.md', '.json', '.jsonl', '.csv', '.py', '.js', '.html', '.txt', '.log', '.ps1'}
            and p not in {project/POLICY, project/'outputs'/SUMMARY, project/'outputs'/AUDIT}]


def unresolved_references(project, labels):
    """Check active path references, including slash/JSON-escaped Windows forms.

    history://case001/<label>/... denotes removed historical evidence. It is
    intentionally not a file path and may never be opened as one.
    """
    found = []
    pattern = re.compile(r'open_3d_case001[/\\]+(' + '|'.join(map(re.escape, labels)) + r')(?=[/\\\s\"\'<>)]|$)') if labels else None
    if not pattern:
        return found
    for p in reference_files(project):
        with p.open(encoding='utf8', errors='replace') as lines:
            for number, line in enumerate(lines, 1):
                if pattern.search(line):
                    found.append({'file': str(p), 'line': number, 'text': line[:500]})
    return found


def preview(project=PROJECT, root=OUTPUTS):
    policy = safe.load(project/POLICY)
    summary_path = safe.checked(root, root/SUMMARY)
    if safe.digest(summary_path) != policy['summary_sha256']:
        raise ValueError('Summary differs from reviewed policy; no deletion allowed')
    summary = safe.load(summary_path)
    case = safe.checked(root, root/'runs/open_3d_case001')
    rows = safe.processes()  # failure is fatal, not interpreted as an empty list
    tracked = safe.tracked_files()
    plan = []
    references = unresolved_references(project, list(policy['delete']))
    for directory in sorted(case.iterdir()):
        safe.checked(root, directory)
        label = directory.name
        record = summary['records'].get(label, {})
        entry = dict(label=label, router_version=record.get('identity', 'UNKNOWN'), action='KEEP',
                     reason=policy['keep'].get(label, 'Unknown/unreviewed directory'),
                     experiment_type=record.get('experiment_type', 'UNKNOWN'))
        if label in policy['delete']:
            try:
                if label.startswith('tune_') or label in policy['keep']:
                    raise ValueError('Protected tuning/representative collision')
                if not record.get('verified_from_original') or record.get('retention') != 'DELETE':
                    raise ValueError('Measured summary missing or not reviewed for deletion')
                if record['guide_sha256'] != policy['delete'][label]['guide_sha256']:
                    raise ValueError('Guide identity differs from policy')
                process_guard(rows, label)
                current = snapshot(directory, root)
                if current != record['files']:
                    raise ValueError('SHA256/size/mtime/membership differs from reviewed snapshot')
                for f in current:
                    if f['kind'] == 'file':
                        path = directory/f['path']
                        if str(path.absolute()).casefold() in tracked:
                            raise ValueError('Git-tracked run file')
                        safe.exclusive_probe(path)
                if references:
                    raise ValueError('Unresolved report/source dependencies; see references')
                entry.update(action='DELETE', reason='Verified historical metrics extracted; raw dependencies detached',
                             bytes=sum(f['bytes'] for f in current), files=sum(f['kind']=='file' for f in current))
            except (OSError, ValueError) as e:
                entry['reason'] = str(e)
        plan.append(entry)
    return dict(mode='dry-run', plan=plan, unresolved_references=references,
                detached_reports=[r['path'] for r in policy.get('detached_reports',[])],
                delete_directories=sum(r['action']=='DELETE' for r in plan),
                delete_files=sum(r.get('files',0) for r in plan),
                delete_bytes=sum(r.get('bytes',0) for r in plan),
                summary_sha256=policy['summary_sha256']), summary


def execute(project=PROJECT, root=OUTPUTS, apply=False):
    plan, summary = preview(project, root)
    if not apply:
        return plan
    destination = safe.checked(root, root/AUDIT)
    if destination.exists():
        raise ValueError('Final audit already exists; no overwrite. Review prior audit before a new policy')
    if plan['unresolved_references']:
        raise ValueError('References remain; cleanup not started')
    # Persist exactly one audit, including interrupted/partial actions. Write
    # the reviewed preview before deletion, flush at each completed directory.
    result = plan | dict(mode='apply', started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), actions=[])
    def persist():
        with destination.open('w', encoding='utf8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    with destination.open('x', encoding='utf8') as f:
        json.dump(result, f, indent=2);f.flush();os.fsync(f.fileno())
    try:
        for entry in plan['plan']:
            if entry['action'] != 'DELETE':
                continue
            directory = safe.checked(root, root/'runs/open_3d_case001'/entry['label'])
            process_guard(safe.processes(), entry['label'])
            expected = summary['records'][entry['label']]['files']
            if snapshot(directory, root) != expected:
                raise ValueError('Candidate changed after preview: ' + entry['label'])
            action = dict(label=entry['label'], deleted_bytes=0, status='STARTED')
            result['actions'].append(action);persist()
            # No recursive deletion. Exact reviewed files, then empty dirs.
            for record in expected:
                if record['kind'] != 'file':
                    continue
                path = safe.checked(root, directory/record['path']);stat=path.stat()
                safe.exclusive_probe(path)
                if (stat.st_size,stat.st_mtime_ns,safe.digest(path)) != (record['bytes'],record['mtime_ns'],record['sha256']):
                    raise ValueError('File changed during deletion: ' + str(path))
                path.unlink();action['deleted_bytes'] += record['bytes']
            for record in sorted(expected,key=lambda r: len(Path(r['path']).parts), reverse=True):
                if record['kind']=='directory':safe.checked(root,directory/record['path']).rmdir()
            directory.rmdir();action['status']='DELETED';persist()
        result['status']='COMPLETED'
    except BaseException as e:
        result.update(status='INTERRUPTED_OR_PARTIAL', error=str(e));raise
    finally:
        result['deleted_bytes']=sum(a['deleted_bytes'] for a in result['actions']);persist()
    return result
