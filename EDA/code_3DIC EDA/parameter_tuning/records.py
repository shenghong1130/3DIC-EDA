"""Read actual runs/reports; bounded result extraction, no synthetic official scores."""
from pathlib import Path
import hashlib
import json
import math
import sys
from support import PROJECT, load, sha256, atomic, TuningError, owned_label

sys.path.insert(0, str(PROJECT/'ui'))
from result_store import Cursor  # Read-only bounded reader; no Store/UI cache is created.

def number(value):
    return value if not isinstance(value, bool) and isinstance(value, (int,float)) and math.isfinite(value) else None

def at(data, *path):
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data

def first(*values):
    return next((v for v in values if v is not None), None)

def official_metrics(report):
    """Current latest/legacy nested schema plus explicit total-field aliases only."""
    total = None; total_source = None
    for container, prefix in [(report,''), (at(report,'score'),'score.'), (at(report,'scores'),'scores.')]:
        if isinstance(container, dict):
            for key in ['total_score','overall_score','final_score']+(['total'] if prefix else []):
                if number(container.get(key)) is not None:
                    total = number(container[key]); total_source = prefix+key; break
        if total is not None:
            break
    if total is None and number(report.get('score')) is not None:
        total = report['score']; total_source = 'score'
    return {
        'total_score': total, 'total_score_source': total_source,
        'wirelength_score': number(at(report,'score','wirelength_score')),
        'congestion_score': number(at(report,'score','congestion_score')),
        'thermal_score': number(at(report,'score','thermal_score')),
        'wirelength_dbu': number(at(report,'wirelength','total_dbu')),
        'wirelength_um': number(at(report,'wirelength','total_um')),
        'official_overflow': number(at(report,'congestion','total_overflow')),
        'official_max_overflow': number(at(report,'congestion','max_overflow')),
        'pearson': number(at(report,'thermal_tsv','correlation_above_ambient')),
        'official_tsv_count': number(at(report,'wirelength','tsv_count')),
    }

def local_summary(path):
    reader = Cursor(path); out = {}; mapping = hashlib.sha256(); density = hashlib.sha256(); nets = 0
    try:
        reader.take('{')
        while reader.char() != '}':
            key = reader.value()[0]; reader.take(':')
            if key == 'nets':
                for net, _, _ in reader.array():
                    nets += 1
                    if net.get('cross_die'):
                        mapping.update(json.dumps([net['net'],net.get('tsv_node')],ensure_ascii=False,separators=(',',':')).encode('utf8')+b'\n')
            elif key == 'tsv_density':
                for value, _, _ in reader.array():
                    density.update((str(float(value))+'\n').encode('ascii'))
            elif key in {'metrics','iterations','schema_version','source'}:
                out[key] = reader.value()[0]
            else:
                reader.skip()
            if reader.char() == '}':
                break
            reader.take(',')
        reader.take('}')
        if reader.char():
            raise TuningError('Trailing result data')
    finally:
        reader.close()
    if not isinstance(out.get('metrics'), dict) or number(out['metrics'].get('routed_nets')) != nets:
        raise TuningError('Result Net count/metrics incomplete')
    out.update(tsv_mapping_sha256=mapping.hexdigest(),tsv_density_sha256=density.hexdigest(),net_count=nets)
    return out

def artifacts(directory, engine):
    paths = ['run.json','result.json','route.guide',f'official_{engine}/invocation.json',f'official_{engine}/evaluation_report.json']
    return {name:sha256(directory/name) for name in paths if (directory/name).is_file()}

def artifact_errors(directory, sealed):
    errors = []
    for name, expected in sealed.items():
        if name not in {'run.json','result.json','route.guide',
                'official_latest/invocation.json','official_latest/evaluation_report.json',
                'official_legacy/invocation.json','official_legacy/evaluation_report.json'}:
            errors.append('Invalid sealed artifact path: '+name);continue
        try:
            if sha256(directory/name) != expected:
                errors.append('Artifact changed: '+name)
        except OSError:
            errors.append('Artifact missing: '+name)
    return errors

