# Local Project V5 / Original V0.16 Source Engine

V5 是原包在本项目的独立名称，不是 V4.5 的算法后继实现。`engine/` 的 79 个文件按字节保留，禁止在此目录编辑算法、入口或参数。无需 C++ 编译，也不需要第三方 Python 依赖。

在工程根目录的 PowerShell 中：

```powershell
python scripts/run_v5_case.py --case open_3d_case001 --label case001_v5 --backend wsl
python scripts/run_v5_case.py --case open_3d_case003 --label case003_v5 --backend wsl
python scripts/run_v5_case.py --case open_3d_case005 --label case005_v5 --backend wsl
```

顺序运行。已有 Label 会使用未占用的 `_runN`，评分时须使用本次真实 Label。每次保留原始默认完整流程和约 11 小时内部预算，外部不缩短预算、不添加减预算参数。资源不足不启动。

Ubuntu 原始接口（路径须正确引用；目标 Guide 不得已存在）：

```bash
bash v5/run_v5.sh --def INPUT.def --lef INPUT.lef --thermal-report HEAT.json --output OUTPUT.guide
```

默认入口仅用 Python 3 标准库。已验证环境为 Ubuntu / Python 3.10.12。适配层运行前后验证全部原文件哈希，禁止生成引擎目录内 pyc。

成功后独立参考评分：

```powershell
python scripts/score_v5_case.py --case open_3d_case001 --label case001_v5 --memory-mb 1536 --timeout 600
```

这是项目 REFERENCE_20261103，不是官方成绩；不改变原始 Guide。旧评分入口的 C++ 注册检查不适用于 Python 引擎，因此使用独立适配器，旧版本入口不改。原引擎输出 `route.guide`、`route.run.json` 和私有阶段日志，项目适配器另记 `run.json`、时间/内存/退出证据；不伪造 C++ `result.json`。

`original/` 保存选中 ZIP、原清单与发布声明。全部任务报告位于 `outputs/reports/v5/`；发布声明的 73.50 不是本次实测分数。禁止自动上传或提交 Git。
