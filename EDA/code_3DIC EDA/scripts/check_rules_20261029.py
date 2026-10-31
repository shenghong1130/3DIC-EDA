"""Read-only, small-case announcement audit; never runs Router or a scorer.

Builds an independent parser-only auditor in TemporaryDirectory, then removes
the executable. A static PASS is NOT an official 20261029 evaluator PASS.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
from common import PROJECT, backend_command, linux_path, existing_run_dir, load_run, sha256


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',required=True)
    parser.add_argument('--label',required=True)
    parser.add_argument('--strict',action='store_true',help='Return nonzero on any static/model mismatch')
    args=parser.parse_args()
    directory=existing_run_dir(args.case,args.label)
    run=load_run(directory)
    if run.get('status')!='ROUTED' or run.get('returncode')!=0:
        parser.error('Audit requires a completed ROUTED record; no routing will be started')
    for key,path in run['inputs'].items():
        if sha256(Path(path))!=run['input_sha256'][key]:parser.error('Input identity changed: '+key)
    result=directory/'result.json'
    if not result.is_file() or result.stat().st_size>64*1024*1024:
        parser.error('This independent audit is limited to existing results <=64 MiB; no large file is parsed')
    backend='wsl' if os.name=='nt' else 'native'
    with tempfile.TemporaryDirectory(prefix='gr_rules_20261029_') as tmp:
        executable=Path(tmp)/'audit'
        build=['g++','-std=c++17','-O2','-I','include','tests/rules_20261029_audit.cpp','src/parser.cpp','-o',linux_path(executable)]
        subprocess.run(backend_command(build,backend,cwd=PROJECT),check=True,timeout=120)
        command=[linux_path(executable),linux_path(run['inputs']['lef']),linux_path(run['inputs']['def']),linux_path(result),linux_path(directory/'route.guide')]
        if args.strict:command+=['--strict']
        return subprocess.run(backend_command(command,backend,cwd=PROJECT),timeout=180).returncode


if __name__=='__main__':
    raise SystemExit(main())
