"""Build the local V5 Trace delivery report from measured artifacts."""
import collections
import json
from pathlib import Path
from common import PROJECT
from run_v5_trace_case import source_identity


def main():
    out=PROJECT/'outputs/reports/v5_trace';run=PROJECT/'outputs/runs/toy/toy_v5_trace_release'
    eq=json.loads((out/'equivalence_result.json').read_text(encoding='utf8'))
    ui=json.loads((out/'ui_browser_result.json').read_text(encoding='utf8'))
    api=json.loads((out/'ui_api_result.json').read_text(encoding='utf8'))
    receipt=json.loads((run/'trace/receipt.json').read_text(encoding='utf8'))
    records=[json.loads(line) for line in (run/'trace/route_trace.jsonl').read_text(encoding='utf8').splitlines()]
    accepted=[n for n in records if n['accepted']]
    astar=[n['net'] for n in accepted if 'REAL_ASTAR' in n['origins']]
    lost=[n for n in records if not n['accepted']]
    algorithms=collections.Counter(a for n in accepted for a in n.get('algorithms',[]))
    identity=source_identity();assert identity['file_count']==79
    assert eq['status']==ui['status']==api['status']=='PASS'
    timings=eq['runs'];hash=eq['runs']['on']['guide_sha256']
    usage=sum(p.stat().st_size for p in (run/'trace').iterdir() if p.is_file())
    (out/'original_sha256_after.json').write_text(json.dumps(identity,indent=2),encoding='utf8')
    summary={'release_run':str(run),'guide_sha256':hash,'verified_nets':len(accepted),'astar_final_nets':astar,'lost_nets':[{k:n.get(k) for k in ('net','reason','candidate_id')} for n in lost],'final_algorithms':dict(algorithms),'trace_directory_bytes':usage,'original_file_count':79,'equivalence_status':eq['status'],'ui_status':ui['status']}
    (out/'delivery_summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf8')
    documents={
    'implementation.md':f'''# V5 Trace 实现与源码检查

交付入口：`scripts/run_v5_trace_case.py`；独立源码：`v5_trace/engine/`。
验证结果：Toy 默认完整流程 {len(accepted)}/203 Net 为 FINAL_ACCEPTED_VERIFIED；{len(astar)} 个含真实 A* 回溯边。

## 原始源码保护

`v5/engine/` 原始 **79 个文件 SHA256 全部不变**。复制前身份在 `v5_trace/original_sha256.json`，交付后核对在本目录 `original_sha256_after.json`。
Trace 引擎复制全部 79 个文件，只在副本中加入观察调用和装饰器，并向两个独立脚本目录加入观察模块。`instrumentation.json` 列出 36 个插桩副本。去掉 `_trace` 导入、调用和装饰器后，所有原始 Python AST 与副本完全一致，测试通过。
没有改动 V4.5、原始 V5、评分器、DEF、LEF 或历史 Guide，没有执行 Git 写命令或上传，也没有运行 case001/003/005。浏览器回归只读取已有 V4.5 case001 记录。

## 实际路径如何产生

| 文件 / 阶段 | 原始实际行为 | 记录点 |
|---|---|---|
| maze_router.py（含 baseline_v12） | 有界三维 A*；从目标沿 parents 反向回溯；复用已连通树为种子 | 回溯完成、转换矩形之前读取真实 path；记录实际节点和相邻边 |
| router.py / grid_router.py | Manhattan HV/VH、ZX/ZY 折线；Prim MST 选终端连接关系，大扇出用 sparse MST | 实际 rect() 原始线段参数，以及 stack() 的相邻层边 |
| adaptive_router.py | 为每条连接选择 H/V 层对和 HV/VH 备选 | 备选独立暂存，只将原算法 best 选中的备选加入候选；缓存亦通过几何身份对应 |
| topology_router.py | MST、水平/垂直主干；支持模式和层对选择 | _build 返回候选、实际线段及拓扑方法 |
| wire_shortener.py | MST、Steiner、主干等候选，在成本和资源门槛下选择 | 实际候选对象、返回的 chosen、实际 Steiner/MST 函数调用 |
| maze_wire.py / guarded_maze.py | A* 共享树；资源约束、接入点选择；候选可能先 compact | A* 父链、原算法选择记录；必要时追踪实际 compact 子图 |
| compact_guide.py | 在已有占用图中剪叶，存在环时通过 Dijkstra 父链选树，再按层输出走廊 | 真实 Dijkstra 回溯；成功返回时的 alive 集合及其内部 neighbors 邻接，标记 COMPACT_SELECTED_SUBGRAPH |
| layer_escape / tree_reattach / 其他直接矩形编辑 | 升层、重接和保留旧接触等几何转换 | 无完整已选路径观察链时保守降为 PATH_PROVENANCE_LOST |

A* 的原始 `path` 只在 connect() 的局部变量中存在，随后逐 GCell 调用 rect() 放进 rects；函数返回后父链和连边丢失。原始 Guide 只含矩形。Grid/Router 也会把真实折线转换成矩形，merge_corridors 合并共线区域。
wire、thermal、TSV relocate、congestion、tree、pin、maze 和 compact 等阶段都可能替换此前结果。final_router 的基线来自 baseline_v12/portable；随后 landing、buffered、wire、tree、pin、maze、compact 依次执行。每个候选先校验，失败或超时回退；buffered 还通过 choose_stage/accept_buffered 质量门槛。最终仅复制 current 指向的已接受 Guide。

## 观察隔离与存储

观察模块不返回替代几何，不修改输入集合、随机状态、候选顺序、权重、TSV 策略、优化顺序或运行预算。装饰器只调用原函数一次并原样返回结果。开关默认关闭；关闭时没有 Writer、数据库或路径文件。写入异常通过 fail-open 诊断并禁用观察，算法继续。

候选路径流式写入单个进程暂存文件，默认最多 32 MiB（同时受总预算的 1/8 限制）；仅实际选择的候选晋升为持久路径，未选候选保留摘要。已选候选、最终路径优先于大量被拒候选。搜索扩展单独受较低配额控制；最终记录设单 Net 100000 节点上限。JSONL 配合 SQLite 按 Net / Candidate / 字节范围索引，UI 单 Net 读取，搜索事件分页。最终路径文件上限另行预留，总预算包含保守索引开销计费；SQLite 页、WAL 等有格式开销，不能把该计费称为精确文件系统硬配额。

Trace 输出与 Guide 分目录，缓存为 ui_cache/v5_trace。本次完整记录目录约 {usage/1024/1024:.1f} MiB。磁盘不足、过滤、超限、缺失或无法核验均不补造路径。

## 使用

```powershell
python -B scripts/run_v5_trace_case.py --case toy --label toy_v5_trace --trace
python -B scripts/run_v5_trace_case.py --case toy --trace --trace-net vlink0 --trace-search --trace-max-mib 64
python -B scripts/run_v5_trace_case.py --case toy --label toy_v5_trace_off
```

新运行自动生成唯一 Label，保留历史文件。支持 native 和 wsl 参数；本次实测为本机 Python，WSL Trace 入口未实测。生产 V5 仍使用原 `scripts/run_v5_case.py`。本次没有启动大型 Trace。
''',
    'trace_format.md':'''# Trace 格式

每个 Run 的 `trace/` 目录含：

| 文件 | 用途 |
|---|---|
| capture-<process>.jsonl | 已记录的实际生成事件及候选/选择/发布摘要；流式追加 |
| capture.sqlite | 按 Net、Candidate、阶段、事件字节范围索引；候选对象身份、选择及 Guide 快照来源链 |
| route_trace.jsonl | 最终核验后的逐 Net 记录，每行一 Net；来源丢失时 nodes/edges 为空 |
| route_trace.index.sqlite | Net → 最终 JSONL off/len/行 SHA256；按需读取 |
| receipt.json | 最终 Guide SHA256、Trace SHA256、状态数量和记录上限 |
| diagnostics.jsonl | 发生观察错误时的诊断；不能写文件时写 stderr |

暂存 `_scratch-<process>.jsonl` 仅在进程运行时存在，正常退出后删除；不将所有候选或搜索数据加载进 RAM。

## 实际事件

公共字段为 schema_version、event、net、candidate_id、stage、process、accepted、provenance。实际几何事件还包含 a/b 或 node、gcell（A*）、kind、algorithm、source、origin。坐标单位为输入 DEF 的 DBU，layer 为原始层号 1～20，node id 是记录范围内的真实节点身份，不冒充不存在的原始 Router 全局 ID。

`astar_node` 和算法为 ASTAR 的 edge 来自 parents 回溯；primitive 来自真实 rect() 调用的原始参数，而不是 Guide 中心线；ACCESS_STACK 来自实际 stack() 操作。M10→M11 的 BUILD_TSV_CENTER 是 _build 的实际跨 Die 中心。compact 的节点/边来自实际内部 alive/邻接，而不是对输出 Guide 重新跑路径。

普通 Via 是同 XY、相邻金属层的实际边，TSV 是可证明的 M10↔M11 边。搜索开关开启才写实际 search_expanded/search_relaxed；配额可能截断，不能据此声称完整 Open/Closed 集历史。

## 最终行示例（结构）

```json
{"event":"final_route","net":"vlink0","guide_sha256":"...","geometry_sha256":"...","candidate_id":"...","accepted":true,"provenance":"FINAL_ACCEPTED_VERIFIED","origins":["REAL_ROUTER_PATH"],"nodes":[{"id":0,"x":100,"y":200,"layer":10}],"edges":[{"a":0,"b":1,"kind":"TSV","origin":"REAL_ROUTER_PATH"}],"algorithms":["PRIM_MST"],"sources":["actual stack() operation"]}
```

示例省略了另一节点，仅说明字段，不是样本几何。真实行中所有边端点必须存在。

状态 REAL_ASTAR / REAL_ROUTER_PATH 表示生成来源；FINAL_ACCEPTED_VERIFIED 表示当前最终 Guide 的接受和几何核验；INTERMEDIATE_ONLY 表示中间记录。PATH_PROVENANCE_LOST 禁止作为最终真实路径绘制；GUIDE_ESTIMATED 是独立、同层、虚线示意。

HTTP：`/api/net?id=toy/toy_v5_trace_release&net=vlink0` 返回核验后的边；`/api/trace?...&net=vlink0&after=0&limit=1000` 分页返回真实事件。next_after 是下一页游标；一次最多约 2000 条事件加一个索引块，不读取整份大文件。
''',
    'path_provenance.md':f'''# 路径来源与最终接受核验

最终有效条件同时包括：

1. Net 身份一致，候选由实际 _build/路由操作观察得到。
2. 原算法确实选择该候选对象；返回了新合并列表的 route_net 只允许唯一的根候选几何对应，不将 Adaptive 的子备选当作根候选。
3. 记录局部选择、种子继承、已校验 Guide 和 publish/replace 的来源链；快照身份包含 **输出路径 + 整份 Guide SHA256**，避免相同字节的不同阶段混淆。
4. final_router 最终 current 的快照被复制到最终输出；被拒阶段的快照不能进入此链。
5. 真实生成时记录的矩形足迹共线规范化后，必须与最终 Net Guide 精确相等；最终整份 Guide SHA256 亦必须一致。Via/TSV 边还检查相同 XY 和相邻层。
6. 路径记录完整、未因观察上限或 IO 失败而缺失。否则输出 PATH_PROVENANCE_LOST、空 edges，并给原因。

几何相等单独不能证明接受；任意一次 A* 成功也不能证明它属于最终结果。UI 不会将 rejected/intermediate 事件放进最终 edges；紫色回放叠层单独标为 INTERMEDIATE_ONLY。

## compact 的信息损失

原始 compact 会剪枝和重新选择子图，不能把其输入 A* 父链直接当作压缩后的最终路径。Trace 在该函数成功返回前记录实际 alive 节点及函数自身的 neighbors(alive) 边，所以这部分可作为 **其他算法的真实所选子图**，来源 COMPACT_SELECTED_SUBGRAPH / REAL_ROUTER_PATH，不能延续声称为原始 A*。
如果没有捕获完整子图、后续又改写几何或候选无法唯一对应，依然标来源丢失。merge_corridors 只合并共线矩形时通过完整足迹校验保留已有实际连接关系，不从合并矩形重建连接。

## 本次 Toy 结果

共 203 Net：**{len(accepted)} 个最终已核验，{len(lost)} 个来源丢失**。
其中 {len(astar)} 个最终记录含 REAL_ASTAR；其余真实生成来源包括 Manhattan HV/VH、MST 和主干/stack 操作。真实 Steiner 由独立四端子小型探针验证；本次 Toy 最终结果没有声称包含 Steiner。

最终算法标签计数（可重叠）：`{json.dumps(dict(algorithms),ensure_ascii=False)}`。
两条 TSV 已核验：vlink0、vlink1。普通 Via 从记录的 stack / A* 相邻层边展示。

`vlink2` 在基线 global relocate 输出首次失去当前实现可证明的候选身份链，之后没有重新获得完整来源。该优化会重新 merge 候选为新列表，当前严格身份匹配不将其自动猜配成旧候选。最终 Guide 本身合法，第三个 TSV 几何在三种完整运行中一致，但这不构成真实连边来源证明；该 Net 的真实最终 edges 为空。这是已明确暴露的 Trace 覆盖缺口，不是证明原算法没有真实路径。

其他可能降级点包括 layer_escape 直接矩形升层、tree reattach 拼接旧几何、手工替换接口矩形、超限/不完整记录、未覆盖的复制/发布。没有冒充这些阶段的完整精确路径。
''',
    'equivalence_tests.md':f'''# 算法一致性与小型验证

完整实测：Toy（203 Net），相同输入、同一 Python 环境、原始默认完整阶段和预算；原始控制在字节一致的独立副本运行，原始 v5/engine 不执行写入。

| 模式 | Label | 最终 SHA256 | 运行状态 | 耗时（本机观察值） |
|---|---|---|---|---|
| 原始 V5 控制 | toy_v5_trace_control | {hash} | ROUTED / 0 | {timings['original']['runtime_seconds']:.2f}s |
| Trace 开启 | toy_v5_trace_release | 同上 | ROUTED / 0 | {timings['on']['runtime_seconds']:.2f}s |
| Trace 关闭 | toy_v5_trace_off | 同上 | ROUTED / 0 | {timings['off']['runtime_seconds']:.2f}s |

Guide 字节、LOCAL_PASS 合法性、3 条 TSV 几何、各最终优化阶段接受结果和指标全部一致。最终 compact 线长代理为 25294800 DBU；不是官方评分器的 track 级 Wirelength。阶段接受：landing 是，buffered 否，wire/tree/pin/maze/compact 是。

另以 Toy 的真实端子构造隔离四端子 fanout 探针，分别执行真实 MazeRouter、Manhattan/MST、SharedTree/Steiner；三种模式的 Guide、合法性、wire proxy 和所有 heap push/pop 决策摘要一致。真实 A*、MST/Steiner 方法来源和最终路径已核验；关闭 Trace 不生成路径目录。写入失败、Net 过滤、文件上限及拒绝/几何失配降级测试通过。

单元测试：7 个 Trace 测试通过（包括损坏 Trace 回退 Guide、已有 Router 启动拦截）；11 个原 V5 UI 测试通过；原 V5 适配器 9 测试中 8 通过、1 个 Linux 专属测试在 Windows 跳过。原生产 2D/3D Canvas 回归通过，V4.5 1064 条真实边显示兼容性通过。真实 Chrome 测试另见 ui_validation.md。

**开销是真实存在的**：本机开启记录约 {timings['on']['runtime_seconds']/timings['original']['runtime_seconds']:.1f} 倍控制耗时，期间有开发检查，并非隔离性能基准。不能把“代码不改算法”说成“零运行时间影响”。原 wall-clock 预算没有暂停、扩展或改参数；大型输入若耗时接近预算边界，观察开销可能改变哪个阶段及时完成。未重跑大型 Case，因此不声称所有规模和截止条件下 Guide 恒等。建议分析时按 Net 选择性记录；正式比赛继续用原 V5。

机器证据：equivalence_result.json、probes_*、三个 Run 的 source_identity.json / run.json / router.log / route.run.json。早期开发运行亦保留，推荐使用 release Label，不将中间运行作为交付结果。
''',
    'ui_validation.md':f'''# UI 实测

UI：<http://127.0.0.1:8501/>。选择 toy → toy_v5_trace_release → vlink0。

生产 API 和真实无头 Chrome 均已测试，不只依赖 Canvas mock。截图为 ui_2d.png、ui_3d.png，并已人工图像核对：连续真实实线、折弯、普通 Via、橙色跨 Die TSV 可见；3D 层号为 M1～M20。vlink0 实际场景 {ui['scene3d']['paths']} 条同层边、{ui['scene3d']['vias']} 条 Via、{ui['scene3d']['tsvs']} 条 TSV，0 条不支持的跨层边。

通过项：自动按选中 Net 载入真实节点/边；保留原搜索、分页、聚焦、缩放、层筛选；2D/3D 使用同一核验数据；Guide 可独立关闭而实线保留；路径开关关闭同层线/Via；TSV 独立开关；独立 GUIDE_ESTIMATED 虚线；来源丢失 Net 不显示假实线；参考 Guide 保持无内部 Route 数据。

Trace 回放只叠加真实记录事件并标 INTERMEDIATE_ONLY。扩展/松弛事件只在 --trace-search 开启且实际记录时存在；未记录时只回放路径/候选事件，不伪造完整 A* 搜索。达到页尾可用下一步载入下一批，内存不累积所有页。

原始 toy_v5 仍禁用真实 Route 按钮、保留 Guide；已有 V4.5 `{ui['v45_checked']}` 可正常载入真实边。没有重新运行该大型用例。浏览器未出现 JS 异常。

发现并处理了两个旧 ui/app.py 同时占用 8501 的问题：只停止已核对命令行的旧 UI，保留新 UI；未停止或重启 Router。交付时 8501 仅保留新 UI。

证据：ui_api_result.json、ui_browser_result.json、ui_fixture.json、ui_2d.png、ui_3d.png。UI 的 N/A 资源和官方评分状态保持原语义；真实路径不冒充官方 DRC 或轨道级布线。
''',
    'final_conclusion.md':f'''# 最终结论

1. **可完整记录的真实路径**：A* 父节点回溯、实际 Manhattan HV/VH/ZX/ZY 线段、层间 stack、MST/Steiner/主干实际生成操作，以及成功压缩的内部选中 GCell 子图；不从 Guide 反推连边。
2. **来源区分**：REAL_ASTAR 仅指真实 A* 父链；Manhattan、MST/Steiner、stack 和 compact 子图标 REAL_ROUTER_PATH，并保留具体方法。独立 Steiner 探针已验证，Toy 最终不虚称 Steiner。
3. **最终接受核验**：Toy **{len(accepted)}/203 Net** 的来源链及足迹与最终 Guide 对应，其中 **{len(astar)} 个含真实 A***，vlink0/vlink1 的 TSV 已证明。vlink2 标 PATH_PROVENANCE_LOST，仅 Guide；第三条 TSV 仅能证明几何一致，不能证明路径来源。
4. **来源丢失阶段**：压缩原本会丢失输入路径连接身份；已新增内部子图观察，但不沿用 A* 身份。重新 merge 的 relocate 候选、未完整观察的直接矩形变换/拼接、IO 或上限截断仍可能降级；本次 vlink2 在 global relocate 开始降级。
5. **UI 已显示真实实线**：2D 和 3D 实测通过，含实际层号、Via、TSV；与 GUIDE_ESTIMATED 虚线分离。截图见 ui_2d.png / ui_3d.png。访问 8501，选择 **toy / toy_v5_trace_release**。
6. **开启 Trace 是否改变 Guide**：本次原始、开启、关闭三种 Toy 完整运行 Guide SHA256 完全相同：`{hash}`；合法性、TSV、阶段接受和指标一致，小型搜索决策摘要一致。记录增加耗时，大型截止预算条件未验证，不能保证所有规模的时限行为相同。
7. **原始 V5 79 文件**：全部 SHA256 不变；原始算法与正式入口保留。没有改写原始 Guide / DEF / LEF / 评分器，没有重跑 case001/003/005，没有执行 Git 写操作或上传。

已完成实际代码、完整 Toy 差分验证、真实 Chrome UI 验证及六份报告；不是仅把推导虚线改为实线。Trace 仍是可关闭的独立分析版本，原始 V5 继续作为正式版本。
'''}
    for name,content in documents.items():(out/name).write_text(content,encoding='utf8',newline='\n')
    print(json.dumps({'status':'PASS','reports':list(documents),'summary':summary},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
