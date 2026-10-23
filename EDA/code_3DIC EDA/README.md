# 3DIC 全局布线程序 v0.1

面向 2026 年研究生创芯大赛 EDA 精英挑战赛赛题八的独立 C++17 初版工程。
Router 只读取 DEF、LEF、Thermal JSON，不读取参考 Guide。UI 通过另外的只读接口加载参考 Guide。

## 快速开始

在本目录执行，路径中包含空格时必须加引号。

```bash
# Ubuntu 22.04 / g++ 11.4
bash build.sh
./router --def /path/input.def --lef /path/input.lef \
  --thermal-report /path/thermal.json --output /path/result.guide
```

Windows PowerShell 使用已配置的 Ubuntu WSL：

```powershell
python scripts/run_case.py --list
python scripts/run_case.py --case toy --backend wsl --label experiment01 --trace
python scripts/evaluate.py --case toy --label experiment01 --backend wsl
python ui/app.py
```

浏览器打开 <http://127.0.0.1:8501>。UI 仅需要 Python 3.10 以上标准库和现代浏览器，无 npm、pip、CDN 依赖。
先选择数据集、Case、自主运行记录，再选择 Net；双 Die 视图同步高亮。

## 输出和运行记录

```text
outputs/runs/<case>/<label>/
  run.json                 输入哈希、配置、命令、进程时间、峰值内存、状态
  route.guide              自主计算的 Guide
  result.json              网格、实例、引脚、真实图边、矩形、局部指标
  trace.jsonl              可选的真实阶段、路径、提交和拆除事件
  router.log               原始运行日志
  resource_usage.txt       Linux /usr/bin/time -v 输出
  official_latest/        未修改官方 ELF 评分器的报告和 CSV
```

运行标签不可覆盖，修改配置后使用新标签。`run_case.py` 限制输出在 `outputs/` 内。
直接比赛 CLI 允许评测方指定任意输出路径；请勿把官方参考文件指定为输出。

## 架构

| 文件 | 输入 → 处理 → 输出 |
|---|---|
| `src/parser.cpp` | LEF/DEF → 词法、几何和方向转换 → `Design` |
| `src/router.cpp` | `Design`/热图/配置 → MST、A*、TSV、协商重布 → `Route` |
| `src/main.cpp` | 固定比赛 CLI → 校验、运行、写出 → Guide/可选 JSON/Trace |
| `scripts/run_case.py` | Case 或显式路径 → 计时运行 → 独立运行目录 |
| `scripts/evaluate.py` | 完整运行 → 官方 latest/legacy 工具 → 原始报告和调用状态 |
| `scripts/package_submission.py` | 源码、入口、五份文档 → 最小 ZIP → 包及清单 |
| `ui/app.py` + `ui/static/` | 真实运行文件 → 本地只读 HTTP/Canvas → 双 Die、回放、热图 |

## 测试与打包

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j2
ctest --test-dir build --output-on-failure
python -m unittest discover -s tests -v
python scripts/package_submission.py --with-linux-binary
```

ZIP 根目录包含 `run.sh`、`build.sh`、Linux `router`（可选）、`src/`、`include/`、五份 `docs/` 和说明。
不包含 UI、官方数据、参考 Guide、Trace、缓存或 Windows 可执行文件。
打包成功只代表压缩包创建成功。实际验证结果见 [优化结果对比](docs/优化结果对比.md) 和 `outputs/reports/`，不能据此推断隐藏用例或正式提交已合格。

## 适用范围

实现矩形 LEF 引脚/OBS、矩形 DIEAREA、标准八方向、两 Die 共最多二十层、相邻普通 Via 和 M10/M11 TSV。
不支持的非空 DEF VIAS/BLOCKAGES/SPECIALNETS 等段落会明确报错；LEF POLYGON/PATH/ITERATE 等引脚几何也会报错。
每个端子选择第一个矩形访问点；尚未进行多端口访问优化、详细布线、RC/时序分析、FLUTE 或大规模并行。
局部容量、固定需求和 Pearson 映射是可解释的近似值，与官方指标分开显示。
