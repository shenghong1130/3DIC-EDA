# REFERENCE_20261029

这是本项目自行实现、可重复使用的参考评价器，**不是比赛官方评分器**。它不调用或修改 `EDA/评估程序/score`，也不读取 Router 的 `result.json`。

## 使用

在工程目录的 PowerShell 中执行：

```powershell
python scripts/evaluate_reference.py --case open_3d_case001 --label case001_v3_4 --backend wsl
```

默认输出 `outputs/reports/reference_20261029/<case>/<label>/`。已有目录只在输入、Guide 和参考源码身份完全一致时复用，否则拒绝覆盖。编译文件、几何外部归并条带使用独立临时目录，正常结束及 C++/Python 异常退出均清理；进程被不可捕获的 SIGKILL/主机断电终止时，操作系统临时目录可能留下条带，不能宣称此时也保证清理。

仅检查真实输入的固定资源规模，不运行 Router：

```powershell
python scripts/evaluate_reference.py --case open_3d_case005 --model-only --memory-mb 1536 --timeout 600
```

该命令不计算 Guide、连通性或正式成绩。普通参考评价默认 Linux 地址空间上限 1536 MiB，可指定 256～8192 MiB；外层有超时，报告含 `getrusage` 峰值 RSS。超过预算记分析错误，不能记为 Guide 不合法或官方 FAIL。

## 规则矩阵

依据为本地原始评分说明（文件名保留 `20291011`）、20261029 文档中完整包含的 20261020/27/29 更新、20261027 独立说明及《赛题问题与解答汇总》。后续更新优先于旧说明。第三方兼容评分器未作为依据。

| 规则 | 文档依据 | 已实现 | 未确定或模型约定 |
|---|---|---|---|
| Guide 语法、Net 身份、层号、正面积 | 原始说明 §2、20261029 第1项 | 直接解析 Guide；未知/重复 Net 拒绝；缺失 Net 报错 | 解析错误是分析错误，非官方 FAIL |
| GCell 对齐与首选方向 | Q&A Q42～Q47、20261020 | 网格对齐；非偏好方向不能跨格；单格拼接不能形成非偏好方向连接 | 采用裁剪到 DIEAREA 的 GCell 边界，完全不许越界，比旧“只需与 DIEAREA 相交”保守 |
| Pin 接入与连通 | 原始说明 §2.1、Q41/Q45/Q46、20261029 第3项 | 实际方向变换后的几何，合法 Via、首选方向图遍历；不依赖单一 Pin 中心 | Pin 任一实际 Geometry 接入视为该终端接入；未还原未发布程序的全部细节 |
| 普通 Via | 原始说明 §2、Q50 | 只允许 LEF 声明的相邻层同位置连接 | Guide 不是详细布线；不声称制造规则 sign-off |
| 唯一 TSV | 原始说明 §2.2、Q36/Q37/Q40 | M10/M11 正面积重叠；最多两个可消歧候选；每跨 Die Net 最终选一个 TSV | 两候选按接口 Pin 中心消歧的细节沿用本地旧说明，待新程序确认 |
| TSV 禁入 | 20261029 第4项 | 真实所有 Routing Layer 的 Pin/OBS 几何并集，禁止中心 GCell；无固定 Pin 例外 | 中心格与完整 5µm 外框邻格的禁入范围尚未明确；报告声明此假设 |
| Capacity | DEF TRACKS、20261020、Q50/Q54 | 按实际首选方向 Track 点；半开格归属，最外边界闭合 | 精确边界约定需新官方程序校准 |
| Pin/OBS 固定需求 | 20261020/27/29、Q58～Q60 | 实例方向、每 Geometry 实际层、DEF IO、Track 集合并集、无 Track Pin 最少一轨 | 无 Track Pin 选择最近格内 Track；零容量格共享虚拟轨，此合并语义未公开 |
| Guide 与 TSV 需求 | 20261020/27、Q48 | 每 Net 格层去重；自身 Pin 格抵扣；5×5µm TSV 在 M10/M11 实际占轨 | 同 Net Guide/TSV 用 max 合并；TSV 覆盖但无轨时最少一单位是参考约定 |
| Wirelength | 原始说明 §3.1、Q54～Q56、20261020 | 去重格层尺寸 + 相邻 Guide 重叠费用 | Via/TSV 3/6 来自 SHA 固定的旧官方 ELF 默认值；矩形拆分计费细节保留假设 |
| Wirelength Score | 原始说明、20261020、Q51～Q55 | 新平台公式已知；输出 Pin 2D HPWL 下界供诊断 | 未独立实现说明中的精确 FLUTE 三维参考基准，因此 Score=N/A；不拿 HPWL 冒充 FLUTE |
| Congestion | 20261029 第2项 | 逐格层 Demand、Capacity、Overflow；输出 Σ(Overflow/max(1,Capacity))² 中正容量部分与零容量需求格数 | 新惩罚公式未公开，Congestion Score=N/A；零容量格不偷偷塞入猜测的百分制公式 |
| Thermal | 原始说明 §3.3、Q66～Q72 | 原始热网格；按 XY 先平均 Z，再格内平均；温升与 TSV 密度 Pearson | 横向、纵向和反向填充顺序是明确标注的模型约定；`G_case` 未公布，Thermal Score=N/A |
| 总分 | 原始说明总览、Q62～Q65 | 保留来源和未知项 | 分项不完整、排名折算未知，Total Score=N/A |

## 独立性与资源控制

`main.cpp` 只链接输入解析器 `src/parser.cpp`；没有链接 Router 或搜索/布线代码。评价输入仅为 LEF、DEF、Thermal、Guide。

`include/physical_resources.hpp` 是参考评价器和 V3.5 共用的几何资源内核，因此二者一致不能替代独立验证。`tests/physical_resources_tests.cpp` 用不同的逐 Track 点集合 Oracle 验证随机几何、并集、最少一轨、条带边界及内存预算；`tests/test_reference_scorer.py` 用人工 DEF/LEF/Guide 验证合法性负例、去重、抵扣与重复确定性。

几何按层和最多 64 行分桶写入临时文件，再用有界 Track 位图逐桶合并；不保留全部格区间，不设 2M 总区间上限。位图工作区默认最多 32 MiB，临时输出流最多 64 个。总体内存还包括 DEF/LEF 设计对象、固定上限的格层需求数组、逐 Net 工作区和热 XY 聚合；不把 32 MiB 错称整个进程的峰值。

`resources.csv` 是逐格逐层的**稀疏完整表示**：包含固定或布线需求非零的资源。未列出的格层需求为零，Capacity 可由 `capacity_axes.json`、网格及方向恢复；报告含每层全网格 Capacity 总量。避免大型 Case 输出数千万条无需求记录。

`reference_scorer_sha256` 是实际编译 ELF 的 SHA256；`reference_source_sha256` 是源码依赖清单的内容 SHA256。`invocation.json` 另记全部输入、Router、Config、Guide 身份。参考合法性为 `REFERENCE_VALID / REFERENCE_INVALID`，绝不写作官方 PASS/FAIL。无效 Guide 的资源/热指标仍可诊断，但不能参与“合法优化获益”的结论。
