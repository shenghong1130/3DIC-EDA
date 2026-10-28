"""Observed comparisons only; no guessed total or fabricated optimization benefit."""
from pathlib import Path
import argparse
import collections
import csv
import io
import itertools
import json
import math
from support import (HERE, load, atomic, atomic_bytes, immutable, verify_files, sha256,
                     object_hash, utc, locked, TuningError)
from records import collect
from plotting import make_figures

DELTAS=['total_score','wirelength_score','congestion_score','thermal_score',
        'wirelength_dbu','official_overflow','pearson','router_runtime_seconds','router_peak_rss_bytes']
OBJECTIVES={'total_score':1,'wirelength_score':1,'congestion_score':1,'thermal_score':1,
            'router_runtime_seconds':-1,'router_peak_rss_bytes':-1}

def display(value):
    if value is None:
        return 'N/A'
    if isinstance(value,float):
        return f'{value:.6g}'
    return str(value).replace('|','\\|').replace('\n',' ')

def compare(rows):
    baseline=next((r for r in rows if r['id']=='baseline'),None)
    for row in rows:
        row['same_guide_as_baseline']=None
        row['same_tsv_mapping_as_baseline']=None
        row['same_tsv_density_as_baseline']=None
        for key in DELTAS:
            row['delta_'+key]=row['percent_'+key]=None
            if baseline and baseline['eligible'] and row['eligible']:
                a=baseline.get(key);b=row.get(key)
                if a is not None and b is not None:
                    row['delta_'+key]=b-a
                    if a!=0:
                        row['percent_'+key]=(b-a)/abs(a)*100
        if baseline and row['router_status']==baseline['router_status']=='ROUTED':
            for output,key in [('same_guide_as_baseline','guide_sha256'),('same_tsv_mapping_as_baseline','tsv_mapping_sha256'),('same_tsv_density_as_baseline','tsv_density_sha256')]:
                if row.get(key) is not None and baseline.get(key) is not None:
                    row[output]=row[key]==baseline[key]
    return baseline

def pareto(rows):
    dimensions={k:v for k,v in OBJECTIVES.items() if rows and all(r.get(k) is not None for r in rows)}
    if not dimensions:
        return [],[]
    def dominates(a,b):
        return (all(sign*a[k]>=sign*b[k] for k,sign in dimensions.items())
                and any(sign*a[k]>sign*b[k] for k,sign in dimensions.items()))
    return [r['id'] for r in rows if not any(dominates(o,r) for o in rows if o is not r)],list(dimensions)

def sensitivity(rows):
    observed=[r for r in rows if r['eligible'] and len(r['changes'])==1]
    effects={}
    for metric in DELTAS:
        values=[r for r in observed if r.get('delta_'+metric) is not None]
        maximum=max((abs(r['delta_'+metric]) for r in values),default=None)
        winners=[r for r in values if maximum is not None and math.isclose(abs(r['delta_'+metric]),maximum,rel_tol=1e-9,abs_tol=1e-12)]
        effects[metric]={'max_absolute_delta':maximum,'experiments':[r['id'] for r in winners],
                         'parameters':sorted({next(iter(r['changes'])) for r in winners}),
                         'zero_effect_observed':maximum==0 if maximum is not None else None}
    recommendations=[]
    parameters={}
    for r in observed:
        parameter=next(iter(r['changes']))
        changes=[abs(r.get('percent_'+k) or 0) for k in ['wirelength_score','congestion_score','thermal_score','router_runtime_seconds','router_peak_rss_bytes']]
        if any(v>1e-9 for v in changes) or r.get('same_guide_as_baseline') is False:
            current=parameters.get(parameter)
            if current is None or max(changes)>current[0]:
                parameters[parameter]=(max(changes),r)
    ordered=sorted(parameters,key=lambda p:(-parameters[p][0],p))
    for a,b in itertools.combinations(ordered,2):
        ra=parameters[a][1];rb=parameters[b][1]
        recommendations.append({'parameters':[a,b],'changes':{**ra['changes'],**rb['changes']},
            'evidence':[ra['id'],rb['id']],
            'reason':'Both parameters showed an observed metric/Guide effect; test interaction under the same budget. No combined benefit has been measured.'})
        if len(recommendations)==5:
            break
    return effects,recommendations

