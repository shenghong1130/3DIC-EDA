"""Read-only, bounded scoring discovery. Geometry and scorer execution stay separate."""
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import math

from common import PROJECT, OUTPUTS, existing_run_dir, load_run, safe_label
from router_versions import identify_router

REFERENCE = 'reference_20261029'
REFERENCE_LATEST = 'reference_20261103'
OFFICIAL = 'official_original'
ORIGINAL_SHA256 = '4104d0b2869ef7376d716ce9c33b56668e659575ab84615f5c0a5f282e108a8a'
SCORERS = {REFERENCE: 'REFERENCE_20261029', REFERENCE_LATEST: 'REFERENCE_20261103', OFFICIAL: 'OFFICIAL_ORIGINAL'}
REFERENCES = {REFERENCE, REFERENCE_LATEST}
REPORT_LIMIT = 2 * 1024 * 1024


class ScoringConflict(ValueError):
    pass


def read_json(path):
    path = Path(path)
    if not path.exists():
        return {}
    if path.stat().st_size > REPORT_LIMIT:
        raise ScoringConflict('Scoring metadata exceeds 2 MiB: ' + str(path))
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ScoringConflict('Scoring metadata must be an object: ' + str(path))
    return value


@lru_cache(maxsize=256)
def _file_hash(path, size, mtime, ctime):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def file_hash(path):
    path = Path(path)
    stat = path.stat()
    return _file_hash(str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def roots(project=None):
    project = Path(project or PROJECT)
    return project, project / 'outputs/reports/scoring', project / 'outputs/reports/reference_20261029'


def reference_directory(case, label, project=None, scorer=REFERENCE):
    """Both layouts may coexist only when identity AND report content agree."""
    _, canonical, historical = roots(project)
    case, label = safe_label(case), safe_label(label)
    if scorer not in REFERENCES:raise ValueError('Unknown reference scorer')
    paths=[canonical / scorer / case / label]
    if scorer==REFERENCE:paths.append(historical / case / label)
    candidates=[p for p in paths if p.exists()]
    if len(candidates) == 2:
        records = []
        for path in candidates:
            inv = read_json(path / 'invocation.json')
            report = read_json(path / 'reference_report.json')
            if not inv or not report:
                raise ScoringConflict('Incomplete duplicate reference directory: ' + str(path))
            identity = inv.get('identity', {})
            required = ('case', 'label', 'ruleset', 'guide_sha256', 'input_sha256',
                        'router_sha256', 'config_sha256', 'reference_source_sha256')
            # Model-only records have neither label nor Guide; they are not UI results.
            if label == 'physical_model':
                required = ('case', 'ruleset', 'input_sha256', 'reference_source_sha256')
            if any(not identity.get(k) for k in required):
                raise ScoringConflict('Duplicate reference identity is incomplete: ' + str(path))
            records.append((identity, inv.get('status'), inv.get('compiled_binary_sha256'), report))
        if records[0] != records[1]:
            raise ScoringConflict('Canonical and historical reference reports conflict; preserve both: ' + str(candidates))
    return candidates[0] if candidates else None


def official_directory(directory, attempt=None):
    return Path(directory) / ('official_latest_attempts/' + safe_label(attempt) if attempt else 'official_latest')


def _same(field, *values):
    known = [v for v in values if v is not None and v != {} and v != 'UNKNOWN']
    if known and any(v != known[0] for v in known[1:]):
        raise ScoringConflict(field + ' identity mismatch')
    return known[0] if known else None


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _first(*values):
    return next((v for v in values if v is not None), None)


def _metadata(case, label, scorer, run):
    return dict(case=case, label=label, scorer_id=SCORERS[scorer], scorer_source_sha256='UNKNOWN',
                scorer_binary_sha256='UNKNOWN', guide_sha256='UNKNOWN', input_sha256='UNKNOWN',
                router_sha256=run.get('router_sha256') or 'UNKNOWN',
                config_sha256=run.get('config_sha256') or 'UNKNOWN', status='NOT_EVALUATED',
                official=scorer == OFFICIAL, report_path=None, runtime_seconds=None, peak_rss_bytes=None)


def summary(case, label, scorer=REFERENCE, source='own', attempt=None, project=None, directory=None):
    """Only small JSON, manifests and cached file hashes; never result.json/resources.csv."""
    if scorer not in SCORERS:
        raise ValueError('Unknown scorer: ' + scorer)
    if source not in ('own', 'reference'):
        raise ValueError('Unknown result source')
    case, label = safe_label(case), safe_label(label)
    project = Path(project or PROJECT)
    directory = Path(directory) if directory else (existing_run_dir(case, label) if project == PROJECT else project / 'outputs/runs' / case / label)
    run = load_run(directory) if project == PROJECT else read_json(directory / 'run.json')
    if not run:
        raise ValueError('Run not found')
    _same('Case', case, run.get('case'))
    guide = directory / 'route.guide'
    complete = run.get('status') == 'ROUTED' and run.get('returncode', 0) == 0 and guide.is_file()
    result = _metadata(case, label, scorer, run)
    router_identity=identify_router(run.get('router_sha256'))
    result.update(router_version=router_identity['router_version'] if source=='own' else None,
                  router_version_status=router_identity['router_version_status'] if source=='own' else 'NOT_APPLICABLE')
    result.update(router_status='ROUTED' if complete else 'INCOMPLETE' if run.get('status') in ('RUNNING', 'ROUTED') else run.get('status', 'UNKNOWN'),
                  router_recorded_status=run.get('status', 'UNKNOWN'), router_complete=complete,
                  router_runtime_seconds=run.get('runtime_seconds'),
                  router_peak_rss_bytes=_first(run.get('router_peak_rss_bytes'), run.get('peak_memory_bytes')),
                  metrics={}, by_layer={}, errors=[], error_counts={}, report={}, files={},
                  source=source, ruleset=SCORERS[scorer], identity_warnings=[])
    if source == 'reference':
        result['note'] = '比赛参考 Guide 没有独立对应评分记录；不沿用自主 Router 的评分。'
        return result
    path = reference_directory(case, label, project, scorer) if scorer in REFERENCES else official_directory(directory, attempt)
    if not path or not path.exists():
        result['note'] = '没有评分记录。' if complete else 'Router 未完成或正式 Guide 缺失；没有可评分的完整输出。'
        return result
    filename = 'reference_report.json' if scorer in REFERENCES else 'evaluation_report.json'
    inv = read_json(path / 'invocation.json')
    report = read_json(path / filename)
    ident = inv.get('identity', {}) if scorer in REFERENCES else inv
    _same('Case', case, ident.get('case'))
    aliases = {label, run.get('label'), run.get('migration', {}).get('original_label'), *run.get('migration', {}).get('previous_labels', [])}
    if ident.get('label') and ident['label'] not in aliases:
        raise ScoringConflict('Label identity mismatch')
    if not complete:
        result.update(status='INCOMPLETE', note='存在评分文件但 Router 尚未完整交付；不展示成功评分。')
        return result
    recorded_guide = _same('Guide SHA256', ident.get('guide_sha256'), report.get('guide_sha256'))
    actual_guide = file_hash(guide)
    _same('Guide SHA256', actual_guide, recorded_guide)
    recorded_inputs = _same('Input SHA256', ident.get('input_sha256'), inv.get('evaluated_input_sha256'), report.get('input_sha256'))
    _same('Input SHA256', recorded_inputs, run.get('input_sha256'))
    for key, expected in (recorded_inputs or {}).items():
        original = run.get('inputs', {}).get(key)
        if original:
            _same('Input ' + key, expected, file_hash(original))
    _same('Router SHA256', ident.get('router_sha256'), run.get('router_sha256'))
    _same('Config SHA256', ident.get('config_sha256'), run.get('config_sha256'))
    binary = _same('Scorer binary SHA256', inv.get('compiled_binary_sha256') if scorer in REFERENCES else inv.get('evaluator_sha256'), report.get('reference_scorer_sha256'))
    source_hash = _same('Scorer source SHA256', ident.get('reference_source_sha256'), report.get('reference_source_sha256'))
    if scorer in REFERENCES:
        _same('Reference ruleset', SCORERS[scorer], ident.get('ruleset'), report.get('ruleset'))
        if report.get('official') or ident.get('official'):
            raise ScoringConflict('Reference result must not claim official status')
    elif binary and binary != ORIGINAL_SHA256:
        raise ScoringConflict('OFFICIAL_ORIGINAL binary SHA256 differs from the original official ELF')
    result['model_parameters']={'pin_assignment_policy':ident.get('pin_assignment_policy')} if scorer==REFERENCE_LATEST else {}
    result.update(scorer_binary_sha256=binary or 'UNKNOWN', scorer_source_sha256=source_hash or 'UNKNOWN',
                  guide_sha256=recorded_guide or 'UNKNOWN', input_sha256=recorded_inputs or 'UNKNOWN',
                  report_path=str(path / filename) if report else None,
                  runtime_seconds=_first(report.get('runtime_seconds') if scorer in REFERENCES else inv.get('evaluator_runtime_seconds'), inv.get('runtime_seconds'), inv.get('total_runtime_seconds')),
                  peak_rss_bytes=_first(report.get('peak_rss_bytes'), inv.get('peak_memory_bytes')),
                  report=report, report_directory=str(path))
    for name, file in [('report', filename), ('invocation', 'invocation.json'), ('log', 'evaluator.log')]:
        if (path / file).is_file():
            result['files'][name] = str(path / file)
    for name, value in [('Guide', recorded_guide), ('Input', recorded_inputs), ('Scorer binary', binary), ('Scorer source', source_hash if scorer in REFERENCES else binary)]:
        if not value:
            result['identity_warnings'].append(name + ' identity UNKNOWN')
    if scorer in REFERENCES:
        status = report.get('static_legality') if inv.get('status') == 'COMPLETED' else 'ANALYSIS_ERROR' if inv.get('status') == 'ERROR' else 'INCOMPLETE'
        if status not in ('REFERENCE_VALID', 'REFERENCE_INVALID', 'ANALYSIS_ERROR', 'INCOMPLETE'):
            status = 'ANALYSIS_ERROR'
        counts = report.get('error_counts')
        result.update(status=status, by_layer=report.get('by_layer', {}), errors=report.get('errors', []), error_counts=counts or {})
        result['metrics'] = dict(wirelength_um=report.get('wirelength_dbu') / report['dbu_per_micron'] if _number(report.get('wirelength_dbu')) is not None and _number(report.get('dbu_per_micron')) and report['dbu_per_micron'] > 0 else None,
                                 overflow=report.get('overflow'), congestion_potential=report.get('normalized_overflow_squared'),
                                 thermal_correlation=report.get('thermal_correlation'), tsv_count=report.get('tsv_count'),
                                 tsv_conflicts=counts.get('TSV_PIN_OBS', 0) if isinstance(counts, dict) else None,
                                 guide_errors=sum(v for k, v in counts.items() if k.startswith('GUIDE_')) if isinstance(counts, dict) else None,
                                 connectivity_errors=sum(v for k, v in counts.items() if k in ('NET_CONNECTIVITY', 'PIN_ACCESS', 'MISSING_NET', 'DIE_OWNERSHIP', 'UNIQUE_TSV', 'INTRA_DIE_TSV', 'TSV_DECLARATION')) if isinstance(counts, dict) else None,
                                 wirelength_score=None, congestion_score=None, thermal_score=None, final_total_score=None)
    else:
        status = inv.get('official_status', inv.get('status'))
        if inv.get('status') == 'PASS' and report.get('status') == 'PASS':
            status = 'PASS'
        elif inv.get('failure_category') == 'OFFICIAL_FAIL' or inv.get('status') == 'FAIL' and report.get('status') == 'FAIL':
            status = 'FAIL'
        elif inv.get('status') == 'ERROR' or not report:
            status = 'ANALYSIS_ERROR'
        else:
            status = 'UNKNOWN'
        scores = report.get('score', {})
        scores = scores if isinstance(scores, dict) else {}
        values = [_number(scores.get(k)) for k in ('wirelength_score', 'congestion_score', 'thermal_score')]
        w = report.get('wirelength', {})
        dbu = report.get('grid', {}).get('dbu_per_micron')
        result.update(status=status)
        result['metrics'] = dict(wirelength_um=_first(w.get('total_um'), w.get('total_dbu') / dbu if _number(w.get('total_dbu')) is not None and _number(dbu) and dbu > 0 else None),
                                 wirelength_dbu=w.get('total_dbu'), overflow=report.get('congestion', {}).get('total_overflow'),
                                 congestion_potential=None, thermal_correlation=report.get('thermal_tsv', {}).get('correlation_above_ambient'),
                                 wirelength_score=values[0], congestion_score=values[1], thermal_score=values[2],
                                 auxiliary_quality=sum(v * weight for v, weight in zip(values, (.45, .30, .25))) if status == 'PASS' and all(v is not None for v in values) else None,
                                 case_score=_first(*[_number(container.get(k)) for container in (report, scores) for k in ('case_score', 'total_score', 'overall_score', 'final_score')]),
                                 final_total_score=_number(report.get('competition_total_score')))
    result['note'] = inv.get('error') or inv.get('failure_reason') or ''
    if result['status'] not in ('REFERENCE_VALID', 'REFERENCE_INVALID', 'PASS', 'FAIL'):
        # Partial files are useful evidence, never a completed set of metrics.
        result.update(metrics={}, by_layer={}, errors=[], error_counts={})
        result['note'] = result['note'] or '评分调用或报告不完整；原文件仅供诊断，不填造检查通过或零错误。'
    return result


def comparison(a, b):
    if b is None:
        return dict(compatible=False, reasons=['未选择 B'], diagnostic_only=False)
    reasons = []
    for key in ('scorer_id', 'ruleset', 'scorer_source_sha256' if a['scorer_id'].startswith('REFERENCE_') else 'scorer_binary_sha256', 'input_sha256'):
        av, bv = a.get(key), b.get(key)
        if av in (None, 'UNKNOWN') or bv in (None, 'UNKNOWN') or av != bv:
            reasons.append(key + ' 不一致或 UNKNOWN，不可直接比较')
    for record in (a, b):
        if record['status'] not in ('REFERENCE_VALID', 'REFERENCE_INVALID', 'PASS', 'FAIL'):
            reasons.append(record['label'] + ' 未完成对应评分')
    if a.get('model_parameters',{})!=b.get('model_parameters',{}):reasons.append('Reference model parameters differ')
    invalid = any(x['status'] in ('REFERENCE_INVALID', 'FAIL') for x in (a, b))
    return dict(compatible=not reasons, reasons=reasons, diagnostic_only=invalid)


def write_index(record, project=None, attempt=None):
    """Small immutable index; authoritative reports stay where they were generated."""
    _, base, _ = roots(project)
    scorer = next(k for k, v in SCORERS.items() if v == record['scorer_id'])
    if scorer == OFFICIAL:
        folder = base / OFFICIAL / record['case'] / record['label']
        target = (folder / 'attempts' / safe_label(attempt) if attempt else folder) / 'index.json'
    else:
        target = base / 'indexes' / scorer / record['case'] / (record['label'] + '.json')
    fields = ('case', 'label', 'scorer_id', 'scorer_source_sha256', 'scorer_binary_sha256',
              'guide_sha256', 'input_sha256', 'router_sha256', 'config_sha256', 'status',
              'official', 'report_path', 'runtime_seconds', 'peak_rss_bytes')
    data = {key: record[key] for key in fields}
    if target.exists():
        if read_json(target) != data:
            raise ScoringConflict('Existing scoring index differs; preserve it: ' + str(target))
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x', encoding='utf8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
    return target
