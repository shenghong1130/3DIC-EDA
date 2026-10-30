#!/usr/bin/env python3
"""Read phase-two measurements and create offline reports; never run Router.

Run after benchmark_router.py --compare-stage baseline. --refresh-derived only
replaces this script's derived reports/charts, never source measurements.
"""
import argparse,csv,json,os,statistics,sys
from pathlib import Path
from common import PROJECT,OUTPUTS,safe_label,sha256

ROOT=OUTPUTS/'reports/algorithm_optimization'
REFRESH=False

def read(path):return json.loads(path.read_text(encoding='utf8'))
def write(name,text):
    with (ROOT/name).open('w' if REFRESH else 'x',encoding='utf8',newline='\n') as f:f.write(text)
def number(v):return 'N/A' if v is None else f'{v:.6g}'

def measurement_times(row):
    """Read real times; retired/missing raw runs never trigger reconstruction."""
    directory=Path(row['run_directory'])
    if (directory/'result.json').is_file() and (directory/'run.json').is_file():
        result=read(directory/'result.json');run=read(directory/'run.json')
        return dict(router_elapsed_seconds=result['metrics']['runtime_seconds'],
            router_helper_runtime_seconds=run['runtime_seconds'],
            router_process_runtime_seconds=(run.get('supervisor_exit') or {}).get('runtime_seconds'))
    if row['case']=='open_3d_case001':
        from case001_history import archived_row
        try:
            r=archived_row(row['label'])
        except (KeyError,FileNotFoundError):r=None
        if r is not None:
            if (r['router_sha256'],r['guide_sha256'])!=(row['router_sha256'],row['guide_sha256']):
                raise ValueError('Historical summary identity differs')
            return dict(router_elapsed_seconds=r.get('metrics',{}).get('runtime_seconds'),
                router_helper_runtime_seconds=r.get('runtime_seconds'),router_process_runtime_seconds=None)
    # Some phase-two raw directories were already absent before pruning. Their
    # measured report is independent evidence, never substitute another run.
    prior=ROOT/'performance_comparison.json'
    if prior.is_file():
        matches=[r for r in read(prior).get('official_cases',[]) if all(r.get(k)==row.get(k)
            for k in ['case','label','router_sha256','guide_sha256'])]
        if len(matches)==1:
            return {k:matches[0].get(k) for k in ['router_elapsed_seconds','router_helper_runtime_seconds','router_process_runtime_seconds']}
    return dict(router_elapsed_seconds=None,router_helper_runtime_seconds=None,router_process_runtime_seconds=None)