def csv_bytes(rows, fields=None):
    fields=fields or list(dict.fromkeys(k for row in rows for k in row)) or ['id','state','official_status']
    out=io.StringIO(newline='');writer=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore');writer.writeheader()
    for row in rows:
        writer.writerow({k:'N/A' if row.get(k) is None else json.dumps(row[k],ensure_ascii=False,sort_keys=True) if isinstance(row[k],(dict,list)) else row[k] for k in fields})
    return out.getvalue().encode('utf-8-sig')

def algorithm_observations(rows):
    """Evidence-linked research priorities, never an inferred unmeasured speedup."""
    notes=[]
    observed=[r for r in rows if r['eligible'] and r['id']!='baseline']
    for row in observed:
        name=row['id'];changes=row['changes']
        if 'iterations' in changes and row.get('iterations_executed') is not None:
            if row['iterations_accepted']==0:
                notes.append(f"{name}：实际优化 {row['iterations_executed']} 轮、接受 0 轮；优先核查重布接受/回滚准则及不可消除固定需求，尚不能断言增加轮数有效。")
        if 'thermal' in changes:
            if row.get('same_tsv_mapping_as_baseline') is True:
                notes.append(f"{name}：TSV Net→Node 映射未变；先统计固定锚点比例并检查候选成本有效范围，再决定是否修改 TSV 搜索。")
            elif row.get('same_tsv_mapping_as_baseline') is False:
                notes.append(f"{name}：TSV 映射变化，官方热分差为 {display(row.get('delta_thermal_score'))}；建议复验热候选选点与拥塞之间的取舍，不把位置变化本身视作改善。")
        if 'margin' in changes and row.get('same_guide_as_baseline') is True and (row.get('delta_router_runtime_seconds') or 0)>0:
            notes.append(f"{name}：Guide 未变、Router wall 增加 {display(row['delta_router_runtime_seconds'])} 秒；优先剖析 A* 搜索区域与扩展开销，并重复测量排除负载噪声。")
        if 'via' in changes and row.get('same_guide_as_baseline') is False:
            notes.append(f"{name}：via 调整改变 Guide；该参数同时作用 MST 与 A*，下一阶段先分别记录拓扑/路径变化，不能用本实验独自归因给其中一个算法。")
    for row in rows:
        if 'A* expansion limit' in (row.get('failure_reason') or ''):
            notes.append(f"{row['id']}：记录了 A* 扩展上限错误；属于不可完成方案，先研究可达性、搜索区域及扩展量，不作为合法低分候选。")
    phases={'PARSE_LEF','PARSE_DEF','GRAPH_BUILD','THERMAL','FIXED_DEMAND','ROUTING','WRITE_RESULT','WRITE_GUIDE'}
    totals=collections.defaultdict(list)
    for row in observed:
        for phase,value in row.get('stage_seconds',{}).items():
            if phase in phases:
                totals[phase].append(value)
    if totals:
        mean={k:sum(v)/len(v) for k,v in totals.items()};largest=max(mean,key=mean.get)
        notes.append(f"可比较已观测变体中，已记录的独立主要阶段平均耗时最大为 {largest}（{display(mean[largest])} 秒）；先剖析该阶段。嵌套/抽样 MST、A* 阶段不用于相加或推断完整占比。")
    return notes or ['尚无足够的真实比较或故障证据，不能指定主要算法瓶颈；先完成第一轮并核对阶段耗时。']

