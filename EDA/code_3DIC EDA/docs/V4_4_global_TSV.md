# V4.4 全局 TSV 联合迁移开发记录（未验证候选）

## 目标及基线

基于 main (2026-11-05) 中的 V4.3，单独编译 `router_v4_4`；不改旧版二进制、评分器、历史 Guide 或主分支。
配置 `configs/v4_4.json` 沿用 V4.3，以便做可归因的 A/B。

V4.3 已有单 Net TSV 搜索（自适应候选/区域调度），但仅凭单 Net 的正向拥塞收益会拒绝需要两条 Net 同时让路的迁移。本候选增加小规模局部 *pair transaction*，不是全局最优求解器。

## 实际新增算法

1. 每个区域（16×16 GCell）汇总跨 Tier Net，优先处理 TSV 重叠较多的区域。
2. 每组最多两个 Net；每个 Net 以半径 1/2/4 搜索、保留最多三处合法 M10 候选。
3. 使用完整 TSV footprint 的正容量归一化边际拥塞代理排序候选，限制成对 trial 数。
4. 通过 V4 的 `v4_local_tsv` 在两侧构造完整的新连接，采用两 Net 的原子占用回滚，必须验证 Pin、TSV、完整 Route、Guide、Via 和所有物理资源。
5. 同一拆除基线比较完整资源需求差分，要求 joint Overflow 不上升且 phi 严格下降，同时检查热 Pearson 下限、全局参考线长和单 Net 长度预算。
6. 在 V4.3 Adaptive TSV 之后、Thermal refinement 之前调用。候选阶段硬限制 12 秒、最多 24 trial，保留其他阶段及输出预算。

## 当前状态

- 已在 GitHub 独立分支提交源码、CMake target、配置和基础 smoke test。
- **尚未编译、尚未运行 CTest、未跑 case001/003/005、未做参考合法性复核，未获得任何 V4.4 的真实拥塞成绩。**
- 不应把本候选称为 PASS 或 RELEASE，也不能据此提交比赛 ZIP。
- 不保证双 Net 原子重布一定优于 V4.3：绝大多数 Net 可能被局部搜索或线长保护拒绝。

## 在本地核实

```bash
cmake -S . -B build_v44 -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON
cmake --build build_v44 --target router_v4_4 v44_regression v43_regression -j2
ctest --test-dir build_v44 --output-on-failure -R 'v44_regression|v43_regression'
```

在 WSL 中运行、比较时保持 V4.3 和 V4.4 输入、评估程序和资源条件一致，使用**不同的** Case Label。
正式实验前先运行 toy/sample001 与 case001，保存 Guide SHA256、参考模型、Overflow、phi、Wirelength、Pearson、运行时间、RSS、TSV 位置变化、拒绝原因和独立合法性审计。case003/005 需要单独内存及时间资源检查；不要干扰现有长时间运行。

## 审核重点

- 双 Net 回滚在第二次安装失败、抛出 bad_alloc、超时后能完整恢复所有 usage 和 TSV。
- `v44_try_tsv_pair` 只对完整 Route 增量做接受判定；候选排序不是最终判断。
- 新功能仅在 `GR_V4_4` 下开启；V4.2/V4.3 编译与运行语义不变。
- 严禁修改官方评分器或压低 Fixed/OBS/Pin 需求。