def main():
    global REFRESH
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',default='paired_steady');p.add_argument('--refresh-derived',action='store_true');a=p.parse_args();safe_label(a.stage);REFRESH=a.refresh_derived
    rows=read(ROOT/'stages'/a.stage/'measurements.json');small=read(ROOT/'small_cases.json')
    actual001=read(ROOT/'case001_comparison.json') if (ROOT/'case001_comparison.json').exists() else []
    if actual001 and len(actual001)!=2:raise RuntimeError('case001 comparison still running; do not publish a partial comparison')
    small+=actual001
    sys.path.insert(0,str(PROJECT/'parameter_tuning'))
    from records import official_metrics  # Reuse real schema, explicit totals only.
    metrics=[('routing_seconds','s'),('peak_rss_bytes','bytes')]+[(k,'s' if k.endswith('_seconds') else 'count') for k in [
        'astar_seconds','expansions','queue_pops','stale_pops','max_dist_nodes','dense_attempts','sparse_attempts','max_workspace_bytes',
        'tsv_seconds','tsv_candidates','heat_values_scanned','heat_scan_seconds','capacity_calls','capacity_cache_hits',
        'rerouted_nets','route_copy_count','route_move_count','backup_seconds','rollback_nets','rollback_seconds','reroute_seconds','route_json_calls']]
    comparison=[];groups={}
    for fixture in ['astar','tsv','reroute']:
        old=[r for r in rows if r['fixture']==fixture and r['stage']=='baseline']
        new=[r for r in rows if r['fixture']==fixture and r['stage']!='baseline'];assert len(old)==len(new)>0
        assert len({r['guide_sha256'] for r in old+new})==1
        assert len({r['normalized_json_sha256'] for r in old+new})==1
        groups[fixture]=(old,new)
        for metric,unit in metrics:
            def values(items):return [r.get(metric,0) if metric in ['routing_seconds','peak_rss_bytes'] else r['profile'].get(metric,0) for r in items]
            ov,nv=values(old),values(new);om,nm=statistics.median(ov),statistics.median(nv)
            comparison.append(dict(fixture=fixture,metric=metric,unit=unit,baseline=om,optimized=nm,
                change_percent=(nm/om-1)*100 if om else None,baseline_min=min(ov),baseline_max=max(ov),optimized_min=min(nv),optimized_max=max(nv)))
    def stat(f,m):return next(r for r in comparison if r['fixture']==f and r['metric']==m)
    def line(f,m):
        r=stat(f,m);pct='N/A' if r['change_percent'] is None else number(r['change_percent'])+'%'
        return f"| {f} / {m} | {number(r['baseline'])} | {number(r['optimized'])} | {pct} |\n"
    # Record official results separately from synthetic fixtures.
    official=[]
    for row in small:
        entry={k:row[k] for k in ['case','version','label','status','official_status','router_sha256','guide_sha256','normalized_json_sha256','peak_memory_bytes','router_peak_rss_bytes']}
        entry.update(official_metrics(row.get('official_report',{})))
        entry.update(measurement_times(row))
        official.append(entry)
    for case in dict.fromkeys(row['case'] for row in small):
        old,new=[r for r in small if r['case']==case];assert old['status']==new['status']=='ROUTED'
        assert old['official_status']==new['official_status']=='PASS'
        for k in ['guide_sha256','normalized_json_sha256']:assert old[k]==new[k]
        for k in ['score','wirelength','congestion','thermal_tsv','status']:assert old['official_report'][k]==new['official_report'][k]
    stage_checks={}
    for path in (ROOT/'stages').glob('*/measurements.json'):
        items=read(path)
        stage_checks[path.parent.name]=all(r['guide_sha256']==groups[r['fixture']][0][0]['guide_sha256'] and r['normalized_json_sha256']==groups[r['fixture']][0][0]['normalized_json_sha256'] for r in items)
    assert all(stage_checks.values())
    official_comparison=[]
    for case in dict.fromkeys(r['case'] for r in official):
        old,new=[r for r in official if r['case']==case]
        for metric,unit in [('router_elapsed_seconds','s'),('router_helper_runtime_seconds','s'),('router_process_runtime_seconds','s'),
                ('peak_memory_bytes','bytes; GNU time process tree'),('router_peak_rss_bytes','bytes; sampled router PID'),
                ('wirelength_score','score'),('congestion_score','score'),('thermal_score','score'),('total_score','score; explicit field only'),
                ('wirelength_dbu','DBU'),('official_overflow','official demand'),('official_status','status')]:
            ov,nv=old.get(metric),new.get(metric);numeric=isinstance(ov,(int,float)) and isinstance(nv,(int,float))
            official_comparison.append(dict(fixture=case,metric=metric,unit=unit,baseline=ov,optimized=nv,
                change_percent=(nv/ov-1)*100 if numeric and ov else None,baseline_min=None,baseline_max=None,optimized_min=None,optimized_max=None))
    repeats=len(groups['astar'][0]);assert all(len(old)==repeats for old,new in groups.values())
    method=(f"{repeats} AB/BA interleaved pairs per controlled fixture; median and full range retained. "
        "C++ steady-clock routing time includes design construction and run(), excludes result/Guide output. "
        "Peak RSS is GNU time for the complete single C++ benchmark, including output. "
        "Both benchmark binaries use -O3 -DNDEBUG and GR_PROFILE; production binaries omit GR_PROFILE. "
        "No CPU affinity/exclusive machine; background load and subsecond timings limit precision. "
        "Compilation finished before the primary paired_steady measurements.")
    payload={'stage':a.stage,'methodology':method,'synthetic_comparison':comparison,'official_comparison':official_comparison,'official_cases':official,
        'stage_equivalence':stage_checks,'raw_measurements':rows,
        'large_cases':{c:'NOT_EVALUATED: not started by this task' for c in ['open_3d_case003','open_3d_case005']}}
    write('performance_comparison.json',json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    with (ROOT/'performance_comparison.csv').open('w' if REFRESH else 'x',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(comparison[0]));w.writeheader();w.writerows(comparison+official_comparison)
    head='| 受控基准 / 指标 | Baseline | Optimized | 变化 |\n|---|---:|---:|---:|\n'
    performance=head+''.join(line(f,m) for f in groups for m in ['routing_seconds','peak_rss_bytes','expansions','astar_seconds','tsv_seconds','reroute_seconds'])
    official_table='| Case | 版本 | 合法性 | Wirelength 分 | Congestion 分 | Thermal 分 | 官方总分字段 |\n|---|---|---|---:|---:|---:|---|\n'
    for r in official:official_table+=f"| {r['case']} | {r['version']} | {r['official_status']} | {number(r['wirelength_score'])} | {number(r['congestion_score'])} | {number(r['thermal_score'])} | {number(r['total_score'])} |\n"
    actual_table='| 真实 Case（每版一次） | 原版 C++ elapsed / 秒 | 优化版 C++ elapsed / 秒 | 原版 GNU time 进程树峰值 / MiB | 优化版 / MiB |\n|---|---:|---:|---:|---:|\n'
    for case in dict.fromkeys(r['case'] for r in official):
        old,new=[r for r in official if r['case']==case]
        actual_table+=f"| {case} | {number(old['router_elapsed_seconds'])} | {number(new['router_elapsed_seconds'])} | {number(old['peak_memory_bytes']/1048576)} | {number(new['peak_memory_bytes']/1048576)} |\n"
    case001_note=('case001 共 3152 Net，结果及相同 900 秒预算、默认配置、无 Trace 的两版本命令见 case001_comparison.json；它的完整 Guide/规范化 JSON/官方分项也全部相同，两版本均 PASS。UI API 已实测 HTTP 200，返回真实 3152 Net、20 层及完整 Pin/路径/Guide/资源。' if actual001 else 'case001 未执行，为 NOT_EVALUATED。')
    bm=read(ROOT/'baseline_manifest.json');om=read(ROOT/'optimized_manifest.json')
    write('astar_analysis.md',f'''# A* 性能分析

保留原 priority_queue、f/Id 比较、邻居顺序、1e-10 容差、margin 扩张、search_limit 和时间预算。不改变 Prim MST 或路径目标。

原实现每次包围盒尝试创建 reserve(4096) 的哈希表，邻居反复 decode/center/查 Layer/Via，并多次哈希访问同一状态。Baseline astar 基准共展开 53,400 节点、弹堆 53,700 次，stale_pops=0，因此未更换堆或宣称减少展开。

新实现按轴缓存非均匀格中心、缓存 Layer/Via，单次查询复用迭代器，平方采用等价乘法。窗口最多 8192 状态时复用 generation 缓冲；超限回退稀疏表。代际回绕清零，不把标记当永久 Closed Set；没有全图 nx×ny×20 数组。容量、Usage/Fixed/History 的语义不变，动态项不缓存。

{head}{line('astar','astar_seconds')}{line('astar','routing_seconds')}{line('astar','expansions')}{line('astar','peak_rss_bytes')}
该表是组合优化版与原版的比较，不能把全部收益归给数组。分阶段构建和原始测量在 stages/astar_v2、tsv、memory、optimized。astar 基准中密集/稀疏两分支各 150 次，展开数与输出均相同。Peak RSS 略升，不能声称所有场景都省内存。局部数组实测最高 {number(stat('astar','max_workspace_bytes')['optimized'])} bytes，硬上限为 8192 个 SearchCell（本机每个 24 bytes）。

新增 C++ 检查用独立 Dijkstra 对照非均匀网格和动态成本，验证非法 Via、generation 复用/回绕、稀疏回退、预算和时间异常。尚未测量真实 case001/003/005 的稀疏分支比例，未改搜索范围策略。
''')
    write('tsv_analysis.md',f'''# TSV 计算分析

Baseline 在每个非固定锚点跨 Die Net 内分别 min_element/max_element 扫全热图。256×256 网格、512 条跨 Die Net 的受控数据共扫描 67,108,864 个值。

热图映射后和 run() 入口计算极值，运行内只读复用；公开 heat 可被直接改写，因此 run() 外每次 select_tsv 都重新扫描。run() 的冻结标记在正常及异常退出时恢复，测试包含索引赋值、std::fill、run() 后修改。并发修改 Router 本身仍不受支持。

候选由 set 改为 vector + sort + unique，保留原 (x,y) 字典序、全部端子格/采样格与严格小于的 tie-breaking。固定共同 M10/M11 格和歧义报错未改；每个 Pin 的距离仍按原顺序累加，未使用可能改变浮点求和的前缀和。

{head}{''.join(line('tsv',m) for m in ['tsv_seconds','heat_scan_seconds','heat_values_scanned','tsv_candidates','routing_seconds','peak_rss_bytes'])}
扫描量降为一次 131,072 个值，候选数 14,336 不变，所有 TSV 锚点、Guide、完整规范化 JSON 相同。此收益对热图较大且跨 Die Net 较多的数据尤为明显；不等于官方 Thermal 分数提高。
''')
    write('memory_analysis.md',f'''# 内存与资源分析

保留 Route.nodes/edges/guides、Pin、Resource capacity/usage/fixed/history 与完整流式结果 Schema。未删减 UI 数据，未改变 Guide 合并或验证规则。

减少未启用/已截断 Trace 的拓扑、搜索路径和 route_json 临时对象；启用 Trace 时事件字段不变。route_json 统计包含最终逐 Net 输出，因此优化版计数仍至少为 Net 数，不能把这一必要序列化消除。

格中心占 O(nx+ny)，层/Via 查表固定 21 项。容量仅按各层首选方向对应的一条轴缓存，合计预算 8 MiB，不足的层回退原 count_tracks；直接 capacity API 不读冻结表。半开区间、最后一格包含末边界及轨道累加顺序不变。局部搜索数组最多约 192 KiB，较大窗口的 sparse map 仍可能很大，search_limit 未提高。

{head}{''.join(line(f,m) for f in groups for m in ['peak_rss_bytes','route_json_calls'])}
重布基准 Peak RSS 明显降低，astar/tsv 略升，符合小缓存换时间的取舍；没有证据证明 case003/005 的峰值会同比下降。RSS 是 GNU time 对完整 C++ 子进程的峰值，包含序列化，无法精确分离某个分配的峰值。真实 run_case 的 peak_memory_bytes 包括监督进程树，另列于 JSON，不能与此混成同一口径。

异常时恢复依赖已提交路由的资源条目均存在且 Usage 有效；对这种正常不变量，恢复无需插入资源或构造 JSON。进程被强杀、WSL 停止或硬件故障仍可能来不及执行 C++ 恢复，不能称为已经解决所有 OOM/异常退出。
''')
    write('reroute_analysis.md',f'''# 增量重布与回滚

Baseline 每轮深拷贝全部 routes，实际热 Net 又复制一次；拒绝后连未修改路由也拆除/恢复和构造 Trace JSON。1800 Net 基准只有 1 个固定端子热点，两轮只重布 2 次，但产生 3602 次 Route 深拷贝和 3600 条回滚。

reroute_pass 仅为实际热 Net 建立 Undo 项：先分配记录，再 Move 原 Route，清空原槽位（避免残留 cross/tsv 标志），扣旧占用，求新路径并提交。接受时释放备份；拒绝时只恢复修改项，逐位置进度计数仍完成全 Net 扫描。严格保持 accepted = now < best，History 累加不回滚。

{head}{''.join(line('reroute',m) for m in ['rerouted_nets','route_copy_count','route_move_count','backup_seconds','rollback_nets','rollback_seconds','reroute_seconds','routing_seconds','peak_rss_bytes'])}
异常回滚恢复本轮所有已改变项，不只当前一条；无需恢复的 Net 完全不动。普通回滚只发出改变项的 net_removed/net_added，故 Trace 行数可能更少，但真实末态一致，不补造搜索过程。部分统计的 reroute_seconds 只覆盖实际热 Net 的拆旧/求路/提交，不包含整轮扫描、History 或独立 rollback，不能直接当整轮总时长。

新增测试验证整轮拒绝后节点/边/Guide/TSV 完全一致、Usage 非负、Fixed/Capacity 不变、History 保留；第一个 Net 已成功重布、第二个跨 Die Net 搜索受限异常时全轮恢复；还覆盖接受改善轮、时间预算、Trace 末态。真实 toy/sample001 的完整 Trace 也与最终 JSON 一致。
''')
    # Build visual artifacts using the same existing optional Matplotlib stack.
    previous={k:os.environ.get(k) for k in ['OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']}
    try:
        for k in previous:os.environ[k]='1'
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    finally:
        for k,v in previous.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
    target=ROOT/'figures';target.mkdir(exist_ok=True)
    for metric,scale,name,ylabel in [('routing_seconds',1,'runtime_comparison.png','Routing time (seconds)'),('peak_rss_bytes',1/1048576,'memory_comparison.png','Process peak RSS (MiB)')]:
        fig,ax=plt.subplots(figsize=(9,4.5));x=list(range(3));width=.34
        for j,(key,color) in enumerate([('baseline','#64748b'),('optimized','#0d9488')]):
            vals=[stat(f,metric)[key]*scale for f in groups];xs=[i+(j-.5)*width for i in x]
            err=[[stat(f,metric)[key]*scale-stat(f,metric)[key+'_min']*scale for f in groups],[stat(f,metric)[key+'_max']*scale-stat(f,metric)[key]*scale for f in groups]]
            bars=ax.bar(xs,vals,width,label=key,color=color,yerr=err,capsize=3)
            ax.bar_label(bars,fmt='%.3f',padding=3,fontsize=8)
        ax.set_xticks(x,list(groups));ax.set_ylabel(ylabel);ax.set_title(f'Controlled fixtures: median of {repeats} AB/BA pairs (range whiskers)');ax.legend();ax.grid(axis='y',alpha=.2);fig.tight_layout()
        if (target/name).exists() and not REFRESH:raise FileExistsError(target/name)
        fig.savefig(target/name,dpi=150);plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(12,4.5))
    for ax,(fixture,(old,new)) in zip(axes,groups.items()):
        for j,key in enumerate(['astar_seconds','tsv_seconds','backup_seconds','rollback_seconds']):
            vals=[statistics.median(r['profile'].get(key,0) for r in group) for group in [old,new]]
            ax.bar([i+(j-1.5)*.18 for i in [0,1]],vals,.18,label=key.replace('_seconds',''))
        ax.set_xticks([0,1],['baseline','optimized']);ax.set_title(fixture);ax.grid(axis='y',alpha=.2)
    axes[0].set_ylabel('Profile time (seconds)');axes[-1].legend(fontsize=8);fig.suptitle('Selected non-overlapping timers (not a complete runtime sum)');fig.tight_layout()
    if (target/'stage_breakdown.png').exists() and not REFRESH:raise FileExistsError(target/'stage_breakdown.png')
    fig.savefig(target/'stage_breakdown.png',dpi=150);plt.close(fig)
    commands='''
## 待执行命令：PowerShell + WSL

```powershell
Set-Location -LiteralPath 'D:\\我\\大学\\program\\3DIC EDA\\EDA\\code_3DIC EDA'
# 按 Case 依次手动执行；必须换用未占用 Label。case005 先核实 Thermal 来源。
python scripts/run_case.py --case open_3d_case001 --backend wsl --label phase2_baseline_01 --router build/router_baseline --config configs/default.json --time-limit 900
python scripts/run_case.py --case open_3d_case001 --backend wsl --label phase2_optimized_01 --router build/router_optimized --config configs/default.json --time-limit 900
python scripts/run_case.py --case open_3d_case003 --backend wsl --label phase2_baseline_003_01 --router build/router_baseline --config configs/default.json --time-limit 1800
python scripts/run_case.py --case open_3d_case003 --backend wsl --label phase2_optimized_003_01 --router build/router_optimized --config configs/default.json --time-limit 1800
python scripts/run_case.py --case open_3d_case005 --backend wsl --label phase2_baseline_005_01 --router build/router_baseline --config configs/default.json --time-limit 1800
python scripts/run_case.py --case open_3d_case005 --backend wsl --label phase2_optimized_005_01 --router build/router_optimized --config configs/default.json --time-limit 1800
```

预算只是示例，不保证完成；相同 Case 两版本预算必须相同。仅对对应 run.json 为 ROUTED、returncode=0、Guide 完整的记录执行下面命令；case005 来源未确认时不可当正式可信成绩。

```powershell
python scripts/evaluate.py --case open_3d_case001 --label phase2_baseline_01 --backend wsl --engine latest --timeout 900
python scripts/evaluate.py --case open_3d_case001 --label phase2_optimized_01 --backend wsl --engine latest --timeout 900
python scripts/evaluate.py --case open_3d_case003 --label phase2_baseline_003_01 --backend wsl --engine latest --timeout 1800
python scripts/evaluate.py --case open_3d_case003 --label phase2_optimized_003_01 --backend wsl --engine latest --timeout 1800
python scripts/evaluate.py --case open_3d_case005 --label phase2_baseline_005_01 --backend wsl --engine latest --timeout 1800
python scripts/evaluate.py --case open_3d_case005 --label phase2_optimized_005_01 --backend wsl --engine latest --timeout 1800
```

## 二进制、重建与第一阶段

`build/router_baseline` 为修改前独立构建，与根目录 router 字节一致；原始额外备份在 build/router_original_70ddc978c6f4。`build/router_optimized` 是生产优化版（无 GR_PROFILE）；最后一次仅修复缩进警告的独立构建 `build/router_optimized_checked` 与它字节相同。

不要运行覆盖根目录 router 的 build.sh 来比较新旧。当前源码可按相同参数重建优化版到一个不存在的目标，直接在 PowerShell：

```powershell
wsl -d Ubuntu -- bash -lc "cd '/mnt/d/我/大学/program/3DIC EDA/EDA/code_3DIC EDA' && test ! -e build/router_optimized_next && g++ -std=c++17 -O3 -DNDEBUG -Wall -Wextra -I include src/main.cpp src/parser.cpp src/router.cpp -o build/router_optimized_next"
$LASTEXITCODE
```

Baseline 构建命令在 baseline_manifest.json；只应在原版源码状态使用，并选择新文件名，不能将当前优化源码编成“Baseline”。基线来源保存在 source_before.json 与 Git HEAD，不维护另一套 Router 源码目录。恢复运行选择只需 `--router build/router_baseline`，根目录原二进制无需更换。

第一阶段代码和旧计划未修改，28 项临时夹具测试通过；没有重写旧 Best Config。工作期间发现旧 phase1 派生报告又发生并发更新，被原有源码指纹校验置为 BLOCKED，新增的这些现有改动也保留，见 concurrent_phase1_changes.json；本任务未执行旧批次 resume/dry-run/report-only。旧批次同时冻结源码/工具/环境指纹，仅换回旧二进制不足以绕过保护。若要续跑旧批次，需原版源码环境，不修改旧指纹。新算法使用新的 Experiment ID：

```powershell
python parameter_tuning/tune_parameters.py --case open_3d_case001 --experiment phase2_case001 --router build/router_optimized --backend wsl --dry-run
# 确认新计划后由用户开始顺序运行，后续使用同一新 ID --resume。
python parameter_tuning/tune_parameters.py --experiment phase2_case001 --resume --max-runs 1
```

这里仅提供命令，没有创建/执行新调参批次。现有 phase1 没有 best_config.json 可作可靠最佳参数对照，未编造。通用 run_case.py 的 source_sha256 是运行时工作区快照：本次 Baseline Run 也会记录优化中的工作区；实际 Baseline 二进制来源应查 baseline_manifest/source_before，与二进制 SHA 联合核对。

## 局限与下一步

没有自动运行 case003/005，它们在本次比较中均为 NOT_EVALUATED；任务开始前已在外部运行的 case005_limit5m03 未被启动、终止或改写，其监控自行增长不属于本次实验。已有历史成绩不混作公平对照。

本轮属于纯执行效率优化，没有减少 A* 展开数或保证大型 Case 成功。未尝试前缀求和、新堆、启发范围调整及全图数组，避免改变求和、tie-breaking 或峰值。对于大窗口仍有 sparse map、堆和 Route set 成本；热图/容量缓存会略增某些小例 RSS。WSL/宿主异常风险仍在，不能将 UNKNOWN 强行判为 OOM。

算法质量下一阶段建议先校准 Pin/OBS 固定需求和官方容量模型，再用树到点/Steiner 连接研究重复路径；单独做质量实验，保留本次可复现性能基线，再考虑 TSV 全局热密度或多目标接受。
'''
    summary=f'''# 第二阶段 Router 优化报告

已在原 src/router.cpp 上完成 A* 状态访问、TSV 热图/候选、资源/Trace 临时内存和增量重布优化。未建立第二套算法工程，未改默认配置、Parser/主入口、UI 或官方评分器。

## 版本与保护

本地与远端 HEAD 均为 `{bm['local_head']}`。任务开始有 3 份 phase1_case001 报告未提交修改，未恢复或覆盖；后来发现旧批次派生报告又更新为 BLOCKED，这些并发改动保留（见 concurrent_phase1_changes.json），不宣称旧报告字节完全未变。Baseline SHA256 `{bm['original_router_sha256']}`；Optimized SHA256 `{om['sha256']}`。生产构建同为 g++ 11.4、C++17、-O3 -DNDEBUG -Wall -Wextra。基线清单、源码文本哈希快照与最终清单分别见 baseline_manifest.json、source_before.json、optimized_manifest.json。

## 受控性能结果

{method}

{performance}
负百分比表示减少。时间以秒、RSS 以 bytes 计；CSV/JSON 同时保留 min/max。测试规模为 astar 300 Net、96×96，tsv 512 Net、256×256，reroute 1800 Net、96×96。不存在官方分数意义，不能按倍数外推大型 Case。

astar 展开数不变，收益来自每节点的处理成本；TSV 热图扫描复用和重布备份/回滚消除最明确。重布场景内存降低，astar/tsv 内存小幅上升，因此没有选择“所有内存都下降”的表述。

## 真实小型用例与官方验证

{official_table}
原始 latest 报告无显式官方总分，表中为 N/A，未在此合成。所有分项、Wirelength/Congestion/Thermal 原始值完全相同。当前工具是官方提供的 GRT Guide/mock-DRT proxy，synthetic Thermal 警告保留，不是详细布线 sign-off 或隐藏用例保证。真实运行 Label、输入/二进制哈希和报告位于 small_cases.json 及其 outputs/runs 目录。

{actual_table}
C++ elapsed 来自 result.metrics.runtime_seconds（包括解析和此前输出阶段，记录时尚未完成最后 Guide 写入），不是单独 A* 时间；完整 helper/监督器运行时间另列在 JSON/CSV，进程树峰值包括监控 Python，不能与合成基准单 C++ 进程峰值混用。每个真实 Case 各版只运行一次，不据此宣称稳定倍数。toy 本次 elapsed 略升约 0.8%，不能保证短用例也更快。{case001_note}

toy/sample001 两版本均 ROUTED、退出 0，Guide SHA256 一致，完整 JSON 仅剔除 metrics.runtime_seconds 后哈希一致；所有 Net/Pin/Route/资源/热图/统计字段保留。完整 Trace 应用后与最终路由一致。现有 UI 8501 的新运行 API 均 HTTP 200，各含 203 Net、20 层和完整 Pin/边/Guide/Resource，见 ui_validation.json；未改 UI 代码或声称重测所有浏览器交互。

C++ 原有与新增检查通过（包括独立 Dijkstra、非均匀坐标、缓存/稀疏回退、预算、固定/唯一 TSV、接受/拒绝/异常恢复、Trace、JSON 临时发布）。Python 工作流 6 项、诊断 18 项、第一阶段 28 项均通过。统计编译仅用 GR_PROFILE，热循环无日志 I/O；生产编译不含计数计时。各阶段源码哈希、编译/核心日志、原始测量均在 stages/。最初 harness 遇 WSL 混合编码日志解析失败，及稀疏测试轨道范围错误，均保留原失败记录；修复后 astar_v2 起各完整阶段通过，未把失败记录列为有效成绩。

![受控运行时间](./figures/runtime_comparison.png)

![受控进程内存](./figures/memory_comparison.png)

![部分计时分解](./figures/stage_breakdown.png)

分解图为 A*/TSV/备份/回滚等互不重叠计时，不含所有阶段，也不把包含 A* 的 reroute_seconds 再叠加求和。
{commands}
'''
    write('optimization_summary.md',summary)
    print(json.dumps({'report':str(ROOT/'optimization_summary.md'),'comparisons':len(comparison),'official_rows':len(official),'stage_equivalence':stage_checks},ensure_ascii=True))

if __name__=='__main__':main()
