# 阶段一参数实验平台交付记录

代码已完成。仅新增 parameter_tuning 目录，既有 scripts、C++、默认配置、UI、官方工具和输入均未修改；没有 commit/push。

验证：28 项轻量测试全部通过；help、两次 dry-run、report-only、独立 analysis 入口均退出码 0。
六张 PNG 均验证文件结构，另实际查看总分与分项敏感性图；当前为空状态，没有编造实验成绩。

## 工作方式

从真实 default.json 生成 Baseline 与 11 个控制变量配置，检查唯一差异；冻结输入、Router、Evaluator、相关依赖、监控、环境和预算哈希。手动运行后，按顺序调用原 run_case.py；仅成功且完整的 ROUTED 才调用同 Label 的 evaluate.py。
结果从真实 run/invocation/evaluation_report 提取；Guide 与 TSV 映射/密度哈希可对照相同输出，迭代执行/接受数及主要阶段耗时用于区分参数理论作用与实测效果。
原子状态文件、操作系统锁、独立 Label、校验后恢复；普通失败保留并继续，基础环境/版本/活动进程异常暂停。显式 retry 用全新 Label。完成记录哈希不一致不能在第二次恢复中重新封存成成功。

## 当前结果与限制

- phase1_case001：12 PENDING、0 attempts；未启动任何真实参数布线或评分，未执行完整 12 组扫描。
- 当前官方 Schema 有三项子分数，没有总分字段；保留 N/A/null，报告分项与 Pareto，不使用 weights 合成总分。
- best_config.json 未提前生成；仅在包括 Baseline 的至少两个完整可比较 PASS、且候选存在明确官方总分时保存。
- case003/005 必须显式选择；case005 真实实验另需热文件来源核验说明。
- 已有外部 case005 RUNNING 任务继续运行，其五份进度/监控文件自然追加；没有被停止、修改配置或重启。真实实验会等待外部未结束运行，dry-run/report-only 正常可用。
- Matplotlib 3.11.2 已安装到当前 Windows Python 用户环境；其他 Python 环境应按 requirements.txt 安装。绘图只在导入时限制 BLAS 线程，随后恢复环境变量，不改 Router/Evaluator 环境。
- UI 的有界 JSON Reader 为只读依赖；不会创建 UI 缓存。单 JSON 值超过 64 MiB 明确报错，不截断结果。

## 验证证据

- [验证汇总](./reports/platform_validation_20261029_000202_9ee0dc/verification.json)
- [单元测试日志](./reports/platform_validation_20261029_000202_9ee0dc/unit_tests.log)
- [816 份原有文件完整性检查](./reports/platform_validation_20261029_000202_9ee0dc/integrity.json)
- [完整使用说明](./README.md)
- [本批分析报告](./reports/phase1_case001/analysis.md)

## 完整实际目录树

包含初轮与最终软件验证记录；.manager.lock 为忽略 Git 的操作系统锁文件，不是实验成绩。未来 attempts/extracted/best_config 在需要时才创建。

```text
parameter_tuning/
├── configs/
│   └── phase1_case001/
│       ├── baseline.json
│       ├── congestion_high.json
│       ├── congestion_low.json
│       ├── history_high.json
│       ├── history_low.json
│       ├── iterations_high.json
│       ├── iterations_off.json
│       ├── margin_high.json
│       ├── thermal_high.json
│       ├── thermal_off.json
│       ├── via_high.json
│       └── via_low.json
├── reports/
│   ├── phase1_case001/
│   │   ├── figures/
│   │   │   ├── memory_comparison.png
│   │   │   ├── parameter_component_sensitivity.png
│   │   │   ├── parameter_sensitivity.png
│   │   │   ├── runtime_comparison.png
│   │   │   ├── score_breakdown.png
│   │   │   └── score_comparison.png
│   │   ├── .manager.lock
│   │   ├── analysis.md
│   │   ├── experiment_plan.json
│   │   ├── experiment_state.json
│   │   ├── ranking.csv
│   │   ├── results.csv
│   │   ├── results.json
│   │   └── second_round_recommendations.json
│   ├── platform_validation_20261028_235639_928a67/
│   │   ├── analysis_cli.log
│   │   ├── cli_help.log
│   │   ├── dry_run.log
│   │   ├── dry_run_again.log
│   │   ├── figures.json
│   │   ├── integrity.json
│   │   ├── report_only.log
│   │   ├── unit_tests.log
│   │   └── verification.json
│   └── platform_validation_20261029_000202_9ee0dc/
│       ├── analysis_cli.log
│       ├── cli_help.log
│       ├── dry_run.log
│       ├── dry_run_again.log
│       ├── figures.json
│       ├── integrity.json
│       ├── report_only.log
│       ├── unit_tests.log
│       └── verification.json
├── tests/
│   └── test_parameter_tuning.py
├── .gitignore
├── analysis.py
├── DELIVERY.md
├── experiment_config.json
├── plotting.py
├── README.md
├── records.py
├── requirements.txt
├── support.py
└── tune_parameters.py
```

## PowerShell 命令

```powershell
Set-Location -LiteralPath 'D:\我\大学\program\3DIC EDA\EDA\code_3DIC EDA'
python parameter_tuning/tune_parameters.py --help
python parameter_tuning/tune_parameters.py --case open_3d_case001 --experiment phase1_case001 --dry-run
# 以下两条会启动真实实验，交付时未执行：
python parameter_tuning/tune_parameters.py --case open_3d_case001 --experiment phase1_case001 --max-runs 2
python parameter_tuning/tune_parameters.py --case open_3d_case001 --experiment phase1_case001 --resume
# 仅重新生成报告：
python parameter_tuning/tune_parameters.py --case open_3d_case001 --experiment phase1_case001 --report-only
```