def run_errors(plan, variant, label, run):
    errors = []
    fp = plan['fingerprints']
    for key, expected in [('case',plan['case']),('label',label),('config',variant['config']),
            ('backend',plan['backend']),('router_sha256',fp['router_sha256']),('input_sha256',fp['input_sha256'])]:
        if run.get(key) != expected:
            errors.append('Run identity/config/version mismatch: '+key)
    expected_monitor = {'enabled': True, 'sample_interval_seconds': plan['monitoring']['monitor_interval'],
        'heartbeat_seconds': plan['monitoring']['heartbeat_interval'], 'progress_every': plan['monitoring']['progress_every'],
        'stall_after_seconds': plan['monitoring']['stall_after']}
    for key, expected in expected_monitor.items():
        if at(run,'monitoring',key) != expected:
            errors.append('Run monitoring mismatch: '+key)
    # Require the effective C++ --time-limit, not merely JSON's nominal budget.
    import shlex
    command = run.get('command', [])
    if plan['backend'] == 'wsl':
        command = shlex.split(command[-1]) if command else []
    try:
        budget = float(command[command.index('--time-limit')+1])
        if budget != plan['router_timeout']:
            errors.append('Effective CLI budget mismatch')
    except (ValueError, IndexError, TypeError):
        errors.append('Effective CLI budget missing')
    return errors

def official_state(invocation, report):
    if not invocation and not report:
        return 'NOT_EVALUATED'
    if invocation.get('process_status') == 'TIMEOUT' or invocation.get('returncode') == 124:
        return 'TIMEOUT'
    if not invocation or not report:
        return 'INCOMPLETE'
    return 'PASS' if (invocation.get('status') == report.get('status') == 'PASS'
        and invocation.get('returncode') == 0 and invocation.get('process_status') == 'FINISHED') else 'FAIL'

def read_optional(path):
    return load(path) if path.is_file() else {}

