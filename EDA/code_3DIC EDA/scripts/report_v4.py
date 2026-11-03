"""Render factual V4 reports from immutable receipts; never route or score."""
import argparse,collections,csv,io,json,math,zipfile
from pathlib import Path
from common import PROJECT,sha256
from diagnostics import json_file
from scoring import reference_directory
from compare_v36 import small_metadata
ROOT=PROJECT/'outputs/reports/v4'

def table(headers,rows):
    return '| '+' | '.join(headers)+' |\n|'+ '|'.join('---' for _ in headers)+'|\n'+''.join('| '+' | '.join('N/A' if v is None else str(v) for v in row)+' |\n' for row in rows)

def text(name,value):
    (ROOT/name).write_text(value,encoding='utf8')

def generate():
    cases={};comparisons={};allgates={}
    for suffix in ['001','003','005']:
        case='open_3d_case'+suffix;label='case'+suffix+'_v4';run_dir=PROJECT/'outputs/runs'/case/label
        run=json_file(run_dir/'run.json');old_run=json_file(PROJECT/'outputs/runs'/case/('case'+suffix+'_v3_6')/'run.json')
        gate=json_file(ROOT/('case'+suffix+'_execution')/'resource_gate.json')
        if not run and gate.get('status')=='NOT_STARTED_RESOURCE_GATE':
            run={'status':gate['status']}
        old_dir=reference_directory(case,'case'+suffix+'_v3_6');new_dir=reference_directory(case,label)
        old=json_file(old_dir/'reference_report.json') if old_dir else {};new=json_file(new_dir/'reference_report.json') if new_dir else {}
        audit=json_file(ROOT/'audit'/case/'summary.json');algorithm=small_metadata(run_dir/'result.json').get('v4',{}) if run.get('status')=='ROUTED' else {}
        old_density=json_file(ROOT/'research/thermal_model_alignment.json').get(case,{}).get('density',{})
        new_density={};positions={}
        if (ROOT/'audit'/case/'tsv_candidates.csv').exists():
            with (ROOT/'audit'/case/'tsv_candidates.csv').open(encoding='utf8',newline='') as f:
                for row in csv.DictReader(f):positions[row['net']]=(int(row['col']),int(row['row']))
            counts=collections.Counter(positions.values());new_density={'unique_gcells':len(counts),'colocated_gcells':sum(v>1 for v in counts.values()),'max_per_gcell':max(counts.values(),default=0)}
        identity=all(old.get(k)==new.get(k) and old.get(k) for k in ['input_sha256','reference_source_sha256','reference_scorer_sha256']) if new else False
        rows=[]
        def metric(name,a,b):rows.append([name,a,b,b-a if isinstance(a,(int,float)) and isinstance(b,(int,float)) else None])
        metric('Reference Legality',old.get('static_legality'),new.get('static_legality'))
        metric('Wirelength (um)',old.get('wirelength_dbu',0)/old.get('dbu_per_micron',1) if old else None,new.get('wirelength_dbu',0)/new.get('dbu_per_micron',1) if new else None)
        for name,key in [('Reference Overflow','overflow'),('Congestion Potential','normalized_overflow_squared'),('Routing Incremental Overflow','routing_induced_overflow'),('Thermal Correlation','thermal_correlation')]:metric(name,old.get(key),new.get(key))
        for l in ['1','10','11']:metric('M'+l+' Overflow',old.get('by_layer',{}).get(l,{}).get('overflow'),new.get('by_layer',{}).get(l,{}).get('overflow'))
        metric('Zero Capacity Demand Cells',sum(x.get('zero_capacity_demand_cells',0) for x in old.get('by_layer',{}).values()) if old else None,sum(x.get('zero_capacity_demand_cells',0) for x in new.get('by_layer',{}).values()) if new else None)
        def peak_mib(record):
            value=record.get('router_peak_rss_bytes') or record.get('peak_memory_bytes')
            return value/2**20 if value is not None else None
        metric('Router Runtime (s)',old_run.get('runtime_seconds'),run.get('runtime_seconds'));metric('Router Peak RSS (MiB)',peak_mib(old_run),peak_mib(run))
        for label,key in [('TSV Colocated GCells','colocated_gcells'),('Max TSV per GCell','max_per_gcell')]:metric(label,old_density.get(key),new_density.get(key))
        changed=None
        if positions:
            with zipfile.ZipFile(PROJECT/'outputs/reports/official_platform/v3_6_26152/scoring_result_3_6.zip') as z,z.open('eval_temp/'+case+'/tsv_points.csv') as f:
                before={row['net']:(int(row['gcell_col']),int(row['gcell_row'])) for row in csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig'))}
            if before.keys()==positions.keys():changed=sum(before[k]!=v for k,v in positions.items())
        metric('Final TSV Sites Changed vs V3.6',0,changed)
        metric('Layer Escape Accepted',None,algorithm.get('layer_escape',{}).get('accepted'))
        metric('V4 Stage A TSV Moves',None,algorithm.get('global_tsv',{}).get('moved'));metric('V4 Stage B Thermal Moves',None,algorithm.get('thermal_refinement',{}).get('moved'))
        passed={'routed':run.get('status')=='ROUTED' and run.get('returncode')==0,'reference_valid':new.get('static_legality')=='REFERENCE_VALID','same_reference_identity':bool(identity),'center_tsv_conflicts_zero':audit.get('rule_matrix_conflicting_candidates',{}).get('A_D')==0,'unique_tsv':bool(audit) and not audit.get('counts',{}).get('ambiguous_nets',0) and not audit.get('counts',{}).get('missing_tsv_nets',0)}
        if new and old:
            passed.update(overflow_nonincrease=new['overflow']<=old['overflow'],potential_nonincrease=new['normalized_overflow_squared']<=old['normalized_overflow_squared']*1.0001+1e-9,wire_growth_bounded=new['wirelength_dbu']<=old['wirelength_dbu']*1.005+1e-6,thermal_loss_bounded=new['thermal_correlation']>=old['thermal_correlation']-1e-5-1e-10)
        allgates[case]=passed
        entry={'run':run,'reference':new,'algorithm':algorithm,'guide_sha256':sha256(run_dir/'route.guide') if (run_dir/'route.guide').exists() else None,'audit_classification':audit.get('classification'),'center_tsv_conflicts':audit.get('rule_matrix_conflicting_candidates',{}).get('A_D'),'gates':passed,'same_identity':bool(identity),'tsv_sites_changed':changed,'density':new_density}
        cases[case]=entry;comparisons[case]=rows
        content='# '+case+': V3.6 / V4\n\n'+table(['Metric','V3.6','V4','Delta'],rows)
        content+='\n身份一致：'+str(bool(identity))+'；缺失数据为 N/A。参考值不是官方分项或总分。\n\n独立审计：'+str(audit.get('classification','NOT_EVALUATED'))+'。中心规则 A+D 冲突：'+str(entry['center_tsv_conflicts'])+'；邻格 footprint 的模型分歧仍保留，不冒称所有解释都通过。\n\n```json\n'+json.dumps(algorithm,ensure_ascii=False,indent=2)+'\n```\n'
        if gate:
            content+='\n启动资源检查（未启动 Router，不是运行失败或 Guide 非法）：\n\n```json\n'+json.dumps(gate,ensure_ascii=False,indent=2)+'\n```\n'
        text('case'+suffix+'_comparison.md',content)
    abl=[]
    for path in sorted((ROOT/'ablations').glob('*/*/summary.json')):
        row=json_file(path);abl.append([row['case'],row['variant'],row['status'],row['overflow'],row['M1'],row['M10'],row['M11'],row['wirelength_um'],row['thermal_correlation'],row['runtime_seconds']])
    text('ablation_results.md','# Isolated ablations\n\n'+table(['Case','Variant','Reference','Overflow','M1','M10','M11','Wirelength um','Pearson','Runtime s'],abl)+'\nB/C/D 是实验宏构建，保存在 outputs/development/v4_ablations，SHA/宏/输入/配置/Guide 身份见各 identity.json。E 的 case001 结果使用正式 case001_v4，不重复执行。\n\ntoy 联合版本的 Layer Escape 先消耗共享线长预算，因此未保留单模块 Thermal 的改善。此结果揭示模块竞争，不宣称联合版本支配全部单模块。计时仅为单次同环境观测，不是统计性能结论。\n')
    text('architecture.md',r'''# V4 architecture — CANDIDATE / NOT_RELEASED

V3.6 parsing, striped PhysicalModel, capacity/fixed union, geometry Pin access,
Steiner/MST fallback, multi-target A*, negotiated congestion, full physical TSV
footprint and Guide pruning are retained. V4 executes after V3.6 redistribution
and before final pruning. No scorer/parser/model/config formula was changed.

V4Transaction wraps the tested move-only V36Transaction. It releases complete
cached old demand, builds a bounded local patch, validates the complete Net,
compares full old/new insertions against the same removed-Net baseline, then
installs and commits. Allocation failures, no paths, budget limits and injected
post-install exceptions restore all Route/TSV demand. Density mutation is after
all allocating detail/install operations. No full resource graph/route collection
is copied. A single candidate Net copy is bounded at 100000 nodes; high-Pin
optimization touches local edges, but complete-Net validation/copy has a cost.

For residual U0, fixed F, capacity C, and demand vectors d_old/d_new:

$$\\Delta O=\\sum_v [\\max(0,F_v+U_{0v}+d_{new,v}-C_v)-\\max(0,F_v+U_{0v}+d_{old,v}-C_v)]$$

$$\\Delta\\Phi=\\sum_v [(O_{new,v}/\\max(1,C_v))^2-(O_{old,v}/\\max(1,C_v))^2]$$

Stage A requires both nonincrease and one strict improvement. Stage B strictly
improves Pearson, forbids a move into equally/more dense target bins, and bounds
cumulative Overflow/Potential growth to 0.01% of its stage baseline. Entire V4
wire proxy growth is capped at 0.5%; per-Net growth <=2%+16 pitches and <=8 extra
ordinary Vias. Proxy budgets are not official scores. Via cost already appears
in the guide-grid wire proxy; it is not an extra resource token.

Fixed iteration/candidate ordering and Node-ID tie breakers are deterministic in
work-bounded fixtures. Wall-clock cutoffs can truncate different prefixes under
different CPU load; byte reproducibility at time truncation is NOT guaranteed.
Global timeout propagates and does not publish a false ROUTED result.
''')
    text('layer_escape.md','# M1 Layer Escape\n\nRank actual marginal M1 routing contribution; every fourth search slot reserves high-Pin candidates. Preserve real Pin access, TSV and branches. Replace at most eight degree-two interior M1 vertices in each direction in a radius-six local window on legal M1/M2/M3. Nonnegative marginal M1 prices preserve the geometric heuristic lower bound. Complete route/Guide/TSV uniqueness validation and strict M1 plus global congestion improvement precede commit.\n\nBudgets: 2048 trials, 90s phase, 8192 A* expansions per search, <=100000-node Net, <=200000 new materialized resources. Existing ordinary Via support is reused; the new behavior is hotspot selection, local chain repair and marginal acceptance. `high_pin_searched` counts trial invocations including preflight failures; only accepted repairs prove successful replacement. No claim is made that every rejected trial reached A*.\n')
    text('global_tsv_optimization.md','# Scalable global TSV\n\nNo Pin-count cutoff. Detach only bounded interface tails, preserve all other branches, reconnect both Dies and rebuild/validate the complete Net. Region size 32x32; four-entry regional frontiers are repriced from current usage on every visit. Regions receive round-robin opportunities; failed Nets cool down for the remaining phase. Candidate radii 1/2/4/8/16/32, at most two local patch attempts per Net. Counts and Pearson are sparse/incremental; no TSV-pair matrix. Stage A caps: 16000 attempts, 180s, 300000 new resource keys. Actual attempted/visited/accepted counts and rejection reasons are recorded separately. Footprint ranking is only a proposal heuristic; acceptance compares complete routes including released old TSV and both new escape paths.\n')
    text('thermal_optimization.md','# Thermal refinement\n\nUnmodified Thermal JSON and the existing full-grid projection are used. Platform heat_gcell.csv reconstruction matches V3.6 Pearson to about 6e-12 on case005; exact values and source hashes are in research/thermal_model_alignment.json. Stage B runs after congestion repair, uses incremental DensityCorrelation and local Manhattan displacement <=4 GCells to avoid wasting trials on hot but wire-infeasible remote jumps. At most 12000 attempts/120s. It strictly raises Pearson, prevents increased target multiplicity relative to the source bin, and admits <=0.01% cumulative Overflow/Potential tradeoff. Full wire and Via guards remain active.\n\nCounters distinguish accepted joint improvements and accepted thermal/congestion tradeoffs. Rejected candidate reasons preserve scope; the accepted-tradeoff counter is not a count of every preflight proposal. Percent Thermal/Congestion/total scores are N/A.\n')
    suite=json_file(ROOT/'tests/suite.json');verified=json_file(ROOT/'tests/v4_verified_test.json')
    extra_checks=[['M1 geometry corrected Track-direction oracle',json_file(ROOT/'tests/geometry_direction_corrected_test.json').get('returncode')],['Python V4 workflow (8)',json_file(ROOT/'tests/v4_workflow.json').get('returncode')],['CMake configure',json_file(ROOT/'tests/cmake_configure.json').get('returncode')]]
    extra_checks += [[k,v] for k,v in json_file(ROOT/'tests/audit_scoring_python.json').items()]
    text('unit_tests.md','# Validation evidence\n\n'+table(['Suite','Returncode'],[[k,v.get('returncode')] for k,v in suite.items()]+[['V4 final heap/time/vertical fixture',verified.get('returncode')],['Python version/CLI tests (28)',json_file(ROOT/'tests/python_checks.json').get('returncode')]]+extra_checks)+'\nOriginal V3.6 macro rebuild is byte-identical: '+str(suite.get('v36_byte_regression',{}).get('identical'))+'.\n\nIndependent tests cover HORIZONTAL/VERTICAL M1 escape, high-Pin terminal retention, fixed/routing attribution, zero Capacity, illegal Via, Via/wire guards, TSV blocking/unique overlap, post-install exceptions, actual heap allocation failure with allocation-free undo, search/global timeout, reverse resource removal, dense scheduling, repeatability and incremental/full Pearson. Existing core/physical/independent TSV tests together cover rotated geometry, Track intervals, no-Track Pins, stripe boundaries and workspace/interruption. The initial M1 diagnostic assumed HORIZONTAL and is superseded by the corrected VERTICAL oracle receipt and m1_geometry_summary.json. Large Case limits are measured in actual runs; synthetic tests do not establish a universal optimality or memory bound.\n')
    perf=[]
    for case,item in cases.items():
        r=item['run'];f=item['reference'];perf.append([case,r.get('status','NOT_STARTED'),r.get('runtime_seconds'),r.get('router_peak_rss_bytes'),f.get('runtime_seconds'),f.get('peak_rss_bytes')])
    text('performance.md','# Runtime and resources\n\n'+table(['Case','Router','Router seconds','Router peak bytes','Reference seconds','Reference peak bytes'],perf)+'\nOne run per formal Case, no automatic retry. Host Commit and WSL records are preserved in case*_execution and the run directories. Failed or gated stages remain NOT_STARTED/ANALYSIS_ERROR; they are not official FAIL.\n')
    ready=all(all(v.values()) for v in allgates.values())
    blocked={case:[name for name,passed in gates.items() if not passed] for case,gates in allgates.items() if not all(gates.values())}
    decision={'status':'CANDIDATE','release_status':'NOT_RELEASED','official_v36_total':45.92,'official_v35_total':45.39,'official_v4_total':None,'official_v4_status':'NOT_EVALUATED','local_quality_gate_passed':ready,'gates':allgates,'cases':{k:{'router_status':v['run'].get('status'),'reference_status':v['reference'].get('static_legality'),'guide_sha256':v['guide_sha256'],'audit_classification':v['audit_classification']} for k,v in cases.items()},'limitation':'Reference-valid is not official PASS. Neighbor footprint interpretations remain MODEL_DEPENDENT. No claim of beating 45.92 without platform result.'}
    decision['blocked_gates']=blocked
    (ROOT/'decision.json').write_text(json.dumps(decision,ensure_ascii=False,indent=2),encoding='utf8')
    text('summary.md','# V4 summary\n\n'+table(['Case','Router','Reference','Overflow','Pearson'],[[k,v['run'].get('status','NOT_STARTED'),v['reference'].get('static_legality','NOT_EVALUATED'),v['reference'].get('overflow'),v['reference'].get('thermal_correlation')] for k,v in cases.items()])+'\nV3.5 official 45.39 / V3.6 official 45.92 preserved. V4 official score N/A. Local gate: '+str(ready)+'. All failures, model gaps and module competition remain visible in the individual reports.\n')
    with (ROOT/'summary.md').open('a',encoding='utf8') as stream:
        stream.write('\nUnmet gates:\n\n```json\n'+json.dumps(blocked,ensure_ascii=False,indent=2)+'\n```\n\ncase003 reference Wirelength increased 0.500443%, marginally exceeding the predeclared 0.5% release gate by 55.60015 um. The internal guide-grid proxy guard is a different measure. The gate is not relaxed after observing results. case005 was not launched: measured Windows Commit headroom 5.34 GiB, then 3.22 GiB, below the 6 GiB start guard; WSL free memory alone does not remove the host constraint. No candidate ZIP is generated.\n')
    return decision

def package(decision):
    if not decision['local_quality_gate_passed']:raise ValueError('Quality gate failed: no candidate ZIP')
    path=PROJECT/'outputs/submissions/3DIC_guide_v4_candidate.zip';manifest=path.with_suffix('.manifest.json')
    if path.exists() or manifest.exists():raise ValueError('Submission path protected')
    files={}
    for case,record in decision['cases'].items():
        p=PROJECT/'outputs/runs'/case/(case.removeprefix('open_3d_')+'_v4')/'route.guide'
        assert sha256(p)==record['guide_sha256'];files[case+'.output.guide']={'path':str(p),'sha256':sha256(p)}
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name,item in files.items():z.write(item['path'],name)
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        import hashlib
        for name,item in files.items():
            h=hashlib.sha256()
            with z.open(name) as f:
                for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
            assert h.hexdigest()==item['sha256']
    with manifest.open('x',encoding='utf8') as f:json.dump({'candidate':True,'official_v4_score':None,'archive_sha256':sha256(path),'guides':files},f,indent=2)

def document(decision):
    path=PROJECT.parents[1]/'\u7b97\u6cd5\u7248\u672c\u8fed\u4ee3\u8bf4\u660e.md'
    original=path.read_bytes();data=original;newline=b'\r\n' if b'\r\n' in data else b'\n';before=sha256(path)
    marker=b'<a id="v4"></a>';next_marker=b'<a id="version-6"></a>'
    if marker in data:
        start=data.index(marker);end=data.index(next_marker,start);data=data[:start]+data[end:]
    elif next_marker not in data:raise ValueError('Cannot locate the existing chapter boundary')
    toc='- [V4：M1 逃逸、全局 TSV 与热联合优化](#v4)'.encode('utf8')
    if toc not in data:
        index=data.index(b'6. [');data=data[:index]+toc+newline+data[index:]
    build=json_file(ROOT/'build.json');geom=json_file(ROOT/'research/m1_geometry_summary.json')
    body='''<a id="v4"></a>
## V4：M1 逃逸、全局 TSV 与热联合优化（2026-11-03）

### V4.1 版本身份与研究基准

V4 基于 `f9db70d5f63543fa60a0e4d348fc0a44adbc5101`，开发分支 `v4-joint-optimization`。保留 **CANDIDATE / NOT_RELEASED**，未自动 commit、push 或上传。V3.5 平台真实总分 **45.39**、V3.6 平台真实总分 **45.92** 保持原值；V4 官方总分为 **N/A**，不能从参考 Overflow 推算。

新增 `build/router_v4`、`configs/v4.json`、`GR_V4` 与 `include/router_v4_impl.hpp`。正式构建同时继承 `GR_V3_FINAL / GR_V3_5 / GR_V3_6`，公共参数与 V3.6 字节相同。消融关闭宏只用于 `outputs/development/v4_ablations/`。完整命令和源码 SHA 清单见构建回执；V3.6 旧宏分支重建 SHA 与原二进制完全一致。

'''
    body+='Router SHA256：`'+build['sha256']+'`。Config SHA256：`'+build['config_sha256']+'`。\n\n'
    body+='''### V4.2 M1 归因与官方模型差异

独立 M1 位图分别计算 Pin、OBS 及共享 Track，使用真实实例几何、Track 方向和 GCell 边界。case005 M1 为 **VERTICAL**。Pin/OBS 并集需求为 '''+str(geom.get('fixed_union_track_demand'))+'''，其中 Pin '''+str(geom.get('pin_track_demand'))+'、OBS '+str(geom.get('obs_track_demand'))+'、共享 '+str(geom.get('shared_pin_obs_tracks'))+'''。参考模型固定基线 Overflow 为 0，路由增量为 146,946；官方 M1 总 Overflow 为 175,425。**这不证明官方固定基线为 0，也不意味着全部路由增量均可消除。** 无 Track Pin 的具体 Track 指派仍是模型假设。

case005 M1 热点 Recall 89.01%、Precision 99.97%，Top-1000 覆盖率 100%，官方热点上的 Demand MAE 约 0.254；12,360 个官方热点未被参考模型命中。MAE 仅针对官方正 Overflow CSV 域，不能冒称全芯片 MAE。平台 TSV 坐标逐 Net 与本地节点一致，未通过平移索引或扣除固定偏差拟合数据。

V3.6 case005 共 39,542 条 TSV Net，其中 5,369 条因 Pin 数大于 32 被排除；受限排序仅保留 2,048 条，实际尝试 1,498 次，接受 29 次；942 次 Thermal Guard、443 次拥塞保护、84 次线长保护拒绝。最终相对 V3.5 改变 33 个 TSV 位置，不能将此等同于阶段 accepted 数，也不能在缺少历史 Trace 时分解初始选址与后处理的因果贡献。

### V4.3 三个实际模块

| 模块 | 实际改变 | 保护与预算 |
|---|---|---|
| M1 Layer Escape | 按真实边际 M1 拥塞选择 Net；保留 Pin、TSV、分叉点，替换局部 degree-two 路径；在合法 M1/M2/M3 搜索 | 2048 次、90 秒、单搜索 8192 展开；每四个名额为高 Pin 保留一个；严格减少 M1 与整体拥塞 |
| Global TSV | 按区域公平调度，四候选前沿动态重计价；拆除并重接两侧短尾路径，支持高 Pin Net，不再重布整个高 Pin 拓扑 | 16000 次、180 秒、每 Net 两次；半径 1/2/4/8/16/32；稀疏资源与密度，不构造 TSV 对矩阵 |
| Thermal Refinement | 在 Stage A 后增量提高 Pearson，禁止向同等或更高密度格堆叠 | 12000 次、120 秒、局部曼哈顿位移不超过 4 格；允许阶段 Overflow 与 Potential 累计增加至多 0.01% |

V4Transaction 复用经过测试的 move-only 回滚，仅保存单条受限 Route；局部子图修改后检查完整 Net，候选副本与完整验证仍有成本。所有模块共用原 PhysicalModel、占用、Pin 抵扣、TSV footprint、Guide 和验证，不降低固定需求，也不更改参考评分规则。

设旧 Net 移除后的占用为 $U_0$，对完整新旧需求分别求插入增量：

$$\\Delta O=\\sum_v[\\max(0,F_v+U_{0v}+d_{new,v}-C_v)-\\max(0,F_v+U_{0v}+d_{old,v}-C_v)].$$

Potential 为 $\\sum_v(O_v/\\max(1,C_v))^2$。Stage A 要求 Overflow 和 Potential 均不增加且至少一项严格改善，热相关损失不超过 $10^{-5}$；Stage B 严格提高 Pearson，并显式报告拥塞权衡。V4 全程 Guide-grid 线长代理预算为 +0.5%，单 Net +2% 加 16 个 pitch，普通 Via 最多增加 8 个。代理值不是官方分数。

### V4.4 测试、消融与局限

C++ 验证包含水平/垂直 M1、必需 Pin、高 Pin 局部修复、非法 Via、TSV 排除与唯一性、反序恢复、安装中断、真实 heap 分配失败时无分配回滚、搜索预算、全局超时和 Pearson 独立重算。旧 core、physical、V3.5/V3.6 测试通过；版本 CLI、独立 TSV 审计和评分入口测试通过。V4 新增工作流 Mock 测试验证低 Commit、身份变化与冲突均不启动计算。

toy/sample001 五组消融全部参考合法。toy 联合方案的 Layer Escape 会占用线长预算，从而阻止单模块 Thermal 的改善；case001 仅 TSV 的总拥塞收益优于联合方案，但联合方案额外降低 M1。**联合并不支配所有单模块。** 所有消融身份、实际命令和指标见独立报告，未将实验二进制冒称正式 V4。

`high_pin_searched` 是局部 trial 调用数，包含可能的预检拒绝；accepted 才证明已完成实际修复，不能将全部调用数声称为 A* 成功数。固定工作夹具重复结果一致；墙钟预算截断仍可能受 CPU 负载影响，不承诺超时截断时逐字节确定性。

### V4.5 三个公开 Case 的同口径结果

'''
    rows=[]
    for suffix in ['001','003','005']:
        case='open_3d_case'+suffix;rd=PROJECT/'outputs/runs'/case/('case'+suffix+'_v4');r=json_file(rd/'run.json');refdir=reference_directory(case,'case'+suffix+'_v4');n=json_file(refdir/'reference_report.json') if refdir else {};od=reference_directory(case,'case'+suffix+'_v3_6');o=json_file(od/'reference_report.json') if od else {}
        rows.append(['case'+suffix,r.get('status',decision['cases'][case]['router_status'] or 'NOT_STARTED'),n.get('static_legality','NOT_EVALUATED'),o.get('overflow'),n.get('overflow'),n.get('wirelength_dbu',0)/n.get('dbu_per_micron',1) if n else None,n.get('thermal_correlation'),r.get('runtime_seconds')])
    body+=table(['Case','Router','Reference','V3.6 Overflow','V4 Overflow','V4 线长 µm','V4 Pearson','Router 秒'],rows)
    body+='''
缺失结果标 N/A，不代表合法或失败。三个 Case 严格顺序执行；完整参考合法性与中心规则 TSV 审计均需完成。`MODEL_DEPENDENT` 保留邻格 footprint 与边界解释差异，不冒称所有规则解释均通过。参考 scoring 的源码、ELF SHA、输入和单位须一致；完整差异见各 Case 对照报告。

case005 启动前检查 Windows Commit、WSL 内存/Swap、其他进程与磁盘。验证脚本门槛为至少 6 GiB Windows Commit；本次实测先为 5.34 GiB，随后降至 3.22 GiB，因此保留 `NOT_STARTED_RESOURCE_GATE`，未启动 Router。资源门槛不等于 Guide 不合法。仓库 Q87–Q89 记录正式单例 12 小时/128 GB；本地计划 14,400 秒不超过该时间限制，但本机资源仍单独检查。

case003 参考线长增长 **0.500443%**，比预先声明的 0.5% 发布门槛多约 **55.60015 µm**。内部 Guide-grid 代理保护与参考 Wirelength 并非同一量；不能事后放宽门槛或宣称全部发布检查通过。case005 尚未验证，故没有生成候选 ZIP。

### V4.6 发布判定与证据

'''
    body+='当前本地完整质量门槛：`'+str(decision['local_quality_gate_passed'])+'`。V4 始终保持 **CANDIDATE / NOT_RELEASED**；没有 V4 官方平台成绩，不能判断是否超过 45.92。只有三组 ROUTED、参考及独立审计完整、目标与资源保护满足后，才允许生成候选 Guide ZIP；不自动上传。\n\n'
    body+='[完整报告](./EDA/code_3DIC%20EDA/outputs/reports/v4/summary.md)、[M1 归因](./EDA/code_3DIC%20EDA/outputs/reports/v4/research/m1_resource_attribution.md)、[模型误差](./EDA/code_3DIC%20EDA/outputs/reports/v4/research/official_model_gap.json)、[消融](./EDA/code_3DIC%20EDA/outputs/reports/v4/ablation_results.md)、[发布判定](./EDA/code_3DIC%20EDA/outputs/reports/v4/decision.json)。\n\n'
    encoded=body.replace('\n',newline.decode()).encode('utf8');index=data.index(next_marker);data=data[:index]+encoded+data[index:]
    def outside_v4(value):
        value=value.replace(toc+newline,b'')
        if marker in value:
            start=value.index(marker);end=value.index(next_marker,start);value=value[:start]+value[end:]
        return value
    if outside_v4(original)!=outside_v4(data):raise ValueError('Unrelated document bytes changed')
    path.write_bytes(data)
    (ROOT/'documentation_change.json').write_text(json.dumps({'before_sha256':json_file(ROOT/'documentation_change.json').get('before_sha256',before),'previous_render_sha256':before,'after_sha256':sha256(path),'scope':'one V4 section and its TOC link; all other bytes retained','outside_section_bytes_identical':True},indent=2),encoding='utf8')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--package',action='store_true');p.add_argument('--update-document',action='store_true');a=p.parse_args();d=generate()
    if a.package:package(d)
    if a.update_document:document(d)
    print(json.dumps({'local_quality_gate_passed':d['local_quality_gate_passed'],'status':d['status'],'cases':d['cases']},ensure_ascii=False))
