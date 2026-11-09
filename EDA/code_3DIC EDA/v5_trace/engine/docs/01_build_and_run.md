# 编译与运行说明

版本：v0.16。环境：Ubuntu 22.04 x86_64、Python 3.10、bash。仅使用 Python 标准库，无需编译或安装软件包，无联网和 API Key 依赖。

执行命令：
```bash
bash run.sh --def /input/chip.input.def --lef /input/chip.input.lef --thermal-report /input/chip.thermal.input.json --output /output/chip.output.guide
```

输入文件只读，路径和文件名不参与算法参数选择。输出文件必须不存在；程序会创建输出目录。除最终 GUIDE 外，输出旁保留 JSON 报告，私有 .final-router-* 临时目录保留阶段日志，便于复现核查。

默认执行完整流程。--development-small-budget 仅供小规模集成检查，不是默认评测参数。总运行预算为每次调用约 11 小时；基线最多 9 小时，后续阶段受剩余预算约束，并预留最终校验时间。

Linux 超时终止整个子进程组。优化失败时保留上一份通过检查的结果；基线异常时仅恢复本次调用私有目录中完整且通过校验的检查点。没有可用完整结果时返回错误，不输出伪造成功。任意超规格输入的运行时间与内存不作保证。
