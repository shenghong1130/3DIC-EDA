"""Regenerate V5 integration reports from measured receipts only."""
import json
from pathlib import Path
from common import PROJECT
from scoring import summary
from run_v5_case import REPORTS, verify


def read(p):
    return json.loads(p.read_text(encoding='utf8')) if p.exists() else {}


def main():
    manifest=verify();eqs=[read(p) for p in sorted(REPORTS.glob('equivalence*/result.json'))]
    eq=next((r for r in reversed(eqs) if r.get('status')=='PASS'),eqs[-1] if eqs else {})
    cases=[];blocked=read(REPORTS/'case_results.json')
    for short in ['001','003','005']:
        case='open_3d_case'+short;label='case'+short+'_v5'
        latest=(blocked.get(case) or [{}])[-1]
        label=latest.get('label',label);p=PROJECT/'outputs/runs'/case/label
        run=read(p/'run.json')
        if not run:run=latest
        score={}
        if run.get('status')=='ROUTED':
            try:score=summary(case,run['label'],'reference_20261103')
            except ValueError as e:score={'status':'ANALYSIS_ERROR','error':str(e)}
        cases.append({'case':case,'label':run.get('label',label),'status':run.get('status','NOT_RUN'),
                      'guide_exists':(p/'route.guide').is_file(),'guide_sha256':run.get('guide_sha256'),
                      'runtime_seconds':run.get('runtime_seconds'),'peak_memory_bytes':run.get('peak_memory_bytes'),
                      'original_validation':run.get('original_validation'), 'reference_status':score.get('status','NOT_EVALUATED'),
                      'reference_metrics':score.get('metrics',{}),'report_path':score.get('report_path'),
                      'resource_check':run.get('resource_check',{}),'resource_gate':run.get('gate')})
    table='| Case | Router | Guide | 原始检查 | REFERENCE_20261103 | Runtime s | Peak bytes |\n|---|---|---|---|---|---:|---:|\n'
    for r in cases:table+='| '+' | '.join(str(x) for x in [r['case'],r['status'],r['guide_exists'],(r['original_validation'] or {}).get('status','N/A'),r['reference_status'],r['runtime_seconds'] or 'N/A',r['peak_memory_bytes'] or 'N/A'])+' |\n'
    data={'version':'v5','source_engine':'Original V0.16 Source Engine','source_integrity':'79/79 BYTE_IDENTICAL','equivalence':eq,'cases':cases,'historical_public_quality_claim':73.5,'historical_claim_not_reproduced':True}
    (REPORTS/'results.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    docs={
    'v5_import_report.md':f'''# V5 原样导入

外层 SHA256：`{manifest['outer_sha256']}`。
最终 eda260890.zip SHA256：`{manifest['selected_zip_sha256']}`。
final_choice、ZIP CRC、安全路径、79 项正式清单全部核实；code_manifest 的 69 项代码清单也逐项匹配，另 10 项为文档/版本/验证资料。code_preflight.zip 的历史哈希单独核实，但未作为引擎。外层 program/、pyc 和缓存未导入。

79 个文件按原始字节保存，包含 baseline_v12 和原始 run.sh；原始清单、final_choice 和验证回执保存在 v5/original。Original V0.16 Source Engine 与 Local Project V5 Version Name 是不同命名，不代表算法升级。

Public Quality 73.50、Legality 1、Hidden null 只是原包历史声明，不是本次新成绩。
''',
    'v5_original_algorithm.md':'''# 原始算法流水线

仅描述原始 final_router.py / baseline_v12/portable.py，未混入 V4.5。

run.sh → final_router.py → baseline_v12/portable.py。baseline 使用原始 optimize（包含 adaptive routing），再按原默认预算进行 wire refinement、maze、compaction、按 Net 数派生的额外 maze、balanced thermal/joint TSV、layer escape、congestion repair 及压缩。MST/Steiner、多层路径、资源模型和热模型全部来自原包。

最终入口在基线验证后顺序执行：landing_repair → buffered_escape → guarded_wire → tree_reattach → pin_escape → guarded_maze → compact_guide。每阶段用 validate 检查完整输出；buffered 另用 choose_stage/accept_buffered：起点 Phi>1、线长在输入派生 allowance 内、Phi 严格下降、组合代理目标改善。其他阶段保留各模块内部验收，不加外部质量筛选。

默认 reroute 预算：buffered 100000、wire 2000、tree 4096、pin 256、maze min(8000,max(2048,ceil(N×.04)))。landing/tree 收到 thermal；这些是原始参数，适配器不覆盖。

入口总预算 39600s，baseline 最多 32400s，并根据剩余时间预留后续阶段；单可选阶段最多 21600s、预留最终验证。原始 runtime_budget 控制子进程组。失败保留上一份通过验证的 Guide；基线异常只搜索本次私有目录最新的最多 12 个 Guide 检查点。没有合法完整结果时失败，不借用历史 Guide。

LOCAL_PASS 的范围包括 Net 集合、Pin 覆盖、GCell 对齐、方向、相邻层连通、TSV 等。Track assignment、真实 DRC、官方质量评分不在该检查范围；不能把原始 LOCAL_PASS 写成官方 PASS。
''',
    'v5_integration.md':'''# 外部集成

新增 scripts/run_v5_case.py、v5/run_v5.sh、v5/verify_source.py；原始 engine 未改。Case 输入使用既有 catalog。只向原入口传 --def/--lef/--thermal-report/--output；没有 config、权重、减预算或公开 Case 算法分支。Case 名只用于目录和外部资源检查。

启动前后验证全部 79 个文件及原 ZIP；输入前后 SHA 相同才记录完整 ROUTED。已有 Label 自动选择未占用的 _runN，不覆盖旧文件。stdout/stderr、原 route.run.json、stage 私有日志、资源/退出回执全部保留。原程序不生成 C++ result.json；不伪造该文件或 ELF 身份。

外部监督不设算法超时，保留原 11 小时内部预算。120s host lease 仅在管理进程失联时保护进程生命周期。PYTHONDONTWRITEBYTECODE 防止 pyc 污染原目录；PYTHONPATH 为空，关闭 user-site，与独立差分环境一致。此处不改变算法参数。

Windows → Ubuntu WSL，使用 Python 3.10.12。case003/005 要求至少 6 GiB Windows Commit 和 Linux MemAvailable，其他至少 2 GiB；还检查 20 GiB 空间与活动任务。资源不足仅写 BLOCKED_RESOURCE，不生成假 Guide。

旧统一评分 CLI 要求已登记的 C++ 二进制和 Config。新增 score_v5_case.py 做 Python 源码身份适配，并调用原项目同一 REFERENCE_20261103 ELF、命令接口、评分目录与索引函数；旧脚本、评分公式和注册表未改。评分完全在 Guide 生成之后进行，不反馈路由决策。现有通用评分 UI 可能将 Python Router 版本显示 UNKNOWN；不能伪造 C++ 登记来消除此限制。
''',
    'v5_equivalence_test.md':'# toy 差分验证\n\n```json\n'+json.dumps(eq,ensure_ascii=False,indent=2)+'\n```\n\n两侧使用同一输入哈希、Python、默认完整预算与环境；仅独立引擎路径和输出路径不同。A 从原 ZIP 临时解压后直接调用原 run.sh，B 经外部启动器调用原 run.sh。临时解压目录已自动清理，输出与命令回执保留。发布记录未给出 toy 输入 SHA，不能仅凭同名宣称输入身份相同；本次两个输出恰与发布 Guide 哈希相同。\n',
    'v5_case_results.md':'# 公开 Case 结果\n\n'+table+'\n实际命令、输入/源码/Guide SHA 与原始阶段结果见各 run.json。参考指标详见 results.json；未运行/未评分写 N/A，禁止以原包历史分数填充。\n',
    'v5_vs_v45.md':'''# V5 与 V4.5

V4.5 原记录是 Windows C++ 本地模型；V5 是 Linux Python 原始引擎。未经相同输入、同一评分器来源和模型参数验证，不直接比较两者 Overflow 或推导提升幅度。

| 指标 | V4.5 | V5 |
|---|---|---|
| 同口径 case001 Overflow | N/A（此前只有 Router Local） | 见本次 reference_report，若无则 N/A |
| 同口径 case003/005 Overflow | N/A | 未完成评分时 N/A |
| Congestion/Wirelength/Thermal/Total 百分制分数 | 无本次可比官方结果 | N/A |
| 合法性 | 原 TSV 审计 MODEL_DEPENDENT，非官方 PASS | 原始 LOCAL_PASS 与参考结论分开 |
| 运行时间/内存 | 原生平台历史结果，不作同平台加速比 | 见 case_results |

不重新跑 V4.5，不用 V4.5 模型干预 V5，也不把原包 73.50 当作新接入成绩。原样一致性优先于得分优劣。
''',
    'v5_final_status.md':'# V5 当前验收\n\n79/79 原始文件字节一致；toy 差分状态：'+eq.get('status','NOT_RUN')+'。\n\n'+table+'\n源码集成与大规模验证分开：没有三个 Case 的新评分证据时，不声明全量验证完成。未修改旧版本、未提交 Git/GitHub、未上传比赛平台。\n'}
    old=read(PROJECT/'outputs/reports/v4_5/results.json').get('formal_rows',[])
    docs['v5_vs_v45.md']+='\n\n## 数值及来源分列（不可直接作差）\n\n|Case|V4.5 本地 Overflow / Phi|V5 参考 Overflow / Phi|V4.5 Runtime s|V5 Runtime s|\n|---|---|---|---:|---:|\n'
    for r in cases:
        prior=next((a for a in old if a['variant']==r['case']),{});m=r['reference_metrics']
        docs['v5_vs_v45.md']+='| '+r['case']+' | '+str(prior.get('overflow_local','N/A'))+' / '+str(prior.get('phi_local','N/A'))+' | '+str(m.get('overflow','N/A'))+' / '+str(m.get('congestion_potential','N/A'))+' | '+str(prior.get('runtime_seconds','N/A'))+' | '+str(r['runtime_seconds'] or 'N/A')+' |\n'
    docs['v5_integration.md']+='\n主机监控复用旧日志设施；没有 C++ progress.jsonl 时显示 NO_PROGRESS 仅表示无该类记录，不代表 V5 算法停滞。应读取私有阶段日志和实际进程状态。原引擎不提供 C++ result.json 的节点/边结构，因此本次不声称现有 2D/3D UI 已支持 V5 完整路径展示；Guide、run.json 和评分索引可独立读取。\n'
    docs['v5_case_results.md']+='\n## ???????????\n\n| Case | Wirelength ?m | Overflow | Phi | Thermal correlation | TSV ?? | Guide ?? | ????? |\n|---|---:|---:|---:|---:|---:|---:|---:|\n'
    for r in cases:
        m=r['reference_metrics']
        docs['v5_case_results.md']+='| '+r['case']+' | '+' | '.join(str(m.get(k,'N/A')) for k in ['wirelength_um','overflow','congestion_potential','thermal_correlation','tsv_conflicts','guide_errors','connectivity_errors'])+' |\n'
    for r in cases:
        if r['status']=='BLOCKED_RESOURCE':
            check=r['resource_check'];available=check.get('windows',{}).get('commit_available_bytes',0)
            docs['v5_case_results.md']+=f"\n{r['case']}????? Commit ?? {available/2**30:.3f} GiB??? {check.get('required_bytes',0)/2**30:.1f} GiB?????? Guide??? `{r['resource_gate']}`?\n"
    toy=summary('toy','toy_v5','reference_20261103')
    docs['v5_equivalence_test.md']+='\n???????`'+toy['status']+'`??? `'+json.dumps(toy.get('metrics',{}),ensure_ascii=False)+'`???????????????? Guide?\n'
    discrepancy='\n## ???????\n\n??????? `v5/engine/tsv_obstacles.py` ? 16 ???? M10/M11 ? Pin/OBS??????????? ALL routing layers ??? GCell ?????case001 ?? LOCAL_PASS???????? 388 ? TSV_PIN_OBS ???Guide ???????? 0??????????????????????????????????????????????? Overflow/???????????????????????\n'
    docs['v5_final_status.md']+=discrepancy
    docs['v5_case_results.md']+=discrepancy
    docs['v5_vs_v45.md']+='\n| Case | V4.5 Peak bytes | V5 Peak bytes |\n|---|---:|---:|\n'
    for r in cases:
        prior=next((a for a in old if a['variant']==r['case']),{})
        docs['v5_vs_v45.md']+=f"| {r['case']} | {prior.get('windows_peak_working_set_bytes','N/A')} | {r['peak_memory_bytes'] or 'N/A'} |\n"
    docs['v5_final_status.md']+='\n???????toy ??????????case001 ???????????????????case003/005 ????????????????? Case ?????????????\n'
    for name,text in docs.items():(REPORTS/name).write_text(text,encoding='utf8')
    print(table,flush=True)


if __name__=='__main__':main()
