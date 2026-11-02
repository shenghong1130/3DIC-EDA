# V3.5 比赛平台评分及预测产物分析

**数据来源：** `scoring_result.zip`（27 项）与 `prediction_result.zip`（5 项）；以下数据通过逐行读取评分报告、拥塞 CSV 和 TSV CSV 计算。报告不是新的评分器。

## 一、评分身份

- 本次评分模式：`score-only`，三个 Guide 均 `PASS`；总分 **45.39**，不是 Router 运行的成绩。`runtime_seconds=0` 代表已有 Guide 的评分场景。
- 平台 `scores.json.total_overflow=208863.67` 是三个 Case 的 **平均值**；各 Case Overflow **总和=626591**。
- 分项加权：质量分 = `0.45 * wirelength_score + 0.30 * congestion_score + 0.25 * thermal_score`。

| Case | Guide SHA256 |
|---|---|
| case001 | `3bbba02ce04b2ffca0dd43813b0438dee756e687a940d753556f294689fdf9dc` |
| case003 | `d66d23b29501bd521176a878b86521440bac0fe7ffdf78acc6cda23cf03b695e` |
| case005 | `cc424c9c4df389549c8fb8a6fe9af5270478308a02002679f1ad0052660b90d9` |

## 二、真实官方指标

| Case | 实际线长（µm） | Overflow | M1 | M10 | M11 | 热相关性 | 总分 |
|---|---:|---:|---:|---:|---:|---:|---:|
| case001 | 426,100.5 | 1,749 | 981 | 29 | 739 | +0.001688310 | 48.53 |
| case003 | 12,728,820.0 | 177,760 | 971 | 80,760 | 89,501 | +0.041540026 | 51.03 |
| case005 | 22,201,333.5 | 447,082 | 175,416 | 55,092 | 215,144 | -0.068547526 | 36.61 |

case003 的 M10/M11 占总 Overflow **95.78%**；case005 的 M1 占 **39.24%**、M10 占 **12.32%**、M11 占 **48.12%**。

## 三、TSV 拥塞证据

| Case | TSV | TSV 占用独立 GCell | 重复占用的 GCell 数 | 同格最多 TSV | 与 TSV 同 XY 的 Overflow 比例 |
|---|---:|---:|---:|---:|---:|
| case001 | 657 | 653 | 4 | 2 | 10.06% |
| case003 | 22,574 | 17,190 | 3,983 | 8 | 95.75% |
| case005 | 39,542 | 30,360 | 7,135 | 18 | 45.19% |

特别注意：“与 TSV 同 XY 的 Overflow”是空间共址统计，不是因果归因；可能包含其他金属层和 Pin/OBS 需求。

- case003：`(335,369)` 共有 8 个 TSV；M11 `demand=121 / capacity=15 / overflow=106`。`(570,343)` 亦有 8 个 TSV；M10/M11 各有 105 Overflow。
- case005：`(292,291)` 共有 18 个 TSV；M11 `demand=270 / capacity=15 / overflow=255`。`(719,318)`、`(818,1022)` 各有 14 个 TSV，M11 Overflow=195。
- case005 的 TSV 格以外，M1 仍产生 **175416** Overflow：必须分开研究 M1 层路径需求。

## 四、从已公开的原始报告验证分数关系

- **线长分**：`100 * hpwl_reference_dbu / total_dbu`，对三个 Case 均与官方报告的 wirelength_score 精确一致。若新 Guide 的官方代理线长无法本地精确复现，则不能认为可精确预测新分数。
- **热优化分**：`50 * [1 + tanh(saturation_gain * correlation_above_ambient)]`；三个 Case 均精确一致，平台报告中的 gain 分别为 `11.684`、`4.224`、`8.323`。相关系数分别为 `+0.00168831`、`+0.04154003`、`-0.06854753`。
- **拥塞分**：缺少官方完整公式。本次报告仅提供分数、总 Overflow、各 GCell `demand/capacity/overflow`；不能凭这三个样本推断可泛化的百分制公式。
- `scores.json.thermal_correlation=0.0` 是顶层汇总；应优先使用 `cases[...].raw_report.thermal_tsv.correlation_above_ambient` 的每 Case 原始值，特别是 case005 为 **负数**。
- 每个 evaluation_report 的 Fidelity 说明“mock-DRT risk proxy”，不能把 Guide PASS 当作详细布线 DRC signoff。

## 五、下一步行动

1. 先保存平台三个 Guide SHA256 和 `scores.json` 为 V3.5 官方基准，勿覆盖旧报告。
2. 优先做 V3.6 `跨 Net TSV 全局分散` 原型：惩罚同 GCell 复用 TSV；目标 case003 的 3983、case005 的 7135 个重复 TSV GCell，尤其超高 demand 热点。需保持合法性、连通性，并考虑 Wirelength/Thermal 取舍。
3. 独立研究 case005 的 M1 175416 Overflow，定位高 Pin Net 与固定需求；避免只优化 M10/M11。
4. case005 热相关性为负，建议在不增加 TSV 簇拥塞的条件下，将 TSV 密度向较高温区域分布。官方热分在 `correlation=0` 时对应 50 分，不能保证通过调 TSV 达到。
5. 只使用相同评分口径与相同输入比较候选 Guide；先小型实例与 case001，再 case003、case005；平台最终复评分是唯一真实成绩判据。

## 六、逐 Case 官方最大热点（前五个）

### case001
| Layer | col | row | Demand | Capacity | Overflow |
|---|---:|---:|---:|---:|---:|
| metal11 | 128 | 74 | 31 | 15 | 16 |
| metal11 | 147 | 164 | 30 | 15 | 15 |
| metal11 | 124 | 57 | 30 | 15 | 15 |
| metal11 | 106 | 76 | 30 | 15 | 15 |
| metal10 | 124 | 57 | 16 | 8 | 8 |

### case003
| Layer | col | row | Demand | Capacity | Overflow |
|---|---:|---:|---:|---:|---:|
| metal11 | 335 | 369 | 121 | 15 | 106 |
| metal11 | 570 | 343 | 120 | 15 | 105 |
| metal10 | 570 | 343 | 120 | 15 | 105 |
| metal10 | 335 | 369 | 120 | 15 | 105 |
| metal11 | 404 | 367 | 108 | 15 | 93 |

### case005
| Layer | col | row | Demand | Capacity | Overflow |
|---|---:|---:|---:|---:|---:|
| metal11 | 292 | 291 | 270 | 15 | 255 |
| metal11 | 818 | 1022 | 210 | 15 | 195 |
| metal11 | 719 | 318 | 210 | 15 | 195 |
| metal11 | 269 | 364 | 195 | 15 | 180 |
| metal11 | 729 | 983 | 180 | 15 | 165 |

## 七、边界

本次原始 ZIP 仅包含已提交 Guide 和单次评分输出，不包含完整官方评分器程序、新候选 Guide 的评价能力、逐 Net 拥塞因果归因或 Router 的正式运行性能。
