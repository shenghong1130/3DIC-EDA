# V5 Trace — 本地路径分析版本

原始 `v5/engine/` 的 79 个文件不变；这里的 `engine/` 是独立副本。
正式比赛继续使用原 V5，Trace 默认关闭。

```powershell
python -B scripts/run_v5_trace_case.py --case toy --label toy_v5_trace --trace
python -B scripts/run_v5_trace_case.py --case toy --trace --trace-net vlink0 --trace-search --trace-max-mib 64
```

入口会检查已有 Router/scorer，避免重叠启动；不会终止任何已有进程。
Windows 大型运行需使用 `--backend wsl` 并通过原资源门槛。本次仅运行 Toy。
相同 Label 已存在时自动分配新 Label，运行回执、源码身份、日志、Guide 和 Trace 各自保存。

UI：<http://127.0.0.1:8501/>，选择 **toy / toy_v5_trace_release**。

仅 `FINAL_ACCEPTED_VERIFIED` 是真实最终实线。`REAL_ASTAR` 来自父节点回溯，
其他实际操作为 `REAL_ROUTER_PATH`。中间候选是 `INTERMEDIATE_ONLY`；
无法核验时为 `PATH_PROVENANCE_LOST` 并保留 Guide；`GUIDE_ESTIMATED` 是独立虚线。
只有记录的搜索事件能回放，不补造完整 A* 搜索。

最终 Toy：202/203 Net 核验通过，16 个含真实 A*，两条 TSV 来源可证明。
vlink2 来源丢失；三种运行的最终 Guide SHA256 一致。Trace 有明显时间开销，
大型时限下是否仍保持相同 Guide 尚未验证。

详见 `outputs/reports/v5_trace/final_conclusion.md` 及同目录其他五份报告。
