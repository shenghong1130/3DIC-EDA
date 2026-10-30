"""Read-only historical metrics. Archived URIs are evidence identifiers, not files.

No function in this module launches/reconstructs a routing or scoring run.
"""
import json
from common import OUTPUTS

SUMMARY = 'reports/case001_history.json'


def history(root=None):
    path = (root or OUTPUTS) / SUMMARY
    data = json.loads(path.read_text(encoding='utf8'))
    if data.get('schema_version') != 1 or data.get('case') != 'open_3d_case001':
        raise ValueError('Invalid case001 history summary')
    return data


def archived_row(label, root=None):
    """Return measured metadata, explicitly without a raw-run directory."""
    rows = history(root)['records']
    if label not in rows:
        matches=[r for r in rows.values() if r.get('current_label')==label]
        if len(matches)==1:return matches[0]
        raise KeyError('No verified history for ' + label)
    return rows[label]


def print_history():
    data=history()
    print(json.dumps({'representatives':data['representatives'], 'rows':summary_rows(data)}, ensure_ascii=False, indent=2))


def summary_rows(data=None):
    """Portable measured rows; never substitute a representative's scores."""
    if data is None:
        try:data=history()
        except FileNotFoundError:return []  # Fresh checkout: no historical measurements yet.
    rows=[]
    for label, r in data['records'].items():
        inv=next((x for x in r['evaluations'] if x['engine_directory']=='official_latest'),{})
        report=inv.get('report') or {}
        row={k:v for k,v in r.items() if k not in {'files','official_reports','evaluations','source_sha256','config_sources'}}
        row.update(case=data['case'],label=label,official_status=inv.get('official_status','NOT_EVALUATED'),
                   official_report=report,evaluator_sha256=inv.get('evaluator_sha256'),
                   wirelength_dbu=report.get('wirelength',{}).get('total_dbu'),
                   official_overflow=report.get('congestion',{}).get('total_overflow'),
                   cpp_runtime_seconds=r.get('metrics',{}).get('runtime_seconds'),
                   history_summary='outputs/'+SUMMARY+'#/records/'+label,
                   raw_run_retained=r['retention'] in {'KEEP','ARCHIVED'},daily_run_retained=r['retention']=='KEEP')
        row.update({k:report.get('score',{}).get(k) for k in ['wirelength_score','congestion_score','thermal_score']})
        rows.append(row)
    return rows
