# 阶段一：自动化参数实验

本目录实现“生成配置 → 顺序运行既有 Router 管理器 → 成功后官方评分 → 校验记录 → 分析与画图”。不修改 C++ 算法、默认配置、正式脚本、UI、评分器及官方输入。交付时仅生成计划、12 份配置、PENDING 状态和空状态报告；真实仿真必须由用户执行运行命令后开始。

## 1. 目录与依赖

```text
parameter_tuning/
├── tune_parameters.py          # CLI、顺序调度、状态恢复
├── experiment_config.json      # 公共预算、监控设置、OAT 参数变化
├── support.py                  # 原子持久化、进程锁、命令、指纹
├── records.py                  # 真实官方 Schema、只读有界结果提取
├── analysis.py                 # CSV/JSON、对照、Pareto、建议
├── plotting.py                 # 离线 Matplotlib PNG
├── requirements.txt
├── README.md
├── configs/phase1_case001/      # baseline 及另外 11 份实验配置
├── reports/phase1_case001/
│   ├── experiment_plan.json    # 不可变：版本、输入、环境、配置、首个 Label
│   ├── experiment_state.json   # 原子更新：状态、全部尝试、命令、文件哈希
│   ├── results.csv
│   ├── results.json
│   ├── ranking.csv
│   ├── analysis.md
│   ├── second_round_recommendations.json
│   ├── figures/               # 五张必需图 + 分项敏感性图
│   ├── attempts/<label>/      # 管理脚本 stdout/stderr
│   ├── extracted/<label>.json # 校验后的派生提取缓存（首次真实运行后）
│   └── best_config.json       # 仅满足严格选择条件后出现
└── tests/test_parameter_tuning.py
```

真实布线、监控及评分仍由原脚本保存在 `outputs/runs/<case>/<label>/`，UI 不需要修改即可发现这些 Run。本目录不会移动、删除或覆盖历史 Run。`best_config_provenance.json` 在有最佳配置时记录其 Label、真实总分及 Guide/配置/计划哈希；推荐失效时保留到 `superseded/`，不继续展示为当前最优。

运行管理与分析使用 Python 标准库，画图依赖 Matplotlib（包含其正常传递依赖）。当前 Windows Python 用户环境已安装并验证，其他环境安装：

```powershell
python -m pip install --user -r parameter_tuning/requirements.txt
```

Matplotlib 使用 Agg 与英文标签，可离线生成 PNG，不需要浏览器、CDN、GPU 或中文字体。`records.py` 只读复用现有 `ui/result_store.py::Cursor`，逐 Net 提取与跳过大数组，不启动 UI，也不构建 SQLite 缓存。该依赖的哈希同样记录在计划中。

## 2. 参数作用与控制变量

Baseline 每次新建批次都读取真实 `configs/default.json`，校验完整字段和 C++ 约束，不从 README 示例拼出默认值。首轮固定 wire=1、tsv=4、stagnation=2、search_limit=1500000、trace_mb=32；默认值与固定要求不同会拒绝创建，避免无意换基线。

| ID | 唯一改变 | 源码作用与生效边界 |
| --- | --- | --- |
| baseline | 无 | 与原 default.json 的解析值完全一致 |
| via_low / via_high | via=0.25 / 1 | 改变 MST 的层差权重，也改变 A* 层间边和启发式；不是只改 Via 数量 |
| congestion_low / congestion_high | congestion=2 / 8 | A* 的归一化溢出平方惩罚；TSV 候选的界面需求惩罚 |
| history_low / history_high | history=0.5 / 2 | A* 历史项；仅拥塞重布产生非零历史后才有作用 |
| thermal_off / thermal_high | thermal=0 / 3 | 非固定 TSV 候选的冷点惩罚；不是普通 A* 每条边的温度项 |
| iterations_off / iterations_high | iterations=0 / 4 | 最大优化轮数；Overflow 已为零或达到 stagnation 会提前停止，未必实际执行四轮 |
| margin_high | margin=16 | 初始搜索包围盒；失败后扩大，不是直接进行无界全图优化 |

`tsv` 在同一个 Net 的候选代价中为相同常数，通常不改变候选排序；如果已有唯一 M10/M11 公共 Pin 格，TSV 直接固定，thermal/congestion 也不会重选该锚点。接受准则仅为局部 Overflow 严格下降，其他指标改善并不保证被接受。以上为源码事实，具体效果仍需实测。

