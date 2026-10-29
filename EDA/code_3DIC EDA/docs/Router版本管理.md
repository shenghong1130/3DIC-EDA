# Router 编译版本管理

`src/router.cpp` 始终是当前开发分支的正式源码；Git 保存历史源码，`build/router_vN` 保存经核实的对应编译文件。修改源码不会自动更新任何旧二进制。

## 1. 已确认的正式版本

| 版本 | 正式文件 | 来源文件（保留） | 源码提交 | 算法内容 |
|---|---|---|---|---|
| V1 | `build/router_v1` | `build/router_baseline` | `024d648dae3fe9051e336b8928502de09bf0654b` | 性能优化前的独立 Router：Prim MST、A*、唯一 TSV、热感知、拥塞重布与 Guide 验证 |
| V2 | `build/router_v2` | `build/router_optimized` | `a0f8e717ebbeca32ef999c8caf398fec46ba41c2`（version2） | 有界 A* 状态/几何缓存、TSV 热图与候选复用、容量/临时对象优化、增量重布备份和回滚 |
| V3 Final | 唯一入口 `build/router_v3_final`；登记须另行核验 | 旧候选保留作历史证据 | 本轮基点 `410a512`，新修改待用户提交 | 固定配置、V2 安全重布与 Guide 质量保护；发布结果见主目录第 13 章 |

V3 的实现、候选 SHA256、对照数据及局限见主目录 [算法版本迭代说明](../../../算法版本迭代说明.md)。登记 JSON 中的 V3 状态仍描述正式版本；本轮没有手改登记或覆盖 V1/V2。

V1 SHA256：`70ddc978c6f427ef79d6601e0a3bf099108e945cbbac249b2c2aa234d3c00423`。

V2 SHA256：`6440fcf3211e616534d9192bbc7ceabec99956fc41f1b43e002d2ffa06c0b30f`。

两个版本均从明确的 Git 提交提取源文件到临时目录，以 g++ 11.4 / C++17 / `-O3 -DNDEBUG -Wall -Wextra` 独立重建，生成文件的 SHA256 与原文件一致，再验证四参数 `--help` 并复制登记。没有改动当前 C++、覆盖旧文件或重新运行布线。Git 的 LF 与历史工作区 CRLF 源码字节可能不同，独立重建确认了二进制对应关系。

`3456a8855d16158688e00ef0a9216b435e621f81`（version2 result）只更新实验配置/报告，C++ 源码与 version2 相同，不因此产生 V3。当前根目录 `router`、`build/router_baseline`、`build/router_original_70ddc978c6f4` 与 V1 同哈希；`router_optimized_checked` 与 V2 同哈希。默认 WSL 路径仍选根目录 `router`，因此当前默认运行 **V1**。Windows native 后端仍按原规则默认选择 `outputs/router.exe`，不同字节未登记时为 UNKNOWN。

## 2. 登记与清单

| 文件 | 用途 |
|---|---|
| [build/router_versions.json](../build/router_versions.json) | 本机运行时登记；包含真实哈希、Git 提交、原文件、编译器、命令、独立重建与 CLI 核验证据 |
| [docs/router_versions.json](../docs/router_versions.json) | 相同登记的可 Git 跟踪副本；`build/` 被忽略，不把 ELF 强行加入 Git |
| [docs/router_build_inventory.json](../docs/router_build_inventory.json) | 本次逐文件清查快照：完整 SHA256、ELF 类型、角色、源码/构建证据、原命令及关联历史 Run |
| `outputs/reports/router_version_management/` | 本次保护清单、注册/测试日志和验证报告；旧性能报告未修改 |

进入 `EDA\code_3DIC EDA` 后，在 PowerShell 执行：

```powershell
python scripts/manage_router_versions.py --list
python scripts/manage_router_versions.py --verify
```

`--list` 动态核对正式文件哈希和 Git 源码证据；`--verify` 另检查可执行的四参数 `--help`，都不编译、不运行布线。需要再次独立重建核验时显式使用 `--verify --rebuild`。Verified 表示来源/字节/CLI 核实，不等于新的官方评分 PASS。

