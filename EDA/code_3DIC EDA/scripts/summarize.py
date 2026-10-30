#!/usr/bin/env python3
"""Summarize only measured runs; preserve official/approximate metric labels."""
from common import *
def fmt(v):return 'Not Tested' if v is None else f'{v:.6f}' if isinstance(v,float) else str(v)
def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--history-only',action='store_true')
    args=parser.parse_args()
    if args.history_only:
        from case001_history import print_history
        print_history();return
    rows=[];allruns=[]
    for p in sorted((OUTPUTS/'runs').glob('*/*/run.json')):
        r=load_run(p.parent);d=json.loads((p.parent/'result.json').read_text(encoding='utf8')) if (p.parent/'result.json').exists() else {};report=p.parent/'official_latest/evaluation_report.json';official=load_record(report) if report.exists() else {}
        official_status=r.get('official_status','NOT_EVALUATED')
        if official and official_status!='PASS':official_status='REPORT_'+official.get('status','UNKNOWN')+' / metadata incomplete'
        allruns.append({'case':r['case'],'label':r['label'],'router':r['status'],'official':r.get('official_status','NOT_EVALUATED'),'local':d.get('metrics',{}),'iterations':d.get('iterations',[]),'process_runtime':r['runtime_seconds'],'peak_memory_bytes':r['peak_memory_bytes'],'report':official,'failure_reason':r.get('failure_reason'),'path':str(p.parent)})
        rows.append('| '+' | '.join(map(fmt,[r['case'],r['label'],r['status'],official_status,official.get('wirelength',{}).get('total_dbu'),official.get('congestion',{}).get('total_overflow'),official.get('thermal_tsv',{}).get('correlation_above_ambient'),d.get('metrics',{}).get('runtime_seconds'),r.get('peak_memory_bytes',0)/1048576 if r.get('peak_memory_bytes') is not None else None]))+' |')
    # Archived rows remain reportable without reading deleted Guide/result/logs.
    from case001_history import summary_rows
    for r in summary_rows():
        if r['daily_run_retained']:continue
        report=r['official_report']
        allruns.append({'case':r['case'],'label':r['label'],'router':r['status'],
            'official':r['official_status'],'local':r.get('metrics',{}),'iterations':r.get('iterations',[]),
            'process_runtime':r.get('runtime_seconds'),'peak_memory_bytes':r.get('peak_memory_bytes'),
            'report':report,'history_summary':r['history_summary'],'raw_run_retained':False})
        rows.append('| '+' | '.join(map(fmt,[r['case'],r['label']+' (archived summary)',r['status'],
            r['official_status'],r['wirelength_dbu'],r['official_overflow'],
            report.get('thermal_tsv',{}).get('correlation_above_ambient'),r['cpp_runtime_seconds'],
            r['peak_memory_bytes']/1048576 if r.get('peak_memory_bytes') is not None else None]))+' |')
    lines=['# 第一版真实测试汇总','','本表由运行记录与未修改官方 ELF 的 evaluation_report.json 生成。Wirelength、Overflow、Pearson 三列来自官方报告；Router 时间来自 result（早期 v2/v3 不含解析，后续 final 含解析），峰值内存为 Linux 进程 RSS。辅助脚本/WSL 启动耗时另存 run.json。','','| Case | Label | Router | 官方合法性 | 官方 Wirelength (DBU) | 官方总 Overflow | 官方 Pearson | Router 时间 (s) | 峰值内存 (MiB) |','|---|---|---|---|---|---|---|---|---|',*rows,'','## 验证说明','','- 官方工具本身标识 Guide / MST proxy 与 mock-DRT；PASS 不代表详细布线 DRC 或隐藏用例通过。','- 新版 sample001 热输入标为 synthetic，按官方输入原样计算；没有伪造热分数。','- 无官方报告时显示 Not Tested / NOT_EVALUATED，失败时保留原始日志。','- toy 与 sample001 的初期失败为 DEF END DESIGN/空 VIAS 段落处理，公开 case001 初期失败为 LEF PROPERTYDEFINITIONS 误识别；修复后使用新标签重跑。','- 大型 case003/case005：Not Tested。','','## 所有实际运行的优化轮次','']
    for r in allruns:
        if r['iterations']:lines += [f"### {r['case']} / {r['label']}",'','```json',json.dumps(r['iterations'],ensure_ascii=False,indent=2),'```','']
    body='\n'.join(lines)+'\n';(OUTPUTS/'reports').mkdir(exist_ok=True)
    (OUTPUTS/'reports/测试汇总.md').write_text(body,encoding='utf8');(PROJECT/'docs/优化结果对比.md').write_text(body,encoding='utf8');save(OUTPUTS/'reports/test_summary.json',allruns)
    print(body[:8000])
if __name__=='__main__':main()
