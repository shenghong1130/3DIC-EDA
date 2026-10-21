# 赛题八提交要求说明


## 评测流程


```
学生提交 ZIP 文件
    ↓
1. Ingestion 阶段 (12小时超时)
    ├─ 解压提交文件
    ├─ 查找可执行文件 (run.sh / router.py / router)
    ├─ 可选：执行 build.sh 编译
    ├─ 运行程序生成 .guide 文件
    └─ 输出: ingestion_result.json
    ↓
2. Scoring 阶段 (5分钟超时)
    ├─ 读取 Ingestion 输出
    ├─ 调用企业评测工具 (score.py)
    ├─ 计算 S1 (合法性)
    ├─ 计算 S2+S3 (质量：线长/拥塞/热)
    ├─ 计算 S4 (效率：时间/内存)
    └─ 输出: scores.json (最终得分)
    ↓
3. 排行榜显示
    └─ 按最终得分排序
```

---

## 提交要求

### 必需文件

```
提交压缩包.zip
├── router (或 router.py 或 run.sh)    # 必需：可执行文件
└── docs/                               #  打榜练习阶段阶段可暂不提交
    ├── 编译与运行说明.md
    ├── 设计架构.md
    ├── 算法原理.md
    ├── 第三方库说明.md
    └── 优化结果对比.md
```

### 可选文件

```
├── build.sh         # 可选：编译脚本
├── src/             # 可选：源代码
├── lib/             # 可选：依赖库
└── README.md        # 可选：说明文档
```


### 命令行接口（固定格式）

**所有程序必须支持以下命令行参数**：

```bash
<executable> --def <input.def> --lef <input.lef> --thermal-report <thermal.json> --output <output.guide>
```

**示例**：
```bash
./router --def sample001.input.def --lef sample001.input.lef --thermal-report sample001.thermal.input.json --output sample001.output.guide
python3 router.py --def sample001.input.def --lef sample001.input.lef --thermal-report sample001.thermal.input.json --output sample001.output.guide
bash run.sh --def sample001.input.def --lef sample001.input.lef --thermal-report sample001.thermal.input.json --output sample001.output.guide
```

---

##  支持的提交格式

### 格式 1: C++ 二进制可执行文件（推荐）

**提交内容**：
```
submission.zip
├── router           # 编译好的 ELF 二进制文件
└── docs/
```

**Ingestion 处理**：
1.  自动添加执行权限 (`chmod +x router`)
2. ✅ 直接运行：`./router --def ... --lef ... --thermal-report ... --output ...`

**成功条件**：
-  文件名为 `router`、`route`、`main` 或 `run` 之一
-  文件存在（权限会自动添加）
-  支持标准命令行参数

---

### 格式 2: Python 脚本 

**提交内容**：
```
submission.zip
├── router.py        # Python 脚本
└── docs/
```

**Ingestion 处理**：
1. 自动识别为 Python 脚本
2. ✅ 运行：`python3 router.py --def ... --lef ... --thermal-report ... --output ...`

**成功条件**：
-  文件名为 `router.py`、`route.py` 或 `main.py` 之一
-  文件存在
-  使用 Python 3.10 标准库（容器已安装）
-  支持标准命令行参数

---

### 格式 3: Shell 脚本包装器 

**提交内容**：
```
submission.zip
├── run.sh           # Shell 脚本
├── router           # 实际可执行文件（可选）
└── docs/
```

**run.sh 示例**：
```bash
#!/bin/bash
./router "$@"
```

**Ingestion 处理**：
1.  优先查找 `run.sh`（最高优先级）
2.  自动添加执行权限
3. ✅ 运行：`bash run.sh --def ... --lef ... --thermal-report ... --output ...`

**成功条件**：
-  文件名必须为 `run.sh`
-  脚本内部可以调用任何程序

---

### 格式 4: 需要编译的源代码 

**提交内容**：
```
submission.zip
├── build.sh         # 编译脚本
├── src/
│   └── router.cpp
└── docs/
```

**build.sh 示例**：
```bash
#!/bin/bash
g++ -std=c++17 -O3 -o router src/router.cpp
```

**Ingestion 处理**：
1.  检测到 `build.sh`，自动执行编译
2.  编译成功后，查找可执行文件
3.  运行可执行文件

**成功条件**：
-  `build.sh` 存在且可执行
-  编译成功生成 `router`、`route`、`main` 之一
-  编译时间在合理范围内（< 10分钟）

---

##  成功保证条件

**只要满足以下条件，保证 Ingestion 成功**：

### 条件 A: 文件结构
```
submission.zip (根目录直接包含)
├── <可执行文件>    # 以下之一
│   ├── run.sh      # 优先级 1
│   ├── router.py   # 优先级 2
│   ├── router      # 优先级 3
│   ├── route.py    # 优先级 4
│   ├── route       # 优先级 5
│   ├── main.py     # 优先级 6
│   └── main        # 优先级 7
└── docs/           # 5个必需文档
```

