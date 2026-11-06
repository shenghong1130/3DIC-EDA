"""Evidence-only V4.5 reports. Does not run routing, scoring or Git operations."""
import bisect
import collections
import json
from pathlib import Path
from common import PROJECT, sha256

ROOT=PROJECT/'outputs/reports/v4_5'
PEER=PROJECT/'outputs/reports/v4_4_peer_analysis'


def read(p):
    return json.loads(p.read_text(encoding='utf8')) if p.is_file() else {}


def row(folder, name):
    d=read(folder/'result.json');summary=read(folder/'summary.json')
    if not d:return {'variant':name,'status':summary.get('router_status','NOT_RUN')}
    nx=len(d['x_lines'])-1;ny=len(d['y_lines'])-1
    layers=collections.defaultdict(lambda:dict(overflow=0,phi=0,fixed_overflow=0,routing_demand=0,zero_capacity_demand=0))
    for v,c,u,f,_ in d['resources']:
        r=layers[int(v)//(nx*ny)];o=max(0,f+u-c)
        r['overflow']+=o;r['phi']+=(o/c)**2 if c>0 else 0;r['fixed_overflow']+=max(0,f-c);r['routing_demand']+=u;r['zero_capacity_demand']+=f+u if c<=0 else 0
    footprints=collections.defaultdict(list);centers=collections.Counter()
    for net in d['nets']:
        if not net['cross_die']:continue
        v=int(net['tsv_node']);centers[v]+=1;x=v%nx;y=(v//nx)%ny
        cx=(d['x_lines'][x]+d['x_lines'][x+1])/2;cy=(d['y_lines'][y]+d['y_lines'][y+1])/2;r=2.5*d['dbu_per_micron']
        for yy in range(max(0,bisect.bisect_right(d['y_lines'],cy-r)-1),min(ny,bisect.bisect_left(d['y_lines'],cy+r))):
            for xx in range(max(0,bisect.bisect_right(d['x_lines'],cx-r)-1),min(nx,bisect.bisect_left(d['x_lines'],cx+r))):footprints[xx,yy].append(net['net'])
    execution=read(folder/'execution.json');audit=read(folder/'tsv_audit/summary.json')
    a=d['v3_algorithm'].get('v4_5',{})
    wire=summary.get('audit',{}).get('wire_proxy_measured_dbu')
    if wire is None:wire=a.get('after',{}).get('wire_dbu_proxy')
    ref=read(folder/'reference/reference_report.json')
    return {'variant':name,'path':folder.relative_to(PROJECT).as_posix(),'status':'ROUTED' if execution.get('returncode')==0 else 'INCOMPLETE',
        'nets':len(d['nets']),'guide_sha256':sha256(folder/'route.guide') if (folder/'route.guide').is_file() else None,
        'overflow_local':sum(r['overflow'] for r in layers.values()),'phi_local':sum(r['phi'] for r in layers.values()),'by_layer':dict(layers),
        'wire_guide_proxy_um':wire/d['dbu_per_micron'] if wire is not None else None,'thermal_clipped_grid':d['metrics'].get('thermal_correlation_local'),
        'thermal_raw_grid':a.get('after',{}).get('thermal_correlation'),'runtime_seconds':d['metrics'].get('runtime_seconds'),
        'windows_peak_working_set_bytes':execution.get('windows_peak_working_set_bytes'),
        'center_colocated_pairs':sum(n*(n-1)//2 for n in centers.values()),'footprint_shared_xy_cells':sum(len(v)>1 for v in footprints.values()),
        'footprint_pair_xy_incidences':sum(len(v)*(len(v)-1)//2 for v in footprints.values()),
        'independent_tsv':audit.get('classification','NOT_RUN'),'independent_tsv_counts':audit.get('counts'),
        'v45':a,'reference':ref,'identity':read(folder/'identity.json')}


def fmt(x):
    if x is None:return 'N/A'
    return f'{x:.6f}' if isinstance(x,float) else str(x)


def formal_rows(records):
    """Stream large results one resource/Net at a time; never load the Net array."""
    import sys
    sys.path.insert(0,str(PROJECT/'ui'))
    from result_store import Cursor
    rows=[]
    for case,record in sorted(records.items()):
        run=record.get('run',{});folder=Path(record.get('path',record.get('planned_path','')))
        entry={'variant':case,'label':record.get('label'),'status':record['status'],
            'reference_status':'NOT_EVALUATED','official_status':'NOT_EVALUATED',
            'reference_gate':read(ROOT/'reference_preflight.json'),
            'resource_gate':record.get('gate',run.get('resource_gate'))}
        if run.get('status')!='ROUTED':rows.append(entry);continue
        root={};layers=collections.defaultdict(lambda:dict(overflow=0,phi=0,fixed_overflow=0,routing_demand=0,zero_capacity_demand=0))
        footprints=collections.Counter();centers=collections.Counter();count=0
        reader=Cursor(folder/'result.json')
        try:
            reader.take('{')
            while reader.char()!='}':
                key=reader.value()[0];reader.take(':')
                if key in ('dbu_per_micron','x_lines','y_lines','metrics'):root[key]=reader.value()[0]
                elif key in ('resources','nets'):
                    nx=len(root['x_lines'])-1;ny=len(root['y_lines'])-1
                    for item,_,_ in reader.array():
                        if key=='resources':
                            v,c,u,f,_=item;r=layers[int(v)//(nx*ny)];o=max(0,f+u-c)
                            r['overflow']+=o;r['phi']+=(o/c)**2 if c>0 else 0
                            r['fixed_overflow']+=max(0,f-c);r['routing_demand']+=u;r['zero_capacity_demand']+=f+u if c<=0 else 0
                        else:
                            count+=1
                            if not item['cross_die']:continue
                            v=int(item['tsv_node']);centers[v]+=1;x=v%nx;y=(v//nx)%ny
                            cx=(root['x_lines'][x]+root['x_lines'][x+1])/2;cy=(root['y_lines'][y]+root['y_lines'][y+1])/2;r=2.5*root['dbu_per_micron']
                            for yy in range(max(0,bisect.bisect_right(root['y_lines'],cy-r)-1),min(ny,bisect.bisect_left(root['y_lines'],cy+r))):
                                for xx in range(max(0,bisect.bisect_right(root['x_lines'],cx-r)-1),min(nx,bisect.bisect_left(root['x_lines'],cx+r))):footprints[xx,yy]+=1
                else:reader.skip()
                if reader.char()=='}':break
                reader.take(',')
            reader.take('}')
        finally:reader.close()
        audit=read(folder/'tsv_audit/summary.json');a=run['algorithm']['v4_5']
        entry.update(path=folder.relative_to(PROJECT).as_posix(),nets=count,guide_sha256=run['guide_sha256'],
            router_sha256=run['router_sha256'],config_sha256=run['config_sha256'],input_sha256=run['input_sha256'],
            source_sha256=run['source_sha256'],by_layer=dict(layers),overflow_local=sum(r['overflow'] for r in layers.values()),
            phi_local=sum(r['phi'] for r in layers.values()),wire_guide_proxy_um=run['output_audit']['wire_proxy_measured_dbu']/root['dbu_per_micron'],
            thermal_raw_grid=a['after']['thermal_correlation'],thermal_clipped_grid=root['metrics'].get('thermal_correlation_local'),
            runtime_seconds=run['runtime_seconds'],windows_peak_working_set_bytes=run['peak_memory_bytes'],
            center_colocated_pairs=sum(n*(n-1)//2 for n in centers.values()),footprint_shared_xy_cells=sum(n>1 for n in footprints.values()),
            footprint_pair_xy_incidences=sum(n*(n-1)//2 for n in footprints.values()),
            independent_tsv=audit.get('classification','PENDING'),independent_tsv_counts=audit.get('counts'),v45=a,
            guide_complete=count==run['output_audit']['nets'] and run['output_audit']['unmeasured_nets']==0,
            guide_hash_verified=sha256(folder/'route.guide')==run['guide_sha256'],reader_max_buffer_chars=reader.maximum)
        rows.append(entry)
    return rows


def table(rows):
    fields=['variant','status','overflow_local','phi_local','wire_guide_proxy_um','thermal_raw_grid','thermal_clipped_grid','runtime_seconds','windows_peak_working_set_bytes','independent_tsv']
    return '| '+ ' | '.join(fields)+' |\n|'+ '|'.join(['---']*len(fields))+'|\n'+'\n'.join('| '+' | '.join(fmt(r.get(k)) for k in fields)+' |' for r in rows)+'\n'


def main():
    rows=[]
    for name,folder in [('V44',PEER/'experiments/open_3d_case001/baseline'),('Exp01',PEER/'experiments/open_3d_case001/initial_tsv')]:rows.append(row(folder,name))
    for folder in sorted((ROOT/'experiments/open_3d_case001').glob('*')):
        if (folder/'result.json').is_file():rows.append(row(folder,folder.name))
    disabled=next((r for r in rows if r['variant']=='disabled'),{})
    if disabled.get('guide_sha256')==rows[0].get('guide_sha256'):rows[0]['thermal_raw_grid']=disabled.get('thermal_raw_grid')
    by={r['variant']:r for r in rows};full=by.get('combined',{})
    formal=formal_rows(read(ROOT/'formal_validation.json'))
    data={'scope':'Windows native O1, Router Local Proxy; reference entries separately identified; not official score','rows':rows,'formal_rows':formal}
    (ROOT/'results.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    sources=read(ROOT/'baseline_identity.json');builds=[p for p in (ROOT/'builds').glob('*/complete.json')]
    docs={}
    docs['baseline_diagnosis.md']='''# V4.4 / Exp01 诊断

依据：原生回执、result.json、Guide、源码快照。不是官方成绩。

Exp01 改变 504/657 个最终 TSV 中心，但只预估平均 Pin 距离、冷点代价和计划 footprint；没有两侧完整 Route 的验收，也不维护全局 Pearson。最终路径进入的 GCell、Via、Steiner 分支不包含在初始目标中。坐标下降降低代理目标不保证完整资源目标下降。

V44 → Exp01：O 7735→7869，正容量 Phi 271.929680→261.460398，Guide 线长代理 405531→407964 µm，裁剪网格热相关性 .035417→.030954。Phi 与 O 不是同一个目标，不能把下降 3.85% 的 Phi 写成全指标改善。

中心共址均为 0，但正面积 5µm footprint 共享 XY 格为 306→331，格内 Net 对关联数为 314→331（每个 XY 格计一次，不重复计 M10/M11）。这只表明几何资源重叠，不直接证明非法或精确 Track demand 相同。边界接触不算正面积重叠。

186 个跨 Die Net 的 Pin 数大于 32，原 V44 完整 TSV 重建直接排除它们。其他硬约束包括 4096 节点、128 格窗口、24K 搜索展开、0.4s/候选；不能靠增大重试次数解决不可移动 Pin/TSV/分支点。

根因已确认：代理目标遗漏接入路径、热模型不统一、完整 TSV 工作空间限制。单 Net 对最终回退的因果贡献仍无法仅由最终两张图准确分离；逐 Net 资源归因会受其他 Net 占用和排序影响，不把差分相关性伪装成反事实收益。

V44 严格 O/Phi 双保护确实会拒绝 O 小增而 Phi 大降的解；是否值得接受必须实测，不能先声称新策略更优。`baseline_diagnosis.json` 给出每层明细、固定需求和零容量需求。
'''
    docs['v45_algorithm_design.md']='''# V4.5 算法设计（EXPERIMENTAL）

在不修改容量、Pin/OBS、TSV 合法性、评分器的前提下增加四个可独立关闭模块。

1. 初始全局提案：每 Net 至多 16 个合法候选，真实 5µm M10/M11 footprint；有界双 Die 接入走廊采样，取实际 Pin 层及邻近两层的容量压力。复用独立稀疏坐标下降/成对交换内核。最多 2M footprint 项、30s。代理只负责排序。
2. 完整路径预览：先保留原始完整 Route，再对 Top-K 候选重建 Pin access、Steiner、多层 A*、Via 和完整 footprint。比较相同已布线背景下的真实边际 O/Phi，初始预览不允许 Guide 线长代理增加；未通过就保留旧 Route。全局最多 512 次、30s，每次 0.25s/24K 展开。尚未布线 Net 的普通路径仍未知，最终全局改善不能由此保证。
3. 精确热门槛：维护原始热网格上密度的充分统计量。批量更新先合并每个格的净变化 k，再算 ΔΣd²=Σ(2dk+k²)、ΔΣhd=Σhk；重复源/目标与交换正确计入交叉项。热场按既有 V3 全局投影构建，未改变参考评分器。
4. 末轮联合修复：从 V44 完整成品出发，按真实边际 Phi 排序，稀疏资源→Net 反向索引，优先修复可移动 degree-2 分支；跨层 maze、受限完整 Steiner 重建、双 Net 联合交换及高 Pin 的短 TSV tail 修复。每轮 512 次、最多 1024 排序 Net、100K owner 项；失败两轮冷却。

高 Pin 不是无限全树 A*：超过 64 Pin/4096 节点只尝试保留原树的 TSV 短尾；无法安全截断则拒绝。超过 10000 节点暂不优化，但初始合法路径不丢弃。

复杂度：候选输入 O(NK F)，K≤16，F 为 footprint 格数；内核坐标下降固定轮数、成对交换有界。预览 O(B E log E)，B≤512，E≤24000/搜索；非全 Net×全格 A*。末轮 owner 索引空间≤100K，回滚保存最多 1024 个有界旧 Route，不复制全图。
'''
    docs['v45_implementation.md']='''# 实现与质量门槛

新增 `GR_V4_5`、CMake `router_v4_5`、`configs/v4_5.json`、`router_v4_5_impl.hpp`、`v45_quality_guard.hpp`；入口仅条件编译接入初始规划与末轮修复。旧 V44/Exp01 文件不改。默认根目录 Router 目标不改。

Φ=Σ(C>0)[max(0,F+U−C)/C]²；C=0 不当正容量，不允许新增非法路径，完整审计仍包括零容量需求。A* 复用非负 V44 边价格，几何启发下界未更改。

| mode | 接受条件 |
|---|---|
|0 STRICT_PARETO|Phi 与 O 均不增，至少一项严格改善|
|1 PHI_FIRST_WITH_BOUNDED_OVERFLOW|Phi 严格下降；相对阶段起点累计 O 增量≤min(64,floor(.005×O₀))；累计 Phi 相对改善至少为 O 相对增加的两倍|
|2 QUALITY_AWARE_PARETO|mode1 条件，并要求单步归一化 Phi 改善超过正向 Wire 增长与 rho 损失之和|

三种模式均限制总 Guide wire 代理≤起点×(1+wire_growth)、总 Via≤起点+64；热模块启用时 rho≥起点−1e−5。这是项目候选策略，不是官方总分公式。

整轮保留 V36Transaction 日志，每 Net 最多改一次，候选/联合组失败逆序回滚；联合资源通过同时移除、顺序插入算精确增量。全量复核 O/Phi/Wire/rho 与增量状态一致才提交。已提交轮作为 best-so-far；异常回到上一已提交合法状态。No improvement 或审计不一致整轮撤销。

内存不足时初始提案/预览回退；整轮 bad_alloc 保留资源正确性并向调用者报告，不伪造 ROUTED。未知异常不吞掉。大 Net 的完整 Wire 无法在既有预算下测量时整阶段拒绝优化。

实验入口：`python -B scripts/validate_v45.py --build --attempt <unique>`；`--case toy|sample001|open_3d_case001 --variant combined --attempt <built>`。独占输出目录，不覆盖历史，不进行 Git 或平台操作。
'''
    docs['ablation_results.md']='# case001 消融：相同输入与 Windows 原生 O1\n\n'+table(rows)+'''
各研究目录包含实际 command、二进制/配置/输入 SHA、Guide SHA、进程回执和资源日志。共有参数沿用 v4_4，统一 600s 总预算；模块开关与额外预算是受控变量。各模块先单独测，再测联合；不能把不同初始路径上的阶段增量相加当作独立贡献。

`path` 仅初始预览；`thermal` 仅末轮 TSV/热门槛；`phi` 新门槛+单分支修复；`repair` 严格门槛+分支/双 Net；`combined` 全部开启。`disabled` 是同一 V45 二进制关闭新模块的兼容对照。
'''
    docs['thermal_analysis.md']='''# 热模型核查

旧 `metrics.thermal_correlation_local` 使用裁剪的 Routing Grid 和现有插值 heat。V3 全局热优化、V45 门槛采用原始（含 die 外格）GCell grid，先合并相同 XY 的温度样本，再填格及扣 ambient，TSV 中心映射到该原始网格。两者不能相互替代。

批量 Pearson 300 个随机 oracle 与重建统计一致，共享目标格/多点交换单独覆盖。原始热网格对应参考模型方向，但最终必须读取独立 REFERENCE_20261103 结果，不能以 Router 自报代替。

case001 联合候选原始热 rho 相比 disabled 对照有提升，裁剪网格 rho 下降；这说明指标口径差异实际影响了结论。初始计划的保热只约束当时全体已定/未定中心，后续 V43/V44 仍可能改变全局分布，因此仍需最终对照。未得到正式官方数据，不宣称 Thermal Score 提升。
\n'''+table(rows)
    docs['tsv_assignment_comparison.md']='# TSV 分配与 footprint\n\n|版本|中心共址 Net 对|共享 XY 格|footprint Net 对-格关联|\n|---|---:|---:|---:|\n'+'\n'.join('|'+ '|'.join(fmt(r.get(k)) for k in ['variant','center_colocated_pairs','footprint_shared_xy_cells','footprint_pair_xy_incidences'])+'|' for r in rows)+'''

footprint 指 5µm 正面积矩形与 GCell 的几何覆盖，M10/M11 同一 XY 不重复计；不把矩形相交直接叫作 TSV 非法。Track 轨数、自身 Pin 抵扣和路径需求仍由原有模型处理。

初始 planner 的 pair_swaps 是代理提案交换数，不等于最终真实 Route 改动数；真实预览 accepted 和最终 phase pair_groups 单独保存在 result.json 的 v3_algorithm.v4_5 中。
'''
    docs['congestion_analysis.md']='''# 拥塞效果与归因

所有完整 Route 的比较都调用现有 prepare_v34_route / v34_usage / scoring_cell，不改固定需求与容量。新增全局质量门槛不是官方总分公式。

原 Exp01 从 271.93 降到 261.46 的 Phi 与 O 增加同时出现，说明过载从低容量重热点转向其他资源时两种统计可反向。V45 将 O 容差设为累计阶段预算，而非每次无限累加。

完整资源 by_layer 在 results.json；V45 每轮 before/after 及拒绝原因保存于 v3_algorithm.v4_5.rounds。候选阶段的 Phi 增量是相同完整模型下的差值。对于对照版本不同布线造成的单 Net 因果归属，保留未知，不将节点暴露差分误称独立归因。

初始 path 单独并未改善 case001 最终 Phi，表明局部完整路径门槛仍无法预测未来其他 Net 的需求。主要收益需由末轮修复与联合策略的实测消融确认。
\n'''+table(rows)
    docs['case_comparison.md']='# 三个公开 Case 最终结果\n\n'+table(formal)+'''

指标均为 Router Local Proxy；参考评分 NOT_EVALUATED（WSL 冷启动资源门槛未通过），官方分数 N/A。Windows 峰值工作集不是 Linux Peak RSS。

case001 当前 native_05 使用 case001_v4_5_run2，原 case001_v4_5 是本任务较早 native_04 候选，保留原数据。新候选修复通道采样创建多余资源的问题，与首轮 Combined 的 Guide SHA 完全相同。原 native_04 在初始提案预算回退后 Phi 更低，说明初始规划并非总有利，不能隐藏这条记录。

case003 已完成 151611 Net，两个修复轮全量资源复核通过；初始规划因资源缓存上限安全回退（578718 footprint 项、18.05s），实际新增收益来自末轮修复，不宣称所有模块都在大 Case 生效。末轮 O 1077612→1076388，Phi 92180.169722→91311.395417，wire 12628054290→12630474662 DBU；这是阶段内对照，不是旧版完整独立实验。

case005 未启动：6 GiB Commit/物理内存门槛未通过；无空运行目录、无假 Guide。精确资源快照见 formal_gates，后续需资源满足再启动。

TSV 独立审计只覆盖几何和 Guide Net 完整性；MODEL_DEPENDENT 不能称为完整合法性 PASS。结果、身份、逐层分布及审计计数见 results.json 的 formal_rows，完整运行回执见 formal_validation.json。
'''
    docs['performance.md']='# 性能与限制\n\n'+table(rows)+'''
Runtime 是 Router result.json 写出前累计秒数；总墙钟另见 execution.json。Windows peak 为 GetProcessMemoryInfo 的 PeakWorkingSetSize，不能伪称 Linux /proc VmHWM。旧 V44/Exp01 记录未可靠采集内存，因此保留 N/A；V45 在独立采样文件读取精确子进程 PID，不抢占 C++ 日志。

原生小型编译/运行要求 Windows Commit 余量≥2 GiB；冷启动 WSL 另保留其启动开销。case005 保持≥6 GiB门槛。不会关闭无关程序或调整系统设置。大型性能不能从 case001 外推。
'''
    docs['validation.md']='# 验证证据\n\n'+ '\n'.join('- '+str(p.relative_to(ROOT))+': '+read(p).get('status','UNKNOWN') for p in builds)+'''

- 随机 batch Pearson 重建 oracle、共享 bin、负计数、三种门槛、累积 O/Wire/Via/thermal 边界。
- 稀疏候选原始测试：随机目标 oracle、确定性、共享 footprint、零容量与预算。
- V45 实际 3D maze 修复、完整 Guide/Pin/连通性验证、bad_alloc 和异常/全局超时回滚、双 Die Route 与 TSV 完整资源释放。
- toy、sample001：完整 Router 与 JSON/Guide 解析。独立 TSV 审计结果按实际写入，不将 MODEL_DEPENDENT 写成 PASS。
- disabled case001 Guide 与历史 V44 SHA 相同，验证关闭新模块的输出兼容。
- 无官方评分成绩；参考评估单独记录来源 SHA 和真实状态。

部分复杂分支（超大高 Pin、多层非均匀随机拓扑等）仍需要扩大覆盖，不能将上述测试声称为所有可能输入的形式化证明。首次测试源码括号错误导致编译失败的回执保留于 native_01，已修正并使用新目录重新编译。
'''
    docs['final_conclusion.md']='''# V4.5 当前结论

状态 **EXPERIMENTAL**，不自动升级发布。

真实变化包括：双 Die 通道采样与完整初始 Route 预览；精确多点 Pearson；累计 Overflow/线长/热保护的 Phi-first 门槛；稀疏热点 owner 驱动的完整双 Net 事务与末轮复核。未复制第三方源码，未改资源或评分模型。

case001 首轮联合候选降低本地 O/Phi，Wire 增幅很小；path 模块单独无收益，不能声称四个模块都有效。初始未来需求未知仍是瓶颈；高 Pin/大树局部 tail 无法替代完整多终端全局优化。

继续研究应以相同参考来源下的单模块和 Pareto 结果为准：预算分配、低收益 pair 剪枝、高 Pin 安全子树重建、同一热网格模型上的最终验收。不能仅凭本地 Phi 推断官方得分，也不因为有新代码就认定值得开发新版本。

大型 Case 和参考评测若资源不足，明确保留阻塞，不伪造完成。最终选用哪一配置与三 Case 状态见 formal_validation.json 和各实际回执。
\n'''+table(rows)
    docs['v45_algorithm_design.md']+='''

最终内存约束补充：通道采样只读 scoring_capacity 和已存在资源的 fixed/usage，不为采样创建新资源；仍保留合法候选处理的资源增长上限。初始阶段相对资源表增长最多 250K；末轮最多 100K；每轮回滚旧 Route 节点总量最多 100K，不按 Net 数无限保存。预算耗尽会明确记录 fallback，不删除原始完整 Route。
'''
    docs['congestion_analysis.md']+='\n\n## 单模块相对 disabled 的实测差值\n\n|模块|Phi 差值|Overflow 差值|Wire µm 差值|\n|---|---:|---:|---:|\n'+ '\n'.join(
        '|'+name+'|'+'|'.join(fmt(by[name].get(k)-disabled.get(k)) for k in ['phi_local','overflow_local','wire_guide_proxy_um'])+'|'
        for name in ['path','thermal','phi','repair','combined'] if name in by)+'\n\n这些是独立实验的最终结果差值，不能相加为 Combined 的因果分解。case003 本地 M10/M11 Overflow 为 502085/567566，合计占总量约 99.4%；这两个层的 TSV footprint 与接入路径仍是主要瓶颈。\n'
    docs['validation.md']+='''

最终 native_05 已编译并通过全部本次构建配置测试；新增测试含双 Net 第一个/第二个安装点故障注入和禁止堆分配下回滚，以及 65 Pin 非均匀网格 TSV tail。V44 与 Exp01 两个宏组合的 main/router 预处理输出与任务前源码快照一致（去除空白后 SHA 相等），disabled Guide 与 V44 字节相同。Linux 编译/REFERENCE_20261103 尚未执行，不能用原生验证代替。源码、配置、旧评分器/二进制保护证据见 protected_integrity.json 和 old_version_preprocessor_compatibility.json。
'''
    docs['final_conclusion.md']+='\n\n## 三 Case 实际交付\n\n'+table(formal)+'''

case001 相对 V44 本地 Phi 降约 9.06%、Overflow 降约 3.96%、wire 代理增约 0.102%；热的原始网格 rho 提高，但旧裁剪网格 rho 下降，未证明官方热得分改善。Path-only 最终 Phi 反而增加；Thermal-only、Final Repair 和联合策略有本地收益。1%/2%/5% wire 预算产生相同 Guide，暂无理由扩大默认 0.5%。严格 Pareto 的 Overflow 更低，Phi-first 的 Phi 略低，需独立参考评分后选择。

本次未取得参考合法性结论，case005 和 Linux 验证仍受资源阻塞。保留 EXPERIMENTAL，不建议直接替代已验证提交版本。后续优先解决大 Case 提案预算与未来路径需求偏差，扩大高 Pin 安全子树覆盖，并在资源充足后完成统一参考评估；无需为版本号而自动开发 V4.6。
'''
    comparison=[rows[0],rows[1],next((r for r in formal if r['variant']=='open_3d_case001'),full)]
    docs['final_conclusion.md']+='\n\n## case001 三版本对照\n\n|指标|V4.4|Exp01|V4.5 native_05|\n|---|---:|---:|---:|\n'+ '\n'.join(
        '|'+label+'|'+'|'.join(fmt(r.get(key)) for r in comparison)+'|' for label,key in [
            ('Overflow（本地）','overflow_local'),('Phi（本地）','phi_local'),('Wire µm（代理）','wire_guide_proxy_um'),
            ('Thermal rho（裁剪网格）','thermal_clipped_grid'),('Thermal rho（原始网格）','thermal_raw_grid'),
            ('中心共址 Net 对','center_colocated_pairs'),('footprint 共享 XY 格','footprint_shared_xy_cells'),
            ('footprint 对-格关联','footprint_pair_xy_incidences'),('运行时间 s','runtime_seconds'),
            ('Windows 峰值工作集 bytes','windows_peak_working_set_bytes')])+'\n\nV4.5 正式行时间含写出和外层管理，历史行是 result.json 写出前累计时间，不能按此小差异判断运行速度；首轮同口径 Combined 为 79.405s。case003/005 的同平台 V4.4、Exp01 独立对照 N/A，未补跑或混用不同评分口径。\n'
    for name,text in docs.items():(ROOT/name).write_text(text,encoding='utf8')
    print('Wrote',len(docs),'reports and',len(rows),'measured case001 rows')


if __name__=='__main__':main()
