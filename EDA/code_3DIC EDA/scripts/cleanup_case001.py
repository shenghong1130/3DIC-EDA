"""Retired case001 pruning and read-only snapshot/reference utilities.

The reviewed historical plan has already completed. --case001-prune previews
its retired status; --apply is refused without loading a policy or deleting files.
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
            and p not in {project/'docs/router_versions.json', project/'outputs'/SUMMARY, project/'outputs'/AUDIT}]


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
    # The exact prior plan was fully applied. Never reinterpret it against the
    # migrated layout or infer a new deletion list from a version registry.
    return dict(mode='dry-run', status='RETIRED', delete_directories=0,
                delete_files=0, delete_bytes=0, plan=[],
                note='Case001 pruning is retired; the completed audit is reports/case001_cleanup.json. No run is eligible for automatic deletion.'), None


def execute(project=PROJECT, root=OUTPUTS, apply=False):
    if apply:
        raise ValueError('Case001 pruning is retired; no history is deleted. Read the completed cleanup audit.')
    return preview(project, root)[0]