**注意**： 不能多一层目录！
```
#  错误示例
submission.zip
└── my_project/
    ├── router
    └── docs/

#  正确示例  
submission.zip
├── router
└── docs/
```

### 条件 B: 命令行接口

程序**必须**支持以下格式：
```bash
<program> --def <path> --lef <path> --output <path>
```

**参数顺序**：无要求，但参数名必须完全一致：
-  `--def` (不是 `-d` 或 `--DEF`)
-  `--lef` (不是 `-l` 或 `--LEF`)
-  `--output` (不是 `-o` 或 `--out`)

#### 条件 C: 输出文件

程序**必须**在指定路径生成 `.guide` 文件，格式如下：

```
net_name1
(
x1 y1 x2 y2 layer_name
x1 y1 x2 y2 layer_name
)
net_name2
(
x1 y1 x2 y2 layer_name
)
```

**格式要求**：
-  每个网络：网络名 + `(` + 矩形列表 + `)`
-  矩形格式：`x1 y1 x2 y2 layer_name` (空格分隔)
-  坐标必须是整数 (DBU 单位)
-  层名必须在 LEF 中定义

---

###  可能失败的情况

#### Ingestion 失败（得 0 分）

| 失败原因 | 检查方法 | 解决方案 |
|---------|---------|---------|
| 找不到可执行文件 | 解压后根目录没有 `router`/`router.py`/`run.sh` | 确保文件在根目录 |
| 命令行参数错误 | 程序不支持 `--def --lef --output` | 修改参数解析 |
| 程序崩溃 | 运行时 segfault 或异常 | 调试程序 |
| 超时 | 运行超过 12 小时 | 优化算法 |
| 未生成输出 | `.guide` 文件不存在 | 检查输出路径 |

#### Scoring 失败（Ingestion 成功，但得 0 分）

| 失败原因 | 检查方法 | 解决方案 |
|---------|---------|---------|
| 输出格式错误 | 企业工具解析失败 | 检查格式严格匹配 |
| 越界 | `crossing border nets` | 所有坐标在 DIEAREA 内 |
| Pin 未覆盖 | `Pin is not contained` | 确保所有 pin 被矩形覆盖 |
| 网络不连通 | `net not connected` | 确保同网络 pin 连接 |

---


##  成功保证清单

### 学生提交前自查

**打包前检查**：
- [ ] 解压后，可执行文件在根目录（不是子目录）
- [ ] 文件名是 `router` 或 `router.py` 或 `run.sh`
- [ ] 有 `docs/` 目录，包含 5 个 .md 文件

**本地测试**：
```bash
# 1. 解压测试
unzip submission.zip -d test_dir
cd test_dir
ls -la  # 确认 router 在当前目录

# 2. 运行测试
./router --def sample001.input.def --lef sample001.input.lef --thermal-report sample001.thermal.input.json --output test.guide
# 或
python3 router.py --def sample001.input.def --lef sample001.input.lef --thermal-report sample001.thermal.input.json --output test.guide

# 3. 检查输出
ls -lh test.guide
head -20 test.guide
```

**输出格式检查**：
```bash
# 格式必须完全正确
cat test.guide | head -10
# 应该看到：
# net_name
# (
# 100 200 300 400 metal1
# )
```

---

## 🔧 常见问题处理

### Q1: 我的程序本地能跑，为什么提交失败？

**可能原因**：
1.  文件名不对（不是 `router`/`router.py`/`run.sh`）
2.  多了一层目录
3.  命令行参数不对
4.  依赖的库在容器里没有

**解决**：
- 严格按照文件名要求
- 打包时 `cd` 到目录内再 `zip`
- 使用标准参数 `--def --lef --thermal-report --output`
- 使用标准库或静态链接


### Q2: 可以用 Java/Rust/Go 吗？

**可以！** 只要：
1.  编译为二进制 (静态链接)
2.  或提供 `run.sh` 包装器
3.  支持标准命令行参数

**示例 run.sh (Java)**：
```bash
#!/bin/bash
java -jar router.jar "$@"
```

---

##  评分公式

```
最终得分 = S1 × (S2 + S3) × S4

其中：
S1 = 合法性 (0 或 1)
     - 所有 pin 被覆盖 
     - 所有坐标在范围内 
     - 输出格式正确 

S2 + S3 = 质量得分 (0-100)
     = 45% × 线长得分
     + 30% × 拥塞得分  
     + 25% × 热优化得分

S4 = 效率 (0 或 1)
     - 时间 < 12 小时 
     - 内存 < 128 GB 
```

---