每份配置生成后检查真实差异：OAT 非 Baseline 必须只改变声明的一个字段。`time_limit` 保留原配置值，所有候选共用 `--router-timeout`；正式脚本传入 `--time-limit` 会覆盖 JSON。计划、CSV 和分析报告同时保存两者。

默认不开 Trace，以减少无关开销；监控统一为 progress_every=1000、采样 5 秒、heartbeat=10 秒、stall_after=30 秒。监控设置可在新实验的 plan-config 中修改，不能在已冻结的批次中途更换。

## 3. PowerShell 完整操作

```powershell
Set-Location -LiteralPath 'D:\我\大学\program\3DIC EDA\EDA\code_3DIC EDA'
python parameter_tuning/tune_parameters.py --help
```

预览并生成 12 组配置、计划和空报告，不启动 Router/Evaluator：

```powershell
python parameter_tuning/tune_parameters.py --case open_3d_case001 --experiment phase1_case001 --dry-run
```

已有同名计划只校验，不改写不可变计划和配置。已生成的图表只有空状态，不代表任何实验成绩；`best_config.json` 不会提前出现。

**以下命令会启动真实实验，交付时未执行：**

```powershell
# 本次最多启动两个尚未执行的 Router：baseline、via_low
python parameter_tuning/tune_parameters.py --case open_3d_case001 --experiment phase1_case001 --max-runs 2

# 跳过已校验的完成项；继续其余尚未运行项
python parameter_tuning/tune_parameters.py --case open_3d_case001 --experiment phase1_case001 --resume

# 也可以每次续跑最多两组新 Router
python parameter_tuning/tune_parameters.py --case open_3d_case001 --experiment phase1_case001 --resume --max-runs 2
```

预算必须在创建新批次时明确设置，续跑可省略以继承冻结值，不能同名改预算：

```powershell
python parameter_tuning/tune_parameters.py --case open_3d_case001 --experiment phase1_case001_1800 --router-timeout 1800 --evaluation-timeout 1800 --backend wsl --engine latest --dry-run
python parameter_tuning/tune_parameters.py --case open_3d_case001 --experiment phase1_case001_1800 --max-runs 2
```

只重新读取已有数据、更新报告，不启动 Router/Evaluator：

```powershell
python parameter_tuning/tune_parameters.py --case open_3d_case001 --experiment phase1_case001 --report-only
# 同一功能的分析入口
python parameter_tuning/analysis.py --experiment phase1_case001
```

## 4. 运行、评分与公平性

调度器用 `subprocess.Popen(argv, shell=False)` 调用原工具；中文/空格路径为独立 argv 元素，不自行拼接 shell 字符串。WSL 转义、Linux Supervisor、Windows HOST 监控全部复用正式脚本。

实际命令在 dry-run 与 state 的每个 attempt 中记录，大致为：

```powershell
python -B scripts/run_case.py --case open_3d_case001 --backend wsl --label <唯一标签> --router <LinuxRouter路径> --config <实验配置路径> --time-limit 600 --progress-every 1000 --monitor-interval 5 --heartbeat-interval 10 --stall-after 30
python -B scripts/evaluate.py --case open_3d_case001 --label <同一标签> --backend wsl --engine latest --timeout 600
```

上面含占位符，只解释调用结构；直接复制运行请使用第三节完整 CLI。唯一标签满足原 `safe_label`，包含实验名、候选 ID、批次随机 ID、尝试序号及随机后缀，长度不超过 80。碰撞时分配新标签。评分只在本次 Router 的完整 ROUTED/返回码 0、Guide/result 都存在且元数据校验通过后启动。

计划冻结 DEF/LEF/Thermal、Router、Evaluator 及其表/库/脚本依赖、正式管理器、C++ 源码、默认配置和结果 Reader 的 SHA256，记录 Windows Python/平台、Ubuntu/kernel/libc/Python、监控与 CLI 预算。每次 Router 和评分前后检查；不一致会 BLOCKED，并停止后续实验，不混合版本排名。新版本/新预算/新输入应使用新实验名。

计划保留初建时的调参代码指纹；每次实际辅助调用另记 `manager_source_sha256`，报告另记 `analysis_source_sha256`，因此报告和管理器自身的版本也可追溯，不需要改写旧计划。