运行时只读本机登记文件，不自动恢复、写登记、调用 Git 或编译。登记缺失/损坏时为 UNKNOWN；换机器后可在检查文档副本后复制到 `build/router_versions.json`，再运行 `--verify`。登记文件和二进制需要一起保存。不要手改 `verified` 冒充已核实版本。

## 3. 旧编译文件的含义

| 文件或生成规则 | 实际用途 | 能否作为正式版本选择 |
|---|---|---|
| `router_v1`、`router_v2` | 哈希与源码已核实的正式 Router 副本 | 可以 |
| `router_baseline`、`router_original_70ddc978c6f4` | V1 的历史文件 | 可运行，按哈希识别 V1；建议新实验使用统一名字 |
| `router_optimized`、`router_optimized_checked` | V2 的历史文件 | 可运行，按哈希识别 V2；建议新实验使用统一名字 |
| `router_<stage>_profile` | `src/main.cpp` + `GR_PROFILE` 的诊断 CLI | 四参数接口可用，但不同哈希、通常为 UNKNOWN，不等于正式 V1/V2 |
| `router_<stage>_bench` | `tests/router_benchmark.cpp` 的受控性能基准 | 不接受比赛四参数，不能作为正常 `run_case.py --router` 输入 |
| `router_<stage>_core`、`core_baseline`、`core_optimized` | `tests/core_tests.cpp` 的测试程序 | 不生成比赛 Guide，不作为 Router |

`scripts/benchmark_router.py --stage <新名称>` 按固定规则分别生成 `_profile`、`_bench`、`_core`，已有阶段不会覆盖；`--reuse-stage` 复用哈希校验后的既有程序。`scripts/report_router_benchmark.py` 只读取测量、生成报告，不编译。各阶段详细证据在 `outputs/reports/algorithm_optimization/stages/*/builds.json`；早期失败阶段可能没有完整 builds.json，无法核实的源码/命令保留 UNKNOWN，不从名字中的 `v2` 推断正式版本。

本次具体未确认项是 `router_astar_core`：其测试断言字符串表明它是 Core Test，但失败阶段没有可匹配其哈希的完整构建清单，准确源码和实际编译命令标为 UNKNOWN。它保持原位，不登记为正式版本。其他逐文件结论见清查 JSON；其中源码阶段快照不等于可确认的正式 Git 版本。

`build.sh` 编译当前源码并覆盖根目录 `router`；CMake 的 `router`、`core_tests` 输出到用户选择的构建目录。它们不会自动登记正式版本，也不会按已有文件名恢复历史源码。保留旧性能程序原路径，避免报告引用失效；本次没有移动或删除这些程序。

## 4. 普通运行会自动显示版本

`scripts/run_case.py` 复用实际选中二进制的 SHA256，对照已核实登记，支持默认路径、显式 `--router`、重命名/复制后的相同文件。文件名不参与版本推断。

启动 Router 前显示版本、二进制、哈希、识别状态、配置实际路径和原始参数、CLI 覆盖值、Case、Label 和结果目录。新 `run.json` 在进程启动前保存：

```json
{
  "router_version": "v2",
  "router_binary": "build/router_v2",
  "router_sha256": "实际文件哈希",
  "router_version_status": "MATCHED",
  "config_path": "configs/default.json",
  "config_overrides": {"time_limit": 600.0},
  "effective_time_limit_seconds": 600.0
}
```

这是字段说明，实际值由当次命令读取。原 `config` 保留配置文件中的值；`--time-limit` 覆盖的生效预算单独保存，不将原配置改写成覆盖后的配置。缺省 JSON 键仍由对应 Router 的默认值处理，不臆造默认参数。

