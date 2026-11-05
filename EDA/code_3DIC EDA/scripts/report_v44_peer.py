"""Reproduce static peer study without importing or executing third-party code."""
import ast
import hashlib
import json
import math
from pathlib import Path
import zipfile
from common import PROJECT

ROOT = PROJECT/'outputs/reports/v4_4_peer_analysis'
ARCHIVE = Path.home()/'Downloads/3dic-optimized-v0.11.1.zip'


def generate():
    ROOT.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    index = {}
    with zipfile.ZipFile(ARCHIVE) as z:
        for info in z.infolist():
            if info.is_dir(): continue
            if info.file_size > 2**20: raise ValueError('Unexpected archive member size')
            raw = z.read(info)
            entry = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            if info.filename.endswith('.py'):
                tree = ast.parse(raw.decode('utf8'))
                entry['symbols'] = [{'name': n.name, 'line': n.lineno, 'end': n.end_lineno}
                                    for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.ClassDef))]
                entry['imports'] = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom))]
            index[info.filename] = entry
    (ROOT/'peer_source_index.json').write_text(json.dumps({'archive_sha256':sha,'members':index,
        'execution':'NOT_EXECUTED','license':'NOT_PRESENT','score_linkage':'UNVERIFIED'},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    documents = {}
    documents['peer_algorithm_analysis.md'] = r'''# v0.11.1 静态算法解剖

## 身份与分析范围

原 ZIP SHA256：`ARCHIVE_HASH`。仅用 zipfile 读取和 AST 解析，未导入、解压执行或上传。无许可证，候选为独立 C++ 实现，没有复制对方算法源码。逐文件 SHA 与函数行号见 `peer_source_index.json`。

用户提供的 95.83 / 85.19 / 6.57 拥塞分与此 ZIP 的关联为 **UNVERIFIED**，包内无 Guide、报告或运行回执。README.md:40–43 将另一个 75.50 成绩关联到 submission 20616，并明确独立包尚无官方成绩；portable.py:95 的自报版本是 0.11-portable。二者都不能验证用户表格。本文代码位置均相对于原 ZIP。

## 实际入口顺序

```mermaid
flowchart TD
  A[run.sh → portable.main] --> B[optimize.main]
  B --> C[AdaptiveRouter 初始布线]
  C --> D[Relocator 局部 TSV]
  D --> E[Relocator 全局热 TSV]
  E --> F[NegotiatedRefiner]
  F --> G[两次完整 TSV 定价的全局热移动]
  G --> H[WireShortener]
  H --> I[MazeWire → compact]
  I --> J[按 Net 数扩展 Maze → compact]
  J --> K[BalancedThermal 联合移动]
  K --> L[CongestionRepair → 最终 compact → 本地验证]
```

依据：run.sh:1–4；portable.py/main:13–108；optimize.py/main:38–48。不是只运行 negotiated.py，也不是直接调用 joint_thermal.py。

| 问题 | 实际实现与精确位置 | 重要限制 |
|---|---|---|
| 初始路径 | adaptive_router.py/route_net:214–246；grid_router.py/_build:60–83 按 Die 建 MST、连接 TSV 锚点、合并走廊 | Net 按 DEF 顺序提交，非全局同时求解 |
| 初始选层 | adaptive_router.py/__init__:43–62、connect:76–107，枚举相邻 H/V 层对和 hv/vh | 初始排除 M10/M11 常规走线；短连接优先最近四组，不是无限搜索 |
| 初始定价 | adaptive_router.py/pressure:64–74，利用率四次差分 + 20 倍过载平方差分 | 零容量是有限 10000 惩罚，不是硬拒绝 |
| TSV 候选 | candidate_centers:109–212，Pin 中位数附近、接口 Pin、局部热邻域和全局热候选 | route_net 选择第一个通过完整本地检查的候选；仍为顺序贪心 |
| 共址抑制 | candidate_centers:119–131、147–176、179–199 | 一部分额外候选排除已占中心；普通候选仍可共享，另有软共址费，不是每格最多一个 TSV |
| 固定资源 | calibrated_resources.py/_fixed_shapes:21–54、_seed_obstacles:56–74；routing_resources.py/capacity:70–80 | 实际层矩形、方向变换、Track 并集；未实现无 Track Pin 至少一轨规则 |
| 热点排序 | refine.py/targets:131–154；negotiated.py/run_pass:105–139；congestion_repair.py/targets:26–37 | Negotiated 风险排除 M10/M11；最后 repair 才按全资源边际平方潜能排序 |
| RRR | negotiated.py/reroute:47–103：移除旧需求，尝试权重 8/24/64、多层和 hv/vh，再安装最优合法候选 | 默认一轮，最多 12000 Net；Maze 仅 ≤6 Pin、最多 300 次；冻结接口需求 |
| A* | maze_router.py/connect:43–127，优先方向 XY + 相邻层，边界 ±5 GCell，多源连接已有树 | 状态数 4000；MazeWire 提高到 12000，并用 h×1.05，不能称最优 A* |
| Steiner | steiner_tree.py/sparse_mst:13–42、steiner_tree:45–76；wire_shortener.py/shared_build:19–35 | 稀疏 Kruskal 与三元中位点候选（至多 8），不是精确 RSMT/FLUTE |
| 全局 TSV 移动 | relocate.py/initialize:104–135、reroute:144–229；balanced_thermal.py/initialize:19–45 | 单 Net 逐次移动；局部通常只重建两候选，Balanced 为六候选；非全局匹配 |
| 接受/回滚 | refine.py/reroute:156–202；wire_shortener.py/reroute:113–152；congestion_repair.py/reroute:45–72 | 在内存计数上移除再加回选择结果，有目标检查；不是本项目的 C++ 异常安全事务 |
| 最终修复 | congestion_repair.py:15–72 | full phi 不增加，默认预算 12000，2～256 Pin；冻结 TSV 实际中心/候选几何 |
| 合法性 | validate.py/check_net:107–180、validate:183–202 | 检查 Net 集、Pin 几何、方向、连通性、TSV；本地检查不等于官方 PASS |

## 不能直接移植的资源差异

| 阶段 | TSV / Phi 模型 |
|---|---|
| Adaptive 初始 | 普通 Guide cell 去除 own Pin credit 后计 1；TSV 中心两层各再加 1（route_net:237–245） |
| Relocator | 中心 M10 加 1、M11 加完整 capacity；phi=(0.1×O/C)²（relocate.py/counts:73–91） |
| Negotiated | 又回到中心两层各加 1（negotiated.py/counts:33–42），不是前一阶段模型 |
| Wire / Joint / Balanced / FinalRepair | native_resources.py/native_counts:22–34：M10 中心 +1，M11 中心按 slot_capacity 加权；slot_capacity:12–20 为经验模型 |
| 最终 repair | full phi=(O/C)²（congestion_repair.py/phi:19–21）；声明 MODEL_PARITY_NOT_ESTABLISHED（:87–88） |

本项目按 5µm TSV 矩形覆盖全部相交 GCell、实际 Track 计数；对方 native model 仅按中心单格经验收费。Pin 的 Track 上边界、零 Track Pin、TSV 禁区也不同：calibrated_resources.py:64 使用 bisect_left；tsv_obstacles.py:15–20 仅禁止 M10/M11 中心格 Pin/OBS，而当前本项目对任意层几何采用保守禁区。**不移植其 Pin credit、中心 TSV 降价或零容量有限惩罚来“提高分数”。**

## case005 低分：可以确认的机制与不能确认的因果

没有对应 Guide/官方 CSV，不能确认低分根因，更不能确认合法性。以下仅是源码风险：

* TSV 仍逐 Net 贪心，候选与热热点集中，部分候选允许共址；没有全局稀疏匹配。
* BalancedThermal 覆盖父类 objective，以 wire/congestion/thermal 加权接受（balanced_thermal.py:47–71），取消 joint_thermal.py:51–53 的严格 phi 不退步门槛。可能用拥塞换热；最终 repair 冻结 TSV，无法修复由移动产生的共址。
* Maze 随规模扩至 4% Net，最多 8000；按用户 Net 数 case003 为 6065、case005 为 7188（含初始 256，portable.py:61）。最终 repair 12000 对 179700 为约 6.68%，且排除 >256 Pin。数量覆盖不等于热点收益，不能直接定罪预算。
* relocate 内部 .01 phi 与最终 full phi 相差 100 倍，阶段间 TSV demand 也改变；无法凭阶段目标改善推断最终同口径改善。
* 未发现 compact 引入新 GCell 的证据：compact_guide.py/compact_net:16–93 只保留现有 cell 子集，验证 TSV 候选一致、线长不增和本地连通性。它不是当前可证实根因。
* parser 未将普通 LEF VIA 定义用于所有相邻层检查，validate 的相邻层正面积连接不是完整工艺 DRC。大密度下官方合法性仍需独立验证。

这些缺陷不能被写成“对方 case005 正因某项而得到 6.57”。最高优先级补证是取得对应 Guide SHA、运行回执、输入 SHA、评分器身份与逐格 CSV。
'''
    documents['v44_current_state.md'] = r'''# 当前 V4.4 基线

任务开始 HEAD：ee56e1eb4777ae80e066cb741fe71443da4bf8ec。原配置 SHA256：39c37646af5e76b8fe9c0b7304e3695a12d047edadeef2dbd8665155c7ce0dfa。

任务开始时没有 `build/router_v4_4` / `.build.json`，没有 V4.4 运行目录；旧 decision 为 CANDIDATE_NOT_COMPILED / BLOCKED_RESOURCE。因此不能把 V4.3 实验数值或 V4.2 官方数据称为当前 V4.4 的执行效果。基线原始源码和配置在 `baseline_sources.zip`，SHA 清单 `baseline_identity.json`。本次独立构建见 `native_relative_paths/`；候选从不覆盖正式 build 二进制。

| 项目 | 当前源码事实 | 定位（原基线行号） |
|---|---|---|
| 初始 TSV | 按 Net 顺序、bbox 网格采样；真实 footprint 边际价格 + 平均 Pin 距离 + 冷点惩罚；允许共享 | src/router.cpp/select_tsv:279–321、run:791 起 |
| 是否全局协调初始分配 | 没有，后面才执行全局移动 | run 中 redistribute_tsv_global → optimize_v43 → prune → optimize_v44 |
| 现有 V44 价格 | 非负 2ΔO + 32ΔPhi + .1利用率⁴，乘 present 加 history | include/router_v4_4_impl.hpp/v44_price:8–18 |
| 重布能力 | 程序保留 V43 联合事务；V44 额外阶段为逐 Net 3D 链 patch 或小 Net 全树 TSV 移动 | v44_patch:22–42、v44_trial:44–95 |
| V44 预算 | 最多 150 秒，3 轮，每轮 4096 尝试；单次 .4 秒、24000 展开、129×129 搜索窗口 | optimize_v44:97–155、v44_trial:55–70 |
| 排除 | >10000 route nodes 跳过；TSV 全树仅 ≤32 Pin、≤4096 nodes；候选只试前 4 | :46、:62、:113、:119–128 |
| History | 最多 200k 项、单格 16、present 最大 4；失败两次冷却 | :106–117 |
| 接受 | ΔO≤0 且 ΔPhi≤0，至少一个严格下降；总线长增长 1%，单 Net 20%+8unit，Via+16；热相关守门 | :81–85、:103–105、:123 |

V44 不是完全没有拥塞/TSV 优化，而是**缺初始全局协调**，并有较紧的后处理全树预算。强制 O 与 Phi 同时不增会拒绝 O 略升但高惩罚热点显著下降的方案；是否值得放宽需同模型消融，不能直接放宽所有保护。任务开始时执行次数、拒绝比例和共址减少量为 N/A；本次已得到 case001 原生执行数据，见文末实测表和 experiment_results.json。V43 的统计仅为历史旁证。
'''
    documents['peer_vs_v44.md'] = r'''# 对方与当前 V4.4

| 能力 | 对方 ZIP | 当前 V4.4 | 本次取舍 |
|---|---|---|---|
| 资源模型 | 阶段之间漂移；中心 TSV 经验模型 | 统一实际 footprint / Track 并集，官方一致性仍有模型假设 | 保留本项目模型 |
| 初始多层 | 显式枚举 H/V pair、hv/vh、动态利用率价格 | 3D A* 已可选层，但没有同样的初始模板组合比较 | 可做后续独立模块，不称本次已改 |
| 初始 TSV | 已占位置软惩罚 + 部分候选避重，但顺序贪心 | 资源边际计费但无跨 Net 预分配 | 本次首选稀疏预分配、坐标下降和双 Net 交换 |
| 联合重布 | 多种逐 Net 几何方案、固定接口 | 已有 V43 多 Net 事务 + V44 patch | 不删除既有能力 |
| 最终修复 | full phi 非增，不硬要求总 O 非增；12k Net | ΔO/ΔPhi 双门槛，4k attempts/轮，最多 150s | 先归因初始分配，避免混入接受规则变化 |
| 安全性 | 本地验证，不覆盖全部工艺规则 | 既有 Guide/资源/TSV 校验和异常回滚 | 继续独立验证 |
| 可证实质量 | 对应官方成绩未建立身份 | 任务开始未编译 | 当前不能证明谁的算法优于谁 |

对方可能有用的组合是：较早分散拥塞、广泛尝试层/形状、最终 full-phi 修复。不能据此断言“某一模块导致 95.83”。本次不复制其模型差异，也不将其最终 repair 当作可以移动 TSV 的阶段。
'''
    documents['official_congestion_root_cause.md'] = r'''# 官方拥塞证据边界

扫描本项目全部文件、`EDA/补充资料` 与 `outputs/reports/official_platform`：找到 V3.5/V3.6 官方 scoring/prediction ZIP，以及 V4.2/V4.3 **REFERENCE_20261103** 结果。未找到任务所述 V4.2 三份官方逐格 CSV/审计原件；已询问其本地位置。

因此 36.92/0.99/0.54、66.62%/99.31%/96.84%、3072/7129 在本次均标 **USER_REPORTED_NOT_REVERIFIED**，不伪装成已重新逐格复算。V3.6 的官方数据不能冒充 V4.2。

用户描述的正容量观测公式为

\[
P=\sum_{C_v>0}(\max(0,D_v-C_v)/C_v)^2,\qquad S_C=100/\sqrt{1+P}.
\]

这不是本次已证明的全部官方规则，更不涵盖 C=0、全格范围、Pin 抵扣、TSV footprint 等细节。本次候选优化相同形式的参考 Phi，不生成官方 Congestion Score。

现有 `../v4_4_congestion_research/baseline_audit.json` 实为 V4.3 参考资源审计：case001 参考 O=10575，M1=594/M10=735/M11=9246；case003 参考 O=1086195，M1=550/M10=507195/M11=572082。已登记 CSV SHA，固定需求 baseline O=0。由此可确认该参考模型中接口层路由需求是主要压力，**不能把全部接口 O 归因于 TSV 共址**：还有多格 TSV footprint 相交、接入线和同 Net 合并。

拿到 V4.2 官方原件后，复算应包含 CSV/Guide/Input SHA、评分版本、所有容量正格的 Phi、零容量需求单列、M10/M11 分层占比、TSV 多中心共享同格与不同中心 footprint 重叠分别统计。候选不能在资料缺失时以重定义 Demand 来追分。
'''
    documents['tsv_assignment_analysis.md'] = r'''# 稀疏初始 TSV 规划

新增 `include/sparse_tsv_assignment.hpp` 与 `include/router_v44_congestion_exp01_impl.hpp`。仅 `GR_V44_CONGESTION_EXP01` 启用；原 V44 实现文件不变。

对于候选 s，成本采用真实两侧 footprint 的增量：

\[
\Delta J(n,s)=L_{access}(n,s)+T_{cold}(s)
 +32\sum_v[\phi_v(F_v+U_v+d_{nsv})-\phi_v(F_v+U_v)] +2k_s.
\]

其中 `prepare_v34_route` 生成 d，包含全部相交 GCell 与既有 own-Pin 抵扣；F/C 来自既有 scoring model。k 是已经规划到同中心的 TSV 数；2k 是 `2*k*(k-1)/2` 共址目标的边际，不是硬禁止共享。L 用两侧所有 Pin 的平均曼哈顿距离（与原 select_tsv 尺度相同），T 保持原冷点罚。**L/T 是代理，不能替代最终线长/热相关性验收**。

步骤：原始合法最佳点 + 半径 1/2/4/8 邻域 → 合法过滤与24候选上限（保留原点）→ 候选少的 Net 优先分配 → 正反序最多4轮坐标下降 → 最多12000对候选交换。双 Net 交换可以跨越单独移动都不改善的局部极小值；不是完整最小费用流、也不是全局最优算法。

全流程只维护稀疏候选与 footprint，不建立 Nets×全部 GCells 矩阵。累计 footprint 边≤4M，45s软预算；超预算不发布部分计划，回退原初始算法。全局 Router 超时继续抛出。空间为 O(候选 footprint + 唯一资源)，而非 O(Nets×GCells)；时间近似 O(4×候选 footprint + bounded pair exchanges)。有序 map 引入 log R 开销，尚需大规模测量。

规划不修改真实 occupancy。真实路由时仅给当前 Net 设置 forced TSV，完整 route/validate 通过后才由原 run 提交。明确的不可达/搜索耗尽/连通性失败回退原选址；bad_alloc、全局超时与未知错误不会被吞掉。热全局分布及最终 Wirelength 不保证改善，因此本候选不是发布版；后续必须通过完整参考评分的 Pareto gate。

与对方的差别：同一物理模型贯穿初始与最终；显式协调未来 Net；不限制最多1个TSV；不忽略邻格 footprint；不更改 Pin/OBS 禁区。当前没有分区边界交换、全局最优证明，也没有预测完整接入路线需求，报告中不声称实现这些能力。
'''
    documents['algorithm_improvements.md'] = r'''# 实施范围与验证入口

已独立实施：稀疏 TSV 分配器、双 Net 交换、真实 footprint 接入、首次路由失败回退、可关闭开关、独立候选目标与配置、可审计测试日志。

候选：`configs/v4_4_congestion_exp01.json`；`exp01_initial_tsv=1`。其余参数逐项与 `v4_4.json` 相同。设置为0用于同二进制消融。CMake目标 `router_v4_4_congestion_exp01`、`sparse_tsv_assignment_tests`、`v44_exp01_regression`；候选 `--version` 明确 EXPERIMENTAL/NOT_VALIDATED。不登记为正式 V4.4，不改默认Router。

接口改动仅受新宏保护：router.hpp声明、main参数/版本、run初始挂钩、algorithm诊断。保留 V43/V44 所有后处理与资源模型。原代码/config快照在 baseline_sources.zip；历史二进制与评分输入不写入。

Linux验证（资源允许后，在独立构建目录顺序执行）：

```sh
cmake -S . -B outputs/development/v4_4_peer_analysis/linux_build -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON
cmake --build outputs/development/v4_4_peer_analysis/linux_build --target sparse_tsv_assignment_tests v44_exp01_regression router_v4_4 router_v4_4_congestion_exp01 --parallel 1
ctest --test-dir outputs/development/v4_4_peer_analysis/linux_build -R '^(sparse_tsv_assignment_tests|v44_exp01_regression)$' --output-on-failure
```

不得直接覆盖已有构建目录中的正式结果。Windows验证脚本 `python -B scripts/validate_v44_peer.py --native-tests --attempt <未占用名称>` 采用相对编译输出，避开MinGW中文绝对路径问题；每次启动前检查2GiB Commit，独立目录拒绝覆盖。

未实施：新的初始多层模块、替换 negotiated、放宽最终接受门槛。这些应在初始 TSV 的独立收益明确后再单独加入。原 V44 已有这些方向的部分能力，不能把重复开关当新算法贡献。
'''
    documents['ablation_results.md'] = r'''# 消融状态

本表以实际回执为准；后续本次实验的数值见 `experiment_results.json`（如存在），编译与回归见 `native_relative_paths/`，分配核测试见 `kernel_test.json`。

| 模块 | 实现状态 | 官方/参考质量结论 |
|---|---|---|
| 当前 V44 baseline | 源码快照保留，独立构建 | 尚无本次官方评分 |
| 新初始 TSV | 已实现，独立宏/开关 | 以完整实验回执为准；合成改进不是比赛提升 |
| 新初始层选择 | 未实施 | N/A |
| 新全局重布 | 未实施（保留已有 V43/V44） | N/A |
| 新 final repair | 未实施（保留已有 V44） | N/A |
| 新联合优化 | 未实施，避免无法归因 | N/A |

已执行独立核测试：2-Net 共址、Bottom/Top 分别冲突、合法共享、多格 footprint 重叠、双Net交换、超时隔离、零容量/重复计数拒绝，以及100组全量目标oracle和确定性检查。无随机伪造Guide，也未执行第三方代码。

Wire预算仍1%；2%/5% Pareto实验未执行。不能声称找到最优线长/拥塞/热权衡。case003必须待case001同模型有效；case005还需≥6GiB Commit且无正在运行任务。本次不因测试缺席降低资源门槛。
'''
    documents['case_comparison.md'] = r'''# 可比性表

| 指标 | V4.2 官方旧数据（用户提供、未复核原件） | V44 当前 | 新候选 | 对方日志（ZIP关联未验证） |
|---|---:|---|---|---:|
| case001 Congestion Score | 36.92 | N/A | N/A | 95.83 |
| case003 Congestion Score | 0.99 | N/A | N/A | 85.19 |
| case005 Congestion Score | 0.54 | N/A | N/A | 6.57 |
| 平方拥塞潜能 | 原逐格审计待定位 | 见真实实验，非官方 | 见真实实验，非官方 | N/A |
| TSV共址GCell | 用户报告003=3072、005=7129 | N/A | N/A | N/A |
| 官方 Wirelength | N/A | N/A | N/A | 仅Wire Score，不是Wirelength |
| 官方 Thermal Score | N/A | N/A | N/A | 001=56.65 / 003=61.91 / 005=19.37 |
| 官方合法性 | 用户称PASS，身份待核 | NOT_EVALUATED | NOT_EVALUATED | UNVERIFIED |
| 运行时间 | 可用历史记录但缺官方绑定 | 见本次回执 | 见本次回执 | N/A |

对方 Wire Score 为69.42/78.57/65.77，Overflow为20/77/29491；不换算线长，不反推官方Phi，不把它的本地validate当官方PASS。用户给出的均分62.32同样未验证。

本次产生的 Windows 原生实验与既有 WSL 运行时间不可直接混排。只有相同输入、编译优化、预算、参考评分来源的 baseline/candidate 才构成消融；编译平台、Guide SHA、Config SHA均应在回执中保存。
'''
    documents['final_conclusion.md'] = r'''# 阶段结论（不冒充质量验收）

1. **对方高分因果未证实。** 静态上值得借鉴的是初始层/路径多候选、动态拥塞定价、TSV避重、最终全资源phi修复；无关联Guide不能给单项归功。
2. **case005=6.57根因未证实。** 可见风险为预算/Pin限制、仍然顺序贪心、阶段资源模型漂移、热阶段可能允许phi反弹、最终冻结TSV。不是实测因果。
3. **case001 的 V44 最终中心共址已为0。** 本次基线和候选均为0，不能将候选收益归因为消除中心共址。case003/005尚无V44实测，不知道是否消除其历史共址；邻格footprint重叠仍需区分。
4. **已补初始跨Net协调，但还缺热与接入路由联合约束。** 本次独立实现稀疏真实footprint规划、坐标下降、双Net交换；保留原后处理，不复制对方经验计数。case001结果显示仅有冷点罚不足以保护最终全局热相关性。
5. **哪项官方收益最大：N/A。** 合成交换/分散测试通过不等于真实官方提升；尚不能推荐替代正式版本。
6. **下一步：**资源允许时直接复用本次已生成的 toy、sample001、case001 Guide，使用固定参考身份评分，不必重复布线；比较Phi、O、Wire、热相关、合法性与TSV中心/footprint重叠。只有通过后才做002之外的大型case003；case005另检查6GiB门槛。补齐V4.2官方CSV身份，再校核参考偏差。先初始分配，后独立实验phi优先接受和层枚举，最后才调权重。

所有完成/阻塞状态及本次资源和测试证据以 `task_status.json` 为准。未运行项不能表述为通过。没有Git提交、推送、上传或第三方代码执行。
'''.replace('002之外的大型case003','大型case003')
    for name,body in documents.items():
        (ROOT/name).write_text(body.replace('ARCHIVE_HASH',sha).strip()+'\n',encoding='utf8')
    measured_reports()
    print('Wrote',len(documents),'reports; third-party source indexed, not executed')


def measured_reports():
    rows=[]
    for summary in sorted((ROOT/'experiments').glob('*/*/summary.json')):
        run=json.loads(summary.read_text(encoding='utf8'));p=summary.parent
        if run['router_status']!='ROUTED':continue
        result=json.loads((p/'result.json').read_text(encoding='utf8'))
        layers={};nx=len(result['x_lines'])-1;ny=len(result['y_lines'])-1
        for node,cap,use,fixed,over in result['resources']:
            layer=str(node//(nx*ny));a=layers.setdefault(layer,{'overflow':0,'phi':0.,'routing_demand':0,'fixed_demand':0,'hotspots':0,'fixed_overflow':0,'zero_capacity_demand':0})
            a['overflow']+=over;a['routing_demand']+=use;a['fixed_demand']+=fixed;a['hotspots']+=over>0;a['fixed_overflow']+=max(0,fixed-cap)
            if cap>0:a['phi']+=(over/cap)**2
            else:a['zero_capacity_demand']+=use+fixed
        audit_path=p/'tsv_audit/summary.json'
        audit=json.loads(audit_path.read_text(encoding='utf8')) if audit_path.exists() else {}
        row={'case':run['case'],'variant':run['variant'],'router_status':'ROUTED','guide_sha256':run['guide_sha256'],
             'reference_status':'NOT_EVALUATED_RESOURCE_BLOCKED','official_status':'NOT_EVALUATED',
             'nets':len(result['nets']),'guide_wire_proxy_um':run['audit']['wire_proxy_measured_dbu']/result['dbu_per_micron'],
             'router_seconds_before_output':result['metrics']['runtime_seconds'],'host_seconds':run['execution']['runtime_seconds'],
             'router_peak_rss_bytes':None,'runtime_platform':'Windows MinGW O1',
             'overflow_local':result['metrics']['overflow_local'],'phi_local_positive_capacity':math.fsum(a['phi'] for a in layers.values()),
             'thermal_correlation_local':result['metrics']['thermal_correlation_local'],'by_layer_local':layers,
             'tsv_density':run['audit']['tsv_density'],'tsv_audit':audit.get('classification','NOT_RUN'),
             'tsv_center_pin_conflicts':audit.get('counts',{}).get('center_PIN_candidates'),
             'tsv_neighbor_pin_risks':audit.get('counts',{}).get('neighbor_PIN_candidates'),
             'initial_planner':run['algorithm'].get('initial_tsv_assignment'),
             'v44':run['algorithm'].get('v4_4')}
        rows.append(row)
    if not rows:return
    compare={}
    for case in sorted({x['case'] for x in rows}):
        variants={x['variant']:x for x in rows if x['case']==case}
        if not {'baseline','initial_tsv'}<=variants.keys():continue
        a,b=variants['baseline'],variants['initial_tsv']
        compare[case]={key:{'baseline':a[key],'candidate':b[key],'delta':b[key]-a[key]}
                       for key in ['overflow_local','phi_local_positive_capacity','guide_wire_proxy_um','thermal_correlation_local','router_seconds_before_output']}
        base=json.loads((ROOT/'experiments'/case/'baseline/result.json').read_text(encoding='utf8'))
        new=json.loads((ROOT/'experiments'/case/'initial_tsv/result.json').read_text(encoding='utf8'))
        if [n['net'] for n in base['nets']]!=[n['net'] for n in new['nets']]:raise ValueError('Net identity mismatch')
        compare[case]['changed_tsv_centers']=sum(x['tsv_node']!=y['tsv_node'] for x,y in zip(base['nets'],new['nets']) if x['cross_die'])
        compare[case]['changed_net_node_sets']=sum(x['nodes']!=y['nodes'] for x,y in zip(base['nets'],new['nets']))
    measured={'scope':'Router local resources and Guide proxy, NOT independent reference or official scoring','rows':rows,'comparison':compare}
    (ROOT/'experiment_results.json').write_text(json.dumps(measured,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    table=['\n## 本次实际完成的原生对照\n',
           '以下全部为 **Windows MinGW O1、Router 本地资源模型**。Guide 线长为已有独立读取脚本计算的代理值，不称官方 Wirelength。\n',
           '| Case | 方案 | Nets | 本地 O | 本地 Phi | Guide 线长代理 µm | 本地 rho | Router 秒（输出前） | TSV 中心共址对 |',
           '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        table.append(f"| {r['case']} | {r['variant']} | {r['nets']} | {r['overflow_local']} | {r['phi_local_positive_capacity']:.6f} | {r['guide_wire_proxy_um']:.3f} | {r['thermal_correlation_local']:.8f} | {r['router_seconds_before_output']:.3f} | {r['tsv_density']['colocated_pairs']} |")
    table+=['\nRouter Peak RSS 为 **N/A**：本次原生测试驱动曾与 C++ Progress 同时创建 resource_monitor.csv，C++ 输出 MONITOR_UNAVAILABLE；Host 原生监控不支持此指标。已修复测试驱动，使后续由 C++ 独占资源日志；未为补齐 RSS 重跑已有实验。退出码、完整 Guide/result、命令及耗时均已保存。',
            '\n基线/候选 toy 和 sample001 均203 Net，case001 均3152 Net。候选关闭开关后的 toy Guide SHA 与基线逐字一致；sample001 两方案 Guide SHA也相同。C++回归包含真实Maze、任意顺序联合事务、TSV资源恢复、bad_alloc与超时。',
            '\ncase001 独立 TSV 审计：中心 Pin/OBS 冲突均0，但 baseline/candidate 分别650/641个邻格Pin风险，分类 MODEL_DEPENDENT；sample001同样有3项模型依赖。toy为NO_VIOLATION_FOUND。不能由此声称官方PASS。',
            '\n参考评分 **BLOCKED_RESOURCE**，没有执行官方评分。参考冻结源码与二进制已核对；256MiB地址空间预算的小评分也未通过冷WSL资源门槛。详见 reference_gate.json。']
    addition='\n'.join(table)+'\n'
    for name in ['ablation_results.md','case_comparison.md','v44_current_state.md']:
        with (ROOT/name).open('a',encoding='utf8') as f:f.write(addition)
    c=compare.get('open_3d_case001')
    if c:
        a,b=c['phi_local_positive_capacity']['baseline'],c['phi_local_positive_capacity']['candidate']
        wire=c['guide_wire_proxy_um'];fraction=(wire['candidate']/wire['baseline']-1)*100
        text=f'''\n## case001 的实际否决证据

候选本地Phi {a:.6f} → {b:.6f}（{(b/a-1)*100:.2f}%），O 7735 → 7869，Guide线长代理405531 → 407964µm（+{fraction:.2f}%），rho 0.03541715 → 0.03095395。Runtime 52.006 → 56.352秒（原生、输出前计时，非Linux横比）。不能把Phi换算成官方分数。

657个TSV中504个最终中心不同；两方案最终中心共址均0。候选初始规划用了438793条footprint记录、92次坐标移动、63次配对交换，约12.91秒，657条计划路由完成且回退0。**初始协调确实执行了，但不能归因为消除最终中心共址**。

因此候选不满足总Overflow/热指标同时不退步的验收，不升级正式版本，不启动case003/005。仅凭Phi下降继续扩大实验会忽略热损失。下一步应让初始规划考虑全局密度相关性与实际接入需求，再分别消融；不是增加更多轮数就能解决。尚未执行2%/5%线长Pareto实验，不能宣称找到最佳预算。
'''
        for name in ['final_conclusion.md','tsv_assignment_analysis.md','algorithm_improvements.md']:
            with (ROOT/name).open('a',encoding='utf8') as f:f.write(text)
    status={'status':'IMPLEMENTED_NATIVE_TESTED_REFERENCE_BLOCKED','candidate':'v4_4_congestion_exp01','promote':False,
            'candidate_quality':'NOT_PARETO_IMPROVING_ON_CASE001_LOCAL_MODEL','reference':'BLOCKED_RESOURCE','linux_build':'NOT_RUN',
            'native_build_and_router_regressions':'PASS','standalone_assignment_tests':'PASS',
            'case001':'3152/3152 ROUTED twice: baseline and independent candidate','case003':'NOT_RUN_QUALITY_GATE','case005':'NOT_RUN',
            'peer_score_linkage':'UNVERIFIED','v42_official_audit_source':'NOT_LOCATED','router_peak_rss':'UNAVAILABLE_MONITOR_CONFLICT',
            'third_party_executed':False,'official_evaluator_modified':False,'git_mutated':False}
    (ROOT/'task_status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


if __name__ == '__main__':
    generate()