真实实验开始前还检查其他历史运行的状态：发现外部 `RUNNING` 记录时暂停，避免同时启动另一个 Router。若是未最终落盘的旧清单，也须先根据其诊断核实，不自动改写状态或停止该进程。dry-run/report-only 不受此限制。

latest 使用原 `评估程序/score`；legacy 使用原配套 Python/CPython 3.10 `.so`。计划记录整个所用引擎文件集合的哈希，不能把 legacy 结果当作 latest。Windows native 官方评分不可用，Windows 管理入口应使用 WSL；Linux 主机才适合 native。

## 5. 状态、暂停与恢复

状态为 PENDING → ROUTING → ROUTED → EVALUATING → COMPLETED，另有 ROUTER_FAILED、EVALUATION_FAILED、INTERRUPTED、BLOCKED。每次变化先原子持久化；临时 JSON flush/fsync 后替换。操作系统文件锁防止两个同名管理器，也阻止本系统两个批次同时启动 Router；锁在进程退出时释放，不依赖旧 PID 文件判断锁有效性。

- `--max-runs N` 只计本次启动的新 Router。恢复已有完整 ROUTED 结果的未开始评分不重复布线，也不消耗此名额。
- 普通搜索预算/布线失败或官方 FAIL/TIMEOUT 保留并继续其他候选；UNKNOWN、WSL 退出、OOM/信号、环境启动等异常默认暂停，不强判 OOM。
- Ctrl+C 保留状态与日志，给本次 Router 写取消请求；不调用 `wsl --shutdown`，不杀无关进程。辅助进程可能仍在收尾；恢复前检查 PID、监督租约或评分超时窗口，不能立即另起重叠实验。
- `--resume` 核对身份、配置、输入、工具和已封存产物哈希。ROUTED 且没有评分目录时可继续评分；部分 `official_*` 目录只保存现场，不直接重跑覆盖。
- Router 失败、评分失败与中断默认不重试。显式重试使用全新 Label，重新完成 Router 和评分，旧 attempts 和所有旧 Run 都保留：

```powershell
python parameter_tuning/tune_parameters.py --case open_3d_case001 --experiment phase1_case001 --resume --retry-failed --max-runs 1
```

管理器帮助日志在 `reports/<experiment>/attempts/<label>/`；C++/HOST/监督、Guide、结果与评分证据在原 `outputs/runs/<case>/<label>/`。可在另一 PowerShell 查看实际运行的进度：

```powershell
# 将 <label> 替换为控制台打印的真实标签
Get-Content -LiteralPath 'outputs/runs/open_3d_case001/<label>/progress.log' -Tail 20 -Wait
```

## 6. 如何读取分析

`analysis.md` 固定九节：概览、配置、完成情况、官方评分、运行效率、敏感性、最优参数、限制、下一阶段建议。`results.csv` 为 Excel 兼容 UTF-8 BOM，缺失值 N/A；JSON 中为 null。

采集官方真实 `score.*_score`、Wirelength DBU/μm、总/最大 Overflow、Pearson、TSV；Router wall/C++ runtime、峰值与来源、Evaluator runtime/内存、迭代实际执行/接受数、阶段耗时、版本/输入/配置/Guide 哈希、故障与路径。大 result 只保留当前 Net 的临时 JSON，提取结果缓存在本目录，不再次构造完整大数组。

复用 Reader 的块大小为 1 MiB，单个 JSON 值上限 64 MiB；遇到超大单 Net 会明确报告提取失败并排除排名，不截断数据，也不把解析失败当成完整结果。

当前 latest 报告的 `score` 是字典，只有三项分数和 weights，没有总分。只认 `total_score` / `overall_score` / `final_score` / 明确 total 字段或数值型 score，不根据 weights 或文档公式自行补成“官方总分”。官方 FAIL 即使附带分数也不能成为合法候选。