无登记匹配时 `router_version=UNKNOWN`；缺失/损坏登记或无法读二进制时说明具体原因。一个 SHA256 匹配多个已验证标签时，状态为 AMBIGUOUS，版本为 UNKNOWN，`router_version_matches` 保留全部候选，不选择其中一个。识别失败不会阻止原运行逻辑；失败或超时也保留启动时的版本记录。历史 `run.json` 不回填。

## 5. 分别选择 V1、V2

以下命令仅供用户执行；本次未启动这些大型用例。每次使用未占用 Label。

```powershell
Set-Location -LiteralPath 'D:\我\大学\program\3DIC EDA\EDA\code_3DIC EDA'
python scripts/run_case.py --case open_3d_case001 --backend wsl --router build/router_v1 --label case001_v1_default01 --config configs/default.json --time-limit 600
python scripts/run_case.py --case open_3d_case001 --backend wsl --router build/router_v2 --label case001_v2_default01 --config configs/default.json --time-limit 600
```

不加 `--router` 的 WSL 命令仍执行根目录 `router`，不会自动替换成最新版本。评分器/UI 仍读取相同 Guide、结果与原有清单字段。参数实验脚本接口保持兼容，不修改旧计划/结果；如果旧批次冻结了 `run_case.py` 哈希，本次新增记录功能会触发其原有版本保护，应使用新 Experiment ID，或在原工具环境续跑，不篡改旧指纹。

## 6. V3 Final 构建与后续版本

日常 V3 只使用 `build/router_v3_final + configs/v3_final.json`。旧 V3 配置原样归档到 `configs/archive/`，旧候选仍保留原路径；`run_case.py` 可解析缺失的旧 V3 配置路径到归档文件，既存运行清单不改写。

`validate_v3_final.py` 在临时目录以 `-DGR_V3_FINAL` 编译当前源码，执行核心及工作流测试，再以同一配置实际比较 V2/Final 的 toy、sample001、case001。只有全部官方 PASS、各分项无退步、case001 线长或 Overflow 严格改善，`--apply` 才独占发布唯一 Final 文件。失败保留报告，不发布或留下永久候选；已存在的 Final 不自动覆盖。

```powershell
python scripts/validate_v3_final.py --experiment final_check_01 --apply
```

实验 ID 必须未占用；此命令不运行 case003/005。CMake 另提供 `router_v3_final` 目标，但自行构建不代替质量验证。原 `build.sh` 不带 Final 构建标志，仍覆盖根目录研究 Router，不用于发布 Final。

通过本地发布后，仍须由用户保存对应 Git 源码提交，再进行正式登记。不要对未提交的源码声称已完成版本注册：

```powershell
$sourceCommit = (git rev-parse HEAD).Trim()
python scripts/manage_router_versions.py --register v3 --final-entry --source build/router_v3_final --git-commit $sourceCommit --description 'V3 Final：固定配置与 Guide 质量保护'
python scripts/manage_router_versions.py --verify
```

`--final-entry` 仅用于 V3：独立提取声明提交的三个生产编译单元，以相同编译选项和 `-DGR_V3_FINAL` 重建，比对 SHA256、验证 `--help`；已有的同一 Final 只核验，不复制覆盖。登记记录 `build_variant=v3_final`，以后 `--verify --rebuild` 复用相同标志。不更改当前工作区、不执行 Git commit/push、不启动布线。未提交源码、不同编译器/选项或不能精确重建时拒绝登记，保持 UNKNOWN。版本标签已存在、文件冲突或登记副本不一致时停止，不覆盖历史版本。

本地验证发布与正式登记是两个步骤。发布后可以运行下列命令；未登记时 UNKNOWN 是正确状态，不表示文件名已经证明版本身份：

```powershell
python scripts/run_case.py --case open_3d_case001 --backend wsl --router build/router_v3_final --label case001_final_study01 --config configs/v3_final.json --time-limit 600
```

后续 V4/V5 仍使用原有显式版本登记流程；本轮不增加其他永久 V3 配置和候选。具体测试、SHA256、性能及发布判定见主目录 [算法版本迭代说明第 13 章](../../../算法版本迭代说明.md#version-13)。