def generate(plan,state,report,plots=True):
    report=Path(report)
    problems=verify_files(plan)
    rows=[collect(plan,r,state['experiments'][r['id']],report) for r in plan['variants']]
    problems+=['Evidence '+r['id']+': '+str(r['integrity_errors']) for r in rows if r['integrity_errors']]
    if state.get('plan_sha256')!=object_hash(plan):
        problems.append('State/plan hash mismatch')
    attestation=report/'thermal_source_attestation.json'
    trusted=(plan['case']!='open_3d_case005' or attestation.is_file() and
        load(attestation).get('thermal_sha256')==plan['fingerprints']['input_sha256']['thermal'])
    if not trusted:
        problems.append('case005 Thermal source not attested; no trusted ranking')
    comparable=not problems
    for row in rows:
        row['batch_comparable']=comparable
        row['eligible']=row['eligible'] and comparable
    baseline=compare(rows)
    eligible=[r for r in rows if r['eligible']]
    frontier,dimensions=pareto(eligible)
    all_totals=bool(eligible) and all(r['total_score'] is not None for r in eligible)
    order=sorted(eligible,key=lambda r:(-r['total_score'] if all_totals else 0,
        r['router_runtime_seconds'] if r['router_runtime_seconds'] is not None else math.inf,
        r['router_peak_rss_bytes'] if r['router_peak_rss_bytes'] is not None else math.inf,r['id']))
    ranking=[{'rank':i+1 if all_totals else None,'id':r['id'],'label':r['label'],
        'basis':'explicit official total, then runtime/RSS' if all_totals else 'No total: Pareto candidates; row order is not a composite rank',
        'pareto':r['id'] in frontier,**{k:r.get(k) for k in OBJECTIVES}} for i,r in enumerate(order)]
    leaders={}
    for metric,direction in OBJECTIVES.items():
        valid=[r for r in eligible if r.get(metric) is not None]
        if valid:
            optimum=(max if direction==1 else min)(r[metric] for r in valid)
            leaders[metric]={'value':optimum,'ids':[r['id'] for r in valid if r[metric]==optimum]}
        else:
            leaders[metric]=None
    effects,recommendations=sensitivity(rows)
    next_stage=algorithm_observations(rows)
    payload={'schema_version':1,'generated_at':utc(),'experiment':plan['experiment'],'case':plan['case'],
        'analysis_source_sha256':{p.name:sha256(p) for p in sorted(HERE.glob('*.py'))},
        'plan_sha256':object_hash(plan),'comparable':comparable,'problems':problems,'rows':rows,
        'leaders':leaders,'pareto_ids':frontier,'pareto_dimensions':dimensions,
        'sensitivity':effects,'second_round_recommendations':recommendations,
        'next_stage_observations':next_stage,
        'total_policy':'Report fields only; weights alone are never interpreted as an official total.'}
    atomic(report/'results.json',payload);atomic_bytes(report/'results.csv',csv_bytes(rows))
    atomic_bytes(report/'ranking.csv',csv_bytes(ranking,['rank','id','label','basis','pareto',*OBJECTIVES]))
    atomic(report/'second_round_recommendations.json',recommendations)
    best=(order[0] if all_totals and len(eligible)>=2 and baseline and baseline['eligible'] else None)
    if best:
        atomic(report/'best_config.json',best['parameters'])
        atomic(report/'best_config_provenance.json',{'id':best['id'],'label':best['label'],'case':plan['case'],
            'total_score':best['total_score'],'selection':'Best among completed comparable PASS observations only',
            'config_sha256':best['config_sha256'],'guide_sha256':best['guide_sha256'],'plan_sha256':object_hash(plan)})
    elif (report/'best_config.json').exists():
        # Preserve an old recommendation, but stop advertising it as current.
        archive=report/'superseded'/utc().replace(':','-');archive.mkdir(parents=True,exist_ok=False)
        for name in ['best_config.json','best_config_provenance.json']:
            if (report/name).exists():
                (report/name).rename(archive/name)
    figures=make_figures(rows,report,comparable) if plots else []
    counts=collections.Counter(r['state'] for r in rows)
    lines=['# 阶段一参数实验分析', '', '## 1. 实验概览', '',
        f"- 实验：`{plan['experiment']}`；Case：`{plan['case']}`；状态：`{state['batch_state']}`。",
        f"- Router SHA256：`{plan['fingerprints']['router_sha256']}`。",
        f"- Evaluator：`{plan['engine']}`；SHA256：`{plan['fingerprints']['evaluator_sha256']}`。",
        f"- JSON time_limit：{plan['baseline']['time_limit']} 秒；生效 CLI Router 预算：{plan['router_timeout']} 秒；评分预算：{plan['evaluation_timeout']} 秒。",
        f"- 后端：{plan['backend']}；监控：`{json.dumps(plan['monitoring'])}`；可比较性：{comparable}。",
        '- 输入与工具/源码/环境详见 experiment_plan.json；完整配置、命令、状态和运行记录均保留。',
        '- 官方总分只读取报告中的明确字段；当前评分器报告没有总分时显示 N/A，不使用 weights 合成。', '',
        '## 2. 参数配置', '', '| ID | 相对 Baseline 的变化 | 配置 SHA256 |', '| --- | --- | --- |']
    for r in rows:
        lines.append(f"| {r['id']} | {display(json.dumps(r['changes']) if r['changes'] else '无')} | `{r['config_sha256']}` |")
    lines+=['', '## 3. 实验完成情况', '', '| 状态 | 数量 |','| --- | ---: |']
    for status in ['PENDING','ROUTING','ROUTED','EVALUATING','COMPLETED','ROUTER_FAILED','EVALUATION_FAILED','INTERRUPTED','BLOCKED']:
        lines.append(f'| {status} | {counts[status]} |')
    lines+=['',f"可比较官方 PASS：{len(eligible)}；Router TIMEOUT：{sum(r['router_status']=='TIMEOUT' for r in rows)}；评分 TIMEOUT：{sum(r['official_status']=='TIMEOUT' for r in rows)}。",
        '', '## 4. 官方评分对比', '', '| ID | Router | 官方 | 总分 | WL Score | Congestion Score | Thermal Score |', '| --- | --- | --- | ---: | ---: | ---: | ---: |']
    for r in rows:
        lines.append('| '+' | '.join(display(r.get(k)) for k in ['id','router_status','official_status','total_score','wirelength_score','congestion_score','thermal_score'])+' |')
    lines+=['', 'ROUTED 不等于 PASS；FAIL/TIMEOUT/未评分均不进入合法候选。子分数、线长 DBU/μm、总/最大 Overflow、Pearson、TSV 及失败原因详见 results.csv。',
        '', '## 5. 运行效率对比', '', '| ID | Router wall 秒 | C++ 秒 | Peak RSS MiB | RSS 来源 | Evaluator 秒 | Evaluator MiB | 实际/接受优化轮数 |', '| --- | ---: | ---: | ---: | --- | ---: | ---: | --- |']
    for r in rows:
        lines.append('| '+' | '.join([r['id'],display(r['router_runtime_seconds']),display(r['router_cpp_runtime_seconds']),
            display(r['router_peak_rss_bytes']/1048576 if r['router_peak_rss_bytes'] is not None else None),display(r['memory_source']),
            display(r['evaluator_runtime_seconds']),display(r['evaluator_peak_rss_bytes']/1048576 if r['evaluator_peak_rss_bytes'] is not None else None),
            display(r['iterations_executed'])+'/'+display(r['iterations_accepted'])])+' |')
    lines+=['', 'Router wall 包括 WSL/监督与输出开销；C++ runtime 来自本地结果，二者不混用。GNU time 命令树峰值与按 Router PID 采样峰值分别标明来源，不能当成完全相同的测量。',
        '', '## 6. 参数敏感性分析', '', '| 指标 | 最大绝对变化 | 对应实验 | 对应参数 |', '| --- | ---: | --- | --- |']
    for metric,effect in effects.items():
        lines.append(f"| {metric} | {display(effect['max_absolute_delta'])} | {', '.join(effect['experiments']) or 'N/A'} | {', '.join(effect['parameters']) or 'N/A'} |")
    if not baseline or not baseline['eligible']:
        lines+=['', '**本批 Baseline 尚未取得完整、可比较的官方 PASS，无法形成正常改善基准。**']
    lines+=['', '绝对差异及适用百分比逐字段写入 results.csv（percent_* 单位为 %；基准为零或缺失时为 N/A）。表中最大变化是观测幅度，不等于改善；各参数量纲与步长不同，不能当作导数或泛化敏感性。',
        '', '| ID | Guide 相同 | TSV Net→Node 相同 | TSV 密度相同 |', '| --- | --- | --- | --- |']
    for r in rows:
        lines.append(f"| {r['id']} | {display(r['same_guide_as_baseline'])} | {display(r['same_tsv_mapping_as_baseline'])} | {display(r['same_tsv_density_as_baseline'])} |")
    observed=[r for r in rows if r['id']!='baseline' and r['eligible']]
    identical=[r['id'] for r in observed if r['same_guide_as_baseline'] is True]
    lines+=['', 'Guide 与 Baseline 字节相同：'+(', '.join(identical) if identical else '尚无该项观测')+'。SHA256 见 CSV；相同输出不宣称质量改善。']
    iterations=[r for r in observed if 'iterations' in r['changes']]
    thermal=[r for r in observed if 'thermal' in r['changes']]
    for group,title in [(iterations,'迭代'),(thermal,'Thermal')]:
        lines+=['', title+'实验观测：'+('; '.join(f"{r['id']}: 实际轮次={display(r['iterations_executed'])}, 接受={display(r['iterations_accepted'])}, Δ拥塞分={display(r.get('delta_congestion_score'))}, Δ热分={display(r.get('delta_thermal_score'))}, TSV密度相同={display(r['same_tsv_density_as_baseline'])}" for r in group) if group else '尚无完整比较，不得推断收益。')]
    conflicts=[r['id'] for r in observed if any((r.get('delta_'+k) or 0)>0 for k in ['wirelength_score','congestion_score','thermal_score']) and any((r.get('delta_'+k) or 0)<0 for k in ['wirelength_score','congestion_score','thermal_score'])]
    lines+=['', '分项评分方向冲突：'+(', '.join(conflicts) if conflicts else '尚未观察到或数据不足')+'。',
        '', '## 7. 最优参数', '', '最高总分、最佳三项子分数、最快合法配置（允许并列）：', '', '| 指标 | 候选 | 值 |','| --- | --- | ---: |']
    for metric,leader in leaders.items():
        lines.append(f"| {metric} | {', '.join(leader['ids']) if leader else 'N/A'} | {display(leader['value'] if leader else None)} |")
    lines+=['', 'Pareto 候选：'+(', '.join(frontier) or 'N/A')+'；共同完整的目标：'+(', '.join(dimensions) or '无')+'。',
        '', f"已保存 best_config.json：{best is not None}。"+('仅代表已观测的完整 PASS 配置中最优，尚未运行的候选不作推断。' if best else '缺少可靠总分、有效 Baseline 或足够完整的可比较 PASS；不能唯一确定综合最优。'),
        '', '## 8. 结果的限制', '', '- 单 Case、单次观测不足以保证泛化；时间/内存受同机负载与采样影响，建议后续使用重复批次核验。',
        '- 本地指标与官方指标严格分开；PASS 是本地官方工具通过，不保证正式隐藏用例。Proxy / mock-DRT 不是实际详细布线 sign-off。',
        '- 总分缺失不设为零；排名不混用引擎或版本；搜索上限/超时失败属于不可完成配置，不是合法低分。',
        '- 所有未执行候选为 PENDING；不借用其他批次旧运行作为本批实测。']
    lines+=['- 可比较性问题：'+display(p) for p in problems]
    lines+=['', '## 9. 下一阶段建议', '', '以下组合仅为待验证假设，不自动执行；建议来源于本轮有实际指标/Guide 差异的参数：']
    if recommendations:
        lines+=['- `'+json.dumps(r['changes'])+'`；证据：'+', '.join(r['evidence'])+'；需验证交互。' for r in recommendations]
    else:
        lines+=['- 当前不足以推荐有依据的参数组合；至少需要两个不同参数的完整比较，再生成最多五组推荐。']
    lines+=['','基于本批记录的算法研究优先项：',*['- '+note for note in next_stage]]
    lines+=['', '阶段二应结合 stage_seconds 与采样边界确定：若实际优化轮数增加但质量不变，检查仅按局部 Overflow 接受与回滚；若 Thermal 改动却 TSV 映射不变，检查固定锚点与候选成本；A* 扩展上限失败需分析搜索区域/可达性；没有测量不得断定 MST、A* 或 TSV 是主要瓶颈。',
        '', '源码已知作用（非本次实测）：via 同时影响 MST 拓扑、A* 的层间边与启发式；congestion 作用于 A* 和 TSV 选点；history 仅在拥塞历史非零时生效；thermal 仅参与非固定 TSV 候选比较；iterations 受 Overflow/停滞提前终止；margin 初始搜索框只在失败后扩大。cfg.tsv 对同一 Net 的各 TSV 候选为相同加数，通常不改变其排序。', '', '图表：']
    lines+=['',*['![%s](./figures/%s)'%(name,name) for name in figures]]
    atomic_bytes(report/'analysis.md',('\n'.join(lines)+'\n').encode('utf8'))
    return payload

def main():
    p=argparse.ArgumentParser(description='Regenerate reports only; never launch Router/evaluator')
    p.add_argument('--experiment',default='phase1_case001');a=p.parse_args()
    from tune_parameters import directory
    report=directory(a.experiment)
    with locked(report/'.manager.lock'):
        generate(load(report/'experiment_plan.json'),load(report/'experiment_state.json'),report)

if __name__=='__main__':
    main()