def collect(plan, variant, entry, report_dir):
    row = {'id':variant['id'],'case':plan['case'],'label':None,'state':entry['state'],
        'parameters':variant['config'],'changes':variant['changes'],'config_sha256':variant['config_sha256'],
        'config_time_limit':variant['config']['time_limit'],'effective_router_timeout':plan['router_timeout'],
        'evaluation_timeout':plan['evaluation_timeout'],'router_status':'NOT_RUN','official_status':'NOT_EVALUATED',
        'failure_category':None,'failure_reason':None,'guide_path':None,'guide_sha256':None,'report_path':None,
        'router_runtime_seconds':None,'router_cpp_runtime_seconds':None,'router_peak_rss_bytes':None,
        'memory_source':None,'evaluator_runtime_seconds':None,'evaluator_peak_rss_bytes':None,
        'tsv_count':None,'tsv_mapping_sha256':None,'tsv_density_sha256':None,'iterations_executed':None,
        'iterations_accepted':None,'stage_seconds':{},'router_sha256':None,'evaluator_sha256':None,
        'input_sha256':None,'integrity_errors':[],'eligible':False,
        **official_metrics({})}
    if not entry['attempts']:
        return row
    attempt = entry['attempts'][-1]; label = attempt['label']; row['label'] = label
    try:
        owned_label(plan,variant,label)
    except (TuningError,ValueError) as e:
        row['integrity_errors'].append(str(e));return row
    directory = PROJECT/'outputs/runs'/plan['case']/label
    try:
        run = read_optional(directory/'run.json')
        inv = read_optional(directory/f"official_{plan['engine']}/invocation.json")
        report_path = directory/f"official_{plan['engine']}/evaluation_report.json"
        official = read_optional(report_path)
        errors = artifact_errors(directory, attempt.get('artifacts',{}))
        required={'run.json','result.json','route.guide',
            f"official_{plan['engine']}/invocation.json",f"official_{plan['engine']}/evaluation_report.json"}
        if entry['state']=='COMPLETED' and not required <= set(attempt.get('artifacts',{})):
            errors.append('Completed attempt is missing sealed artifact hashes')
        if run:
            errors += run_errors(plan, variant, label, run)
        else:
            errors.append('Run manifest missing')
        guide = directory/'route.guide'; result = directory/'result.json'
        routed = run.get('status') == 'ROUTED' and run.get('returncode') == 0 and run.get('process_status') == 'FINISHED' and guide.is_file() and result.is_file()
        row.update(router_status='ROUTED' if routed else 'TIMEOUT' if run.get('failure_category') == 'TIMEOUT' or run.get('process_status') == 'TIMEOUT' else 'FAIL' if run else 'NOT_RUN',
            official_status=official_state(inv,official),router_runtime_seconds=number(run.get('runtime_seconds')),
            router_peak_rss_bytes=first(number(run.get('router_peak_rss_bytes')),number(run.get('peak_memory_bytes'))),
            memory_source='observed_router_pid' if number(run.get('router_peak_rss_bytes')) is not None else run.get('peak_memory_source','gnu_time_command_tree' if run.get('peak_memory_bytes') is not None else None),
            evaluator_runtime_seconds=number(inv.get('runtime_seconds')),evaluator_peak_rss_bytes=number(inv.get('peak_memory_bytes')),
            failure_category=run.get('failure_category') if not routed else inv.get('process_status') if row.get('official_status') == 'FAIL' else None,
            failure_reason=run.get('failure_reason') or inv.get('failure_reason') or attempt.get('reason'),
            router_sha256=run.get('router_sha256'),evaluator_sha256=inv.get('evaluator_sha256'),input_sha256=run.get('input_sha256'),
            last_phase=run.get('last_phase'),guide_path=str(guide) if guide.exists() else None,
            guide_sha256=sha256(guide) if guide.exists() else None,report_path=str(report_path) if report_path.exists() else None,
            **official_metrics(official))
        if inv:
            if inv.get('engine') != plan['engine'] or inv.get('evaluator_sha256') != plan['fingerprints']['evaluator_sha256']:
                errors.append('Evaluator version/engine mismatch')
            if inv.get('evaluated_input_sha256') != plan['fingerprints']['input_sha256']:
                errors.append('Scored input hashes missing/different')
            if inv.get('report') != official:
                errors.append('Invocation/report disagreement')
        if routed and not errors:
            cache = Path(report_dir)/'extracted'/f'{label}.json'
            signature = sha256(result)
            local = load(cache) if cache.is_file() else {}
            if local.get('result_sha256') != signature:
                local = {'result_sha256':signature,'summary':local_summary(result)}; atomic(cache,local)
            data = local['summary']; rounds = data.get('iterations',[])
            row.update(tsv_count=number(data['metrics'].get('tsv_count')),router_cpp_runtime_seconds=number(data['metrics'].get('runtime_seconds')),
                tsv_mapping_sha256=data['tsv_mapping_sha256'],tsv_density_sha256=data['tsv_density_sha256'],
                iterations_executed=max(0,len(rounds)-1),iterations_accepted=sum(r.get('accepted') is True for r in rounds))
        progress = directory/'progress.jsonl'
        if progress.is_file():
            with progress.open(encoding='utf8') as f:
                for line in f:
                    try:
                        event = json.loads(line)
                        if event.get('event') == 'phase_completed' and number(event.get('stage_seconds')) is not None:
                            phase = event.get('phase','UNKNOWN'); row['stage_seconds'][phase] = row['stage_seconds'].get(phase,0)+event['stage_seconds']
                    except ValueError:
                        continue
        if row['official_status'] in {'FAIL','TIMEOUT','INCOMPLETE'} and routed:
            row['failure_category'] = 'EVALUATION_' + row['official_status']
        row['integrity_errors'] = errors
        row['eligible'] = (routed and row['official_status'] == 'PASS' and not errors and entry['state'] == 'COMPLETED'
            and bool(attempt.get('artifacts')))
    except (OSError, ValueError, KeyError, TuningError) as e:
        row['integrity_errors'].append(type(e).__name__+': '+str(e))
        row['failure_reason'] = str(e); row['eligible'] = False
    return row