- `delta_*` 为相对本批合法 Baseline 的绝对差；`percent_*` 为除以基准绝对值后的百分比。缺失或基准为零时百分比为 N/A。
- Guide 字节相同有哈希与明确标记；TSV Net→Node 映射与 GCell 密度另作哈希，不能将相同 Guide 宣称为质量改进。
- 仅完整、同版本的 PASS 进入候选。总分缺失时 ranking 的 rank=N/A，只报告子项冠军、最快合法配置及完整共同目标的 Pareto 集，不虚构唯一综合冠军。
- 至少两个完整可比较 PASS（包括 Baseline）且候选都有真实总分，才写 best_config.json；它只代表当前已观测配置，不证明未运行候选或隐藏 Case 的最优。
- 单次 OAT 的差异不是统计显著性；零差异、质量/运行成本冲突、迭代提前终止都要保留。推荐最多五个有观测依据的组合，数据不足时明确不推荐。不会自动启动第二轮。
- `stage_seconds` 内含嵌套/抽样阶段，不能全部相加或把抽样 A* 时间当成完整 A* 成本。以实际阶段证据判断瓶颈，不能仅凭权重变化断定算法收益。

五张必需 PNG 均生成；总分不可用时总分/总分敏感性图显示空状态，另有 `parameter_component_sensitivity.png` 展示可用分项差异。FAIL 和缺失分数不会画成 0 分有效柱子。

## 7. 扩展批次、组合与大型 Case

第二轮：复制 experiment_config.json 为本目录内新 JSON，新增 suite（`mode: "combinations"`）与明确 changes，再指定新实验名、`--plan-config` 和 `--suite`。只允许支持的六种研究字段，原默认配置不改；第一项仍必须是空变化 Baseline。`second_round_recommendations.json` 只是研究假设与来源，不自动变成运行计划。

大型 Case 只在用户显式选择时创建/执行，默认始终 case001。以下先预览，不启动布线：

```powershell
python parameter_tuning/tune_parameters.py --case open_3d_case003 --experiment phase1_case003 --router-timeout 1800 --evaluation-timeout 1800 --dry-run
python parameter_tuning/tune_parameters.py --case open_3d_case005 --experiment phase1_case005 --router-timeout 1800 --evaluation-timeout 1800 --dry-run
```

case005 的 Thermal 名称与 Case 不一致，保留原始输入且默认不允许真实实验。来源核验后必须提供可审计说明（不验证说明真假，责任仍在来源核验）：

```powershell
# 仅在已经核验热文件来源之后手动执行；这里的说明必须换成实际核验依据
python parameter_tuning/tune_parameters.py --case open_3d_case005 --experiment phase1_case005 --confirm-thermal-source '填写真实来源、核验过程和证据位置' --max-runs 1
```

超时预算是限制，不承诺能完成。case003/005 的单 Net/热解析等原有内存风险不由调参系统消除。

## 8. 测试与排错

```powershell
python -B -m unittest discover -s parameter_tuning/tests -v
```

测试使用隔离临时目录、Mock 子进程和小 JSON 夹具，不运行真实 Router/评分器，不将夹具结果写入正式实验或历史 outputs。另行实际检查 CLI help/dry-run/report-only 与 PNG；本次没有真实参数仿真。

| 问题 | 处理 |
| --- | --- |
| 计划或预算不同 | 同批冻结，恢复时继承原值；研究新设置使用新实验名 |
| 配置/工具/输入哈希变化 | 停止比较与继续执行；保留旧批次，新版本另建批次 |
| 没有 best_config.json | 总分缺失、Baseline 未 PASS 或完整比较不足，这是正确的空状态 |
| reports 有部分 official 记录 | 保留现场，显式 retry 用新 Label；不能删除旧目录来绕过保护 |
| 评分失败 | 查看官方 invocation/evaluation_report/evaluator.log，ROUTED 不是 PASS |
| 另一个管理器持锁 | 等该进程退出；不要删除锁文件去规避运行中的锁 |
| WSL/环境异常 | 修复环境后按原版本恢复；不自动重启/关闭 WSL |
| helper PID 或评分窗口仍活动 | 等已有进程收尾/超时，再恢复；不启动重叠实验 |
| 图表依赖缺失 | 在当前 Windows Python 安装 requirements.txt；不需要 Ubuntu 安装 Matplotlib |
| 看不到新 Run | UI 刷新列表；仅完整 ROUTED 结果可供完整绘图，失败记录仍保留 |

参赛打包脚本仍按原规则，只打包正式 Router/指定 docs；调参目录不会被默认打入提交 ZIP。推荐配置也不会自动成为提交默认参数，后续应另做验证与明确集成。
