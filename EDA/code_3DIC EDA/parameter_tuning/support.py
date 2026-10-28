"""Isolated experiment persistence, process locks and reproducibility checks."""
from pathlib import Path
import contextlib
import datetime
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(PROJECT / 'scripts'))
from common import catalog, safe_label, sha256, linux_path, EDA

class TuningError(RuntimeError):
    pass

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def load(path):
    with Path(path).open(encoding='utf8') as f:
        return json.load(f)

def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf8')

def object_hash(value):
    return hashlib.sha256(encoded(value)).hexdigest()

def atomic(path, value):
    atomic_bytes(path, encoded(value))

def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()

def immutable(path, value):
    """Publish closed immutable JSON without overwriting an existing inode."""
    path = Path(path)
    if path.exists():
        if load(path) != value:
            raise TuningError('Existing immutable file differs: ' + str(path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as f:
            f.write(encoded(value)); f.flush(); os.fsync(f.fileno())
        os.link(temp, path)  # Fails on conflict; never replaces an existing file.
    finally:
        if temp.exists():
            temp.unlink()

@contextlib.contextmanager
def locked(path):
    """OS advisory lock, automatically released on crash; stale PID files are not locks."""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open('a+b')
    try:
        if not path.stat().st_size:
            f.write(b'0'); f.flush()
        f.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise TuningError('Another experiment manager holds ' + str(path)) from e
        yield
    finally:
        # Closing the descriptor releases either platform's lock.
        f.close()

def validate_config(config):
    expected = {'wire','via','tsv','congestion','history','thermal','iterations','stagnation',
                'margin','search_limit','time_limit','trace_mb'}
    if set(config) != expected:
        raise TuningError('Default/config keys differ from the supported C++ Config: ' + str(set(config) ^ expected))
    for k, v in config.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise TuningError('Non-finite/non-numeric config: ' + k)
        if k in {'iterations','stagnation','margin','search_limit','trace_mb'} and int(v) != v:
            raise TuningError('Integer config required: ' + k)
        if v < 0 or k in {'wire','via','stagnation','margin','search_limit','time_limit','trace_mb'} and v <= 0:
            raise TuningError('Invalid positive/nonnegative config: ' + k)
    if config['iterations'] > 100:
        raise TuningError('C++ iterations limit is 100')

def variants(default, settings, suite):
    validate_config(default)
    spec = settings['suites'][suite]
    out = []; ids = set()
    for key, value in settings['fixed_parameters'].items():
        if default.get(key) != value:
            raise TuningError('Actual default does not match the declared fixed parameter: ' + key)
    for row in spec['variants']:
        name = safe_label(row['id']); changes = row['changes']
        if name in ids or not set(changes) <= {'via','congestion','history','thermal','iterations','margin'}:
            raise TuningError('Duplicate ID or unsupported phase-one parameter: ' + name)
        if spec['mode'] == 'oat' and len(changes) != (0 if name == 'baseline' else 1):
            raise TuningError('OAT must change exactly one field: ' + name)
        config = dict(default, **changes); validate_config(config)
        diff = {k: v for k, v in config.items() if v != default[k]}
        if diff != changes:
            raise TuningError('Declared parameter change is not an actual difference: ' + name)
        if name == 'baseline' and changes:
            raise TuningError('Baseline must equal actual default.json')
        ids.add(name); out.append({'id': name, 'changes': changes, 'config': config})
    if not out or out[0]['id'] != 'baseline' or spec['mode'] not in {'oat', 'combinations'}:
        raise TuningError('Baseline must be first; suite mode must be oat/combinations')
    return out

def unique_label(experiment, variant, batch, attempt=1):
    return safe_label(f'tune_{experiment[:18]}_{variant[:21]}_{batch[:8]}_a{attempt:03}_{uuid.uuid4().hex[:6]}')

def owned_label(plan, variant, label):
    safe_label(label)
    prefix=f"tune_{plan['experiment'][:18]}_{variant['id'][:21]}_{plan['batch_id'][:8]}_a"
    if not label.startswith(prefix):
        raise TuningError('Label does not belong to this experiment/candidate: '+label)
    return label

def validate_state(plan, state):
    valid={'PENDING','ROUTING','ROUTED','EVALUATING','COMPLETED','ROUTER_FAILED','EVALUATION_FAILED','INTERRUPTED','BLOCKED'}
    seen=set()
    for row in plan['variants']:
        entry=state['experiments'][row['id']]
        if entry['state'] not in valid:
            raise TuningError('Unknown experiment state: '+str(entry['state']))
        for attempt in entry['attempts']:
            label=owned_label(plan,row,attempt['label'])
            if label in seen:
                raise TuningError('A Label is assigned to multiple attempts')
            seen.add(label)
            run,evaluate=commands(plan,row,label)
            if attempt.get('router_command')!=run or attempt.get('evaluation_command')!=evaluate:
                raise TuningError('Stored helper commands differ from the frozen plan: '+label)

def commands(plan, variant, label):
    common = [sys.executable, '-B']
    monitor = plan['monitoring']
    run = common + [str(PROJECT/'scripts/run_case.py'), '--case', plan['case'],
        '--backend', plan['backend'], '--label', label, '--router', plan['router_path'],
        '--config', variant['config_path'], '--time-limit', str(plan['router_timeout'])]
    for key in ['progress_every', 'monitor_interval', 'heartbeat_interval', 'stall_after']:
        run += ['--' + key.replace('_','-'), str(monitor[key])]
    evaluate = common + [str(PROJECT/'scripts/evaluate.py'), '--case', plan['case'],
        '--label', label, '--backend', plan['backend'], '--engine', plan['engine'],
        '--timeout', str(plan['evaluation_timeout'])]
    return run, evaluate

def environment(backend, probe=True):
    data = {'platform': platform.platform(), 'python': sys.version, 'python_executable': sys.executable,
            'backend': backend, 'wsl_distro': 'Ubuntu' if backend == 'wsl' else None,
            'environment': {k: os.environ.get(k) for k in ['WSLENV','LD_LIBRARY_PATH','OMP_NUM_THREADS',
                'OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','PYTHONHASHSEED']}}
    if backend == 'wsl' and probe:
        # Read-only environment probe; never invokes Router or Evaluator.
        cmd = ['wsl','-d','Ubuntu','--','python3','-c',
            'import json,platform,sys;print(json.dumps({"kernel":platform.release(),"machine":platform.machine(),"python":sys.version,"libc":platform.libc_ver(),"os_release":open("/etc/os-release").read()}))']
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=30, check=True)
            data['linux'] = json.loads(r.stdout.decode('utf8'))
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            raise TuningError('WSL environment probe failed: ' + str(e)) from e
    return data

def fingerprints(case, backend, engine, router, probe=True):
    source = catalog().get(case)
    if not source:
        raise TuningError('Case not found: ' + case)
    tool = EDA / ('评估程序' if engine == 'latest' else 'eda-elite-challenge/eda-elite-challenge2026/evaluator')
    tools = ([tool / n for n in ['score','POST9.dat','POWV9.dat']] if engine == 'latest' else
        [p for p in tool.iterdir() if p.suffix in {'.py','.so','.dat'} or '.so.' in p.name])
    scripts = [PROJECT/'scripts'/n for n in ['run_case.py','evaluate.py','common.py','diagnostics.py','linux_supervisor.py']]
    sources = sorted(p for folder in ['src','include'] for p in (PROJECT/folder).glob('*') if p.is_file())
    files = [Path(source[k]) for k in ['def','lef','thermal']] + [Path(router), PROJECT/'configs/default.json', *tools, *scripts, *sources, PROJECT/'ui/result_store.py']
    try:
        hashes = {str(p.resolve()): sha256(p) for p in files}
    except OSError as e:
        raise TuningError('Required input/tool missing: ' + str(e)) from e
    return {'files': hashes, 'input_sha256': {k: hashes[str(Path(source[k]).resolve())] for k in ['def','lef','thermal']},
        'router_sha256': hashes[str(Path(router).resolve())],
        'evaluator_sha256': hashes[str((tool/('score' if engine=='latest' else 'score.py')).resolve())],
        'inputs': {k: source[k] for k in ['def','lef','thermal']}, 'environment': environment(backend, probe),
        'warning': source.get('warning')}

def verify_files(plan, check_environment=False):
    problems = []
    for name, expected in plan['fingerprints']['files'].items():
        try:
            if sha256(Path(name)) != expected:
                problems.append('Changed SHA256: ' + name)
        except OSError:
            problems.append('Missing: ' + name)
    for row in plan['variants']:
        try:
            if sha256(Path(row['config_path'])) != row['config_sha256'] or load(row['config_path']) != row['config']:
                problems.append('Config changed: ' + row['id'])
        except (OSError, ValueError):
            problems.append('Config missing/invalid: ' + row['id'])
    if check_environment and environment(plan['backend']) != plan['fingerprints']['environment']:
        problems.append('Runtime environment differs from frozen plan')
    return problems
