"""Publish local V44 evidence without running or scoring a Router. Never overwrite."""
import ast,io,json,subprocess,sys,unittest
from pathlib import Path
from common import PROJECT,sha256
from diagnostics import windows_memory
from evaluate_reference_20261103 import source_identity
from analyze_v44 import save
ROOT=PROJECT/'outputs/reports/v4_4_congestion_research'

def main():
    audit=json.loads((ROOT/'baseline_audit.json').read_text(encoding='utf8'))
    protected=json.loads((ROOT/'protection.json').read_text(encoding='utf8'))
    changed=[name for name,digest in protected.items() if not (PROJECT.parent/name).is_file() or sha256(PROJECT.parent/name)!=digest]
    save(ROOT/'preservation.json',{'checked_files':len(protected),'changed':changed,'reference_source_sha256':source_identity()[0]})
    if changed:raise RuntimeError('Protected file identity changed')
    stream=io.StringIO();suite=unittest.defaultTestLoader.discover(str(PROJECT/'tests'),pattern='test_v44_workflow.py')
    outcome=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    with (ROOT/'python_tests.log').open('x',encoding='utf8') as f:f.write(stream.getvalue())
    syntax=[]
    for name in ['analyze_v44.py','validate_v4_4.py','report_v44.py']:
        ast.parse((PROJECT/'scripts'/name).read_text(encoding='utf8'));syntax.append(name)
    save(ROOT/'tests.json',{'python_tests':outcome.testsRun,'passed':outcome.wasSuccessful(),'python_syntax':syntax,'cpp':'NOT_TESTED: CMake launch failed before compilation','router_experiments':'NOT_TESTED'})
    baseline={}
    for suffix in ['001','003']:
        case='open_3d_case'+suffix;label='case'+suffix+'_v4_3'
        run=json.loads((PROJECT/'outputs/runs'/case/label/'run.json').read_text(encoding='utf8'))
        ref=json.loads((PROJECT/'outputs/reports/scoring/reference_20261103'/case/label/'reference_report.json').read_text(encoding='utf8'))
        baseline[case]={'run':run,'reference':ref}
    small={case:json.loads((PROJECT/'outputs/reports/v4_3/ablations'/case/'joint/summary.json').read_text(encoding='utf8')) for case in ['toy','sample001']}
    evidence={'status':'BLOCKED_RESOURCE','algorithm_status':'CANDIDATE_NOT_COMPILED','baseline':baseline,'small_baseline':small,'new_experiments':[],
              'official_scores':None,'final_windows_memory':windows_memory(),'environment':json.loads((ROOT/'environment_failure.json').read_text(encoding='utf8')),
              'source_commit':json.loads((ROOT/'source_before.json').read_text())['git_head'],
              'source_sha256':{p.relative_to(PROJECT).as_posix():sha256(p) for folder in ['src','include'] for p in sorted((PROJECT/folder).glob('*')) if p.is_file()},
              'config_sha256':sha256(PROJECT/'configs/v4_4.json'),'router_v4_4_sha256':None,'guide_sha256':None,'reference_source_sha256':source_identity()[0]}
    save(ROOT/'results.json',evidence)
    save(ROOT/'decision.json',{'status':'CANDIDATE_NOT_COMPILED','validation':'BLOCKED_RESOURCE','replace_v4_3':False,'official_score':None,'git_mutations':False,'platform_upload':False,'protected_files_unchanged':not changed})
    table='| Case | V4.3 Overflow | V4.3 线长 µm | V4.3 Phi | V4.3 Thermal rho | V4.4 |\n|---|---:|---:|---:|---:|---|\n'
    for case,row in baseline.items():
        r=row['reference'];table+=f"| {case} | {r['overflow']} | {r['wirelength_dbu']/r['dbu_per_micron']:.3f} | {r['normalized_overflow_squared']:.6f} | {r['thermal_correlation']:.9f} | NOT_TESTED |\n"
    table+='| case005 | N/A | N/A | N/A | N/A | BLOCKED_RESOURCE |\n'
    measured='| Case | Total O | Fixed baseline | Routing increment | M1 | M10 | M11 | Hot cells | Top 1% O share |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|\n'
    for case,r in audit.items():
        if 'overflow' not in r:continue
        l=r['by_layer'];measured+=f"| {case} | {r['overflow']} | {r['fixed_baseline']} | {r['routing_increment']} | {l['1']['overflow']} | {l['10']['overflow']} | {l['11']['overflow']} | {r['hotspot_cells']} | {r['top_fraction']['0.01']['fraction']:.2%} |\n"
    diagnosis='''# V4.3 拥塞诊断（真实历史数据）

''' + measured + '''
统计直接来自冻结 REFERENCE_20261103 resources.csv，逐行核对 O=max(0,F+U-C)。固定基线为零只表示固定占用独立不超容量，不表示固定占用无影响，也不证明全部路由增量都可消除。Pin/OBS 的轨道并集保持不变。

Top 1/5/10% 的分母为 CSV 输出的有需求资源，不是全芯片的所有空闲格。case001 Top 1% 占 85.25%，case003 为 50.32%；两者 Top 5% 已包含全部 Overflow。每层 GCell 而非 XY 合并后的格作为资源单位。另在 JSON 中给出仅对拥塞格排序的结果，避免分母混淆。

case001 的 M11 占 87.43%，M1+M11 占 93.05%；case003 的 M10+M11 占 99.36%，M1+M11 占 52.72%。资源模型和跨 Die 连接限制提示 TSV footprint 是重要研究方向，但其精确拥塞贡献与两侧接入路径贡献均为 UNKNOWN：CSV 没有逐 Net Pin 豁免及 footprint max 合并来源，不能凭接口层总量完成归因。

热点 Net 表为结构关联代理，不是可加和的精确边际贡献。case001 中 net416/392/941、case003 中 FE_OFN17114_n_143076 等与热点相交；是否真能绕行必须由实际合法搜索决定。完整候选及可移动 degree-two 节点数见 hotspot_analysis.json。

源码确认：每 Net Guide 去重，TSV footprint 与 Guide 取 max，再抵扣同 Net Pin 格；Pin/OBS 对实际 Track 求并集。本次未发现足以证明重复计费的新证据。旧 analyze_congestion.py 的“节点数+2TSV”恒等式不适用于新 footprint 模型，因此新增只读 CSV 分析器，没有调整计费规则。

V4.3 case003 Adaptive TSV 触及 16000 次试探上限，只访问 7999/22574 个跨 Die Net；12719 次 wire_or_via_guard。协作修复只有 6 次尝试、2 次接受，98 次 no_movable_local_seed；只处理 degree-two 短链是覆盖不足的证据。case001 协作仅接受 1 次，TSV 接受 145 次。不是全部失败都由 A* 不可达导致。

case005 无 V4.3 Guide/参考报告；V4.2 已有 ROUTED 历史，但没有相同参考口径的完成报告，因此不将旧本地 Overflow 混入。toy/sample001 已读取现有 V4.3 summary，未重复运行。
'''
    design='''# V4.4 设计与取舍（源码候选，未完成编译验证）

以完整 V4.3 初始布线、优化和 Guide Pruning 输出为基线，再执行独立 GR_V4_4 后处理。初始选址重构暂未实现，以避免同时改变基线和无法归因。未新增官方评分公式。

## 选择的算法

1. 多轮有界协商：按完整 Route 拆除可减少的 O+32Phi 排序，最多 3 轮（配置 1..6），拥塞历史价上限 16、最多 200000 项；Present 系数最多 4。两次无效访问冷却，连续两轮无接受停止。
2. 多层修复：保护 Pin/TSV/分支点；每侧最多 32 条边，窗口 margin 12，最多邻近四层，24000 次展开。原 V4.3 只处理较短链，新实现可跨越更长拥塞段。必须合法 Via、首选方向、同 Die 搜索。
3. 稀疏 TSV 协调：每 Net 半径 1/2/4/8/16 的候选，按完整 5µm footprint 边际 O/Phi 与历史资源价格排序，仅搜索 Top 4；小 Net 重建两侧完整树。它是顺序动态价格启发式，不是全局最优最小费用流，也没有宣称硬区域容量分配。
4. 新搜索价格：正容量下 p*(2DeltaO+32max(0,DeltaPhi)+0.1min(1,(F+U+q)/C)^4)+H；p<=4,H<=16。零容量返回无穷大，同 Net Pin 豁免沿用真实模型。所有附加价非负，原几何 A* 下界仍成立；有限窗口不保证全图最短路。

参考 [McMurchie/Ebeling PathFinder 原论文](https://janders.eecg.utoronto.ca/1387_2015/readings/pathfinder.pdf) 的逐轮资源协商思想。本候选使用更保守的单事务 Pareto 接受，不允许已提交状态质量恶化；未实现 PathFinder 式整轮临时恶化与 best-so-far 快照。既有 V4.3 三 Net 原子事务保持。新增更大多 Net 联合优化为 NOT_IMPLEMENTED。

## 完整接受与边界

候选在相同拆除基线比较完整新旧资源，不仅比较 TSV 中心。要求 Overflow/Phi 均不变差且至少一项改善，参考线长总量不越预算；每 Net 线长最多旧值1.2+8unit，Via 最多增加16。TSV 热相关性不低于阶段基线1e-5；所有修改保留原合法性检查。

V36Transaction move/undo 复用；所有可能分配的报告字段在提交前完成，安装异常需无分配恢复。每候选0.4秒、整个阶段150秒、每轮4096次试探、资源表增量最多200000；完整 TSV 树仅<=32 Pins/4096 nodes，全部候选<=10000 nodes。搜索窗口最多129×129×10，避免 Nets×GCells 稠密矩阵。实际 C++ 异常/内存回滚测试尚未执行，不能据代码设计宣称已验证。

固定模型、参考模型、旧编译分支、原二进制不修改。V4.4 开关在 Router 私有选项及 main 独立解析，不改 model.hpp，确保参考评分器源码身份不变。
'''
    implementation='''# V4.4 实现状态

新增 include/router_v4_4_impl.hpp、configs/v4_4.json、tests/v44_regression.cpp、scripts/analyze_v44.py、scripts/validate_v4_4.py、scripts/report_v44.py、tests/test_v44_workflow.py。

增量接入 include/router.hpp、src/router.cpp、src/main.cpp、include/router_v3_impl.hpp 与 CMakeLists.txt。新 target 为 router_v4_4/v44_regression，历史 target 默认宏未改变。原源码已归档 outputs/development/v4_4/source_before.zip，有逐文件 SHA 清单。

配置独立字段：v44_wire_growth、v44_rounds、v44_cost、v44_negotiation、v44_layer、v44_tsv。参数范围及布尔数值在 C++ 入口校验。候选默认1%线长增长，必须实测后再选择。不存在已验证的 build/router_v4_4；未登记可用正式版本。历史 router_v4_3/v4_2、配置、Guide、报告哈希复核见 preservation.json。

环境恢复后可执行 python scripts/validate_v4_4.py build --attempt recovered01；它只会在资源门槛满足时串行编译、运行新增测试，成功后才发布候选二进制及身份。后续 tests、experiment 使用独立输出目录，冲突即拒绝。所有参考评分使用冻结 SOURCE SHA，不启动旧官方 ELF。
'''
    ablation='''# 消融与线长预算

| 组件 | 实现 | 本次实测 |
|---|---|---|
| V4.3 基线 | 复用核对历史 | 已有 REFERENCE_VALID |
| 仅新 Cost | 新增单轮修复价格 | NOT_TESTED |
| 仅 Negotiation | 多轮历史价调度 | NOT_TESTED |
| 仅 Layer | 扩展链与层窗口 | NOT_TESTED |
| 仅 Global TSV | 稀疏动态资源价格 + 两侧完整树；非全局最优分配 | NOT_TESTED |
| V4.4 Joint | 上述组合 | NOT_TESTED |
| 新初始全局分配 / 更大多 Net 交换 | NOT_IMPLEMENTED | N/A |

| 相对完整V4.3线长预算 | Overflow/Phi/Wire/Via/TSV/Thermal/Runtime/RSS/合法性 |
|---|---|
| 0% | NOT_TESTED |
| 0.5% | NOT_TESTED |
| 1% | NOT_TESTED |
| 2% | NOT_TESTED |
| 5% | NOT_TESTED |

不能计算收益或 Pareto frontier，也不能称任何候选有效。公开大用例由小用例验证、case001 同源评分改善和资源门槛共同控制。当前无新 Router 实验或 Guide。
'''
    environment='''# 性能与环境限制

CMake 配置实测退出1，未到 C++ 编译；日志只有 WSL 混合编码代理提示。随后只读探测退出4294967295，错误为 Wsl/Service/CreateInstance/ERROR_NETNAME_DELETED。其时 Windows Commit 可用435761152字节（0.406 GiB）。

后续只读 boot_id 从81b17b28-6127-4cff-8dbf-2c63a57cdd99变为54a9550f-13e7-4a08-8c70-2ebe0032e1fd；WSL启动后 Commit 可用1176035328字节（1.095 GiB）。能确认WSL会话变化，不能确认OOM或根因。原始 CMake log/receipt 与 environment_failure.json 已保留。没有执行 shutdown、修改系统配置或结束无关进程。

资源门槛现增加 WSL 探测后的 Commit 复核：启动WSL前的可用内存不等于启动后的余量。构建/小实验至少2GiB；case005至少6GiB。低于门槛不启动WSL探测。没有以降低门槛来完成实验。

V4.4 Runtime/Peak RSS 为 N/A。旧 V4.3 单次观测 case001 37.634秒/102.902MiB，case003 592.921秒/904.496MiB；不是本轮性能提升或统计显著结论。新增诊断逐行CSV、1个Net流式读取，最大缓冲见baseline_audit.json。
'''
    tests='''# 测试结果

7 项 Python unittest 实际通过，耗时及逐项日志见 python_tests.log；三个新增管理/诊断脚本 AST 语法检查通过。测试覆盖真实小型CSV分解、0容量保留、UNKNOWN归因、已有文件冲突保护、缺失构建拒绝运行、非法预算、WSL启动前后内存门槛。测试用临时目录自动清理。

C++ CMake 配置未成功，故新增与旧版C++回归、旧版二进制重编译等价检查均 NOT_TESTED。新 tests/v44_regression.cpp 包含实际迷宫修复及安装后 bad_alloc/异常回滚夹具，并复用旧两Net交换、TSV资源、任意顺序回滚测试；它们尚未执行，不得写为通过。

toy/sample001/case001/case003/case005 的 V4.4 实验均未启动；没有新的参考合法性结论。历史 V4.3 结果仅只读复用。
'''
    recommendation='''# 最终建议

状态：CANDIDATE_NOT_COMPILED / BLOCKED_RESOURCE。已实际写入独立C++算法候选、配置、测试和诊断工具，但构建环境中断，未完成算法验收。不能取代V4.3，也不具备本轮提交依据。没有新V4.4二进制、Guide或真实成绩。没有Git add/commit/push/fetch、分支操作或平台提交。

''' + table + '''
## 下一步最重要的两个瓶颈

1. **M10/M11完整TSV footprint与接入树的联合优化**：占据绝大部分真实溢出，必须补足逐Net反事实归因，测量完整树重连是否突破短尾修复局限。若顺序定价效果有限，再考虑稀疏区域拍卖/多Net TSV交换，不直接宣称当前实现为最优全局分配。
2. **可验证的线长—拥塞预算与拒绝机制**：V4.3 大量试探被Wire/Via保护拒绝，需完成0/0.5/1/2/5%消融，按真实参考结果选择Pareto点；当前没有新数据证明应放宽限制，也没有98分依据。

恢复顺序：稳定Windows Commit/WSL → 独立串行构建与全部回归 → toy/sample001 → case001消融 → 满足质量/身份/资源门槛后case003。case005未满足6GiB不启动。无需用户重建任何历史结果。

仍未知：官方20261103无Track Pin轨道选择、Guide/Via计费细节、百分制拥塞与热增益、完整线长归一化及总分。这里只使用 REFERENCE_20261103，所有官方百分制和总分 N/A。模型假设不变，不能用参考合法冒充官方PASS。
'''
    documents={'baseline_analysis.md':diagnosis,'congestion_diagnosis.md':diagnosis,
        'algorithm_design.md':design,'v44_implementation.md':implementation,'ablation_results.md':ablation,
        'case_comparison.md':'# 相同参考口径的新旧对比\n\n'+table+'\nV4.4 未运行。所有分数 N/A；不能计算改善比例。\n',
        'performance.md':environment,'test_results.md':tests,'final_recommendation.md':recommendation}
    for name,text in documents.items():
        with (ROOT/name).open('x',encoding='utf8',newline='\n') as f:f.write(text)
    print('REPORTS',len(documents),'PROTECTED',len(protected),'PYTHON',outcome.testsRun,'PASS',outcome.wasSuccessful(),flush=True)

if __name__=='__main__':main()
