"""Read-only same-identity reference comparison; never predict platform scores."""
import argparse
import collections
import csv
import io
import json
import zipfile
from pathlib import Path

from common import PROJECT, sha256
from scoring import reference_directory


def small_metadata(path):
    with path.open('rb') as f:
        data = f.read(8 * 1024 * 1024).decode('utf8')
    prefix = '"v3_algorithm":'
    index = data.find(prefix)
    if index < 0:
        raise ValueError('No algorithm metadata in bounded result header')
    return json.JSONDecoder().raw_decode(data[index+len(prefix):].lstrip())[0]


def compare(case):
    short = case.removeprefix('open_3d_')
    directory = PROJECT/'outputs/reports/v3_6'
    versions = {}
    for version in ['v3_5', 'v3_6']:
        label = short+'_'+version
        run_dir = PROJECT/'outputs/runs'/case/label
        run = json.loads((run_dir/'run.json').read_text(encoding='utf8'))
        ref_dir = reference_directory(case, label)
        ref = json.loads((ref_dir/'reference_report.json').read_text(encoding='utf8'))
        if run['status'] != 'ROUTED' or ref['guide_sha256'] != sha256(run_dir/'route.guide'):
            raise ValueError('Run/Guide/Reference mismatch')
        versions[version] = {'run': run, 'reference': ref,
            'algorithm': small_metadata(run_dir/'result.json'), 'guide_sha256': sha256(run_dir/'route.guide'),
            'run_directory': str(run_dir), 'reference_directory': str(ref_dir)}
    old, new = versions['v3_5'], versions['v3_6']
    for key in ['reference_source_sha256', 'reference_scorer_sha256', 'input_sha256']:
        if old['reference'][key] != new['reference'][key]:
            raise ValueError('Incomparable reference identity: '+key)
    if old['run']['config'] != new['run']['config']:
        raise ValueError('Different base parameters')
    audit = json.loads((directory/'audit'/case/'summary.json').read_text(encoding='utf8'))
    original_points = {}
    baseline = PROJECT/'outputs/reports/official_platform/v3_5_24699'
    with zipfile.ZipFile(baseline/'scoring_result.zip') as z, z.open('eval_temp/'+case+'/tsv_points.csv') as f:
        for p in csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig')):
            original_points[p['net']] = int(p['gcell_col']), int(p['gcell_row'])
    new_points = {}
    with (directory/'audit'/case/'tsv_candidates.csv').open(encoding='utf8',newline='') as f:
        for p in csv.DictReader(f):
            if p['net'] in new_points:
                raise ValueError('Ambiguous TSV candidates: '+p['net'])
            new_points[p['net']] = int(p['col']), int(p['row'])
    if original_points.keys() != new_points.keys():
        raise ValueError('Different TSV Net set')
    def distribution(points):
        count = collections.Counter(points.values())
        return {'count': len(points), 'unique_gcells': len(count), 'colocated_gcells': sum(v>1 for v in count.values()),
                'excess_colocated_tsvs': len(points)-len(count), 'max_per_gcell': max(count.values(), default=0)}
    old['distribution'], new['distribution'] = distribution(original_points), distribution(new_points)
    moved = sum(original_points[k] != v for k, v in new_points.items())
    a, b = old['reference'], new['reference']
    policy = json.loads((directory/'gate_policy.json').read_text(encoding='utf8'))
    gates = {'reference_valid': b['static_legality']=='REFERENCE_VALID',
        'center_tsv_geometry': not audit['rule_matrix_conflicting_candidates']['A_D'],
        'unique_tsv': not audit['counts'].get('ambiguous_nets',0) and not audit['counts'].get('missing_tsv_nets',0),
        'wire_guard': b['wirelength_dbu'] <= a['wirelength_dbu']*(1+policy['max_reference_wirelength_growth_fraction']),
        'overflow_guard': b['overflow'] <= a['overflow'],
        'potential_guard': b['normalized_overflow_squared'] <= a['normalized_overflow_squared']+policy['max_reference_potential_growth_tolerance'],
        'thermal_guard': b['thermal_correlation'] >= a['thermal_correlation']-policy['max_reference_thermal_correlation_loss_vs_v35']}
    result = {'case': case, 'versions': versions, 'audit_classification': audit['classification'],
        'tsv_sites_changed_vs_v35': moved, 'case003_quality_gate': gates, 'quality_gate_passed': all(gates.values()),
        'official_v36_status': 'NOT_EVALUATED', 'official_v36_score': None}
    with (directory/(short+'_comparison.json')).open('x',encoding='utf8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    rows = [('参考合法性', a['static_legality'], b['static_legality']),
        ('参考 Wirelength (um)', a['wirelength_dbu']/a['dbu_per_micron'], b['wirelength_dbu']/b['dbu_per_micron']),
        ('参考 Overflow', a['overflow'], b['overflow']),
        ('参考 Congestion Potential', a['normalized_overflow_squared'], b['normalized_overflow_squared']),
        *[(f'M{l} 参考 Overflow', a['by_layer'][str(l)]['overflow'], b['by_layer'][str(l)]['overflow']) for l in [1,10,11]],
        ('参考 Thermal Correlation', a['thermal_correlation'], b['thermal_correlation']),
        ('TSV 共址格', old['distribution']['colocated_gcells'], new['distribution']['colocated_gcells']),
        ('超出每格首个的 TSV 数', old['distribution']['excess_colocated_tsvs'], new['distribution']['excess_colocated_tsvs']),
        ('单格最多 TSV', old['distribution']['max_per_gcell'], new['distribution']['max_per_gcell']),
        ('Router Runtime (s)', old['run']['runtime_seconds'], new['run']['runtime_seconds']),
        ('Router Peak RSS (MiB)', old['run']['peak_memory_bytes']/1024**2, new['run']['peak_memory_bytes']/1024**2)]
    with (directory/(short+'_comparison.md')).open('x',encoding='utf8') as f:
        f.write(f'# {case}: V3.5 / V3.6\n\n相同输入、相同基础参数及同一参考评分器源码/二进制 SHA256。下表不是官方成绩。\n\n| 指标 | V3.5 | V3.6 |\n|---|---:|---:|\n')
        for key,x,y in rows: f.write(f'| {key} | {x} | {y} |\n')
        f.write(f'\n相对 V3.5 最终 Guide 改变 {moved} 个 Net 的 TSV 格；这与 V3.6 自身优化阶段 moved 计数不同。\n\n门槛结果：`{gates}`；质量门槛通过：{all(gates.values())}。运行资源另行检查后才能继续大型 Case。\n\n')
        f.write(f'独立 TSV 审计：{audit["classification"]}。A+D 中心正面积冲突为零不代表消除全部外框/边界模型风险；本轮保留已通过平台的 V3.5 策略。参考评分还独立检查完整连通性。\n\n')
        f.write('V3.6 新全局优化统计：\n\n```json\n'+json.dumps(new['algorithm'].get('global_tsv'),ensure_ascii=False,indent=2)+'\n```\n')
    print(json.dumps({'case':case,'quality_gate':gates,'passed':all(gates.values()),'v35_overflow':a['overflow'],
        'v36_overflow':b['overflow'],'v35_distribution':old['distribution'],'v36_distribution':new['distribution']},ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--case',required=True)
    compare(parser.parse_args().case)
