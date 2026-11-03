"""Render V4.2 evidence, uncertainty and release gates; never route or score."""
import argparse,json,re,subprocess
from pathlib import Path
from common import PROJECT,sha256,load_run
from diagnostics import json_file
from scoring import REFERENCE_LATEST,reference_directory
from evaluate_reference_20261103 import source_identity

ROOT=PROJECT/'outputs/reports/v4_2'
def write(name,data):
    path=ROOT/name;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(data if isinstance(data,str) else json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
def table(head,rows):
    return '| '+' | '.join(head)+' |\n|'+ '|'.join('---' for _ in head)+'|\n'+''.join('| '+' | '.join('N/A' if x is None else str(x) for x in row)+' |\n' for row in rows)
def report(case,label):
    p=reference_directory(case,label,scorer=REFERENCE_LATEST)
    return json_file(p/'reference_report.json') if p else {}
def rules():
    source='EDA/补充资料/3DIC_Scoring_Rules_Complete_20261103.docx'
    specs=[
      ('W01','20261020','min(1,1/L)；归一化 L 的完整实现未公开','UNKNOWN','百分制保持 N/A；不猜测 FLUTE 三维基准','reference_scorer/main_20261103.cpp::Wirelength','test_reference_scorer_20261103'),
      ('P01','20261027','每 Geometry 使用实际层，支持多层、多 Port','CONFIRMED','继承真实层几何解析，无首 Geometry 投影','src/parser.cpp; physical_rectangles','rules_20261103; core; physical_resources'),
      ('P02','20261020','Pin/OBS Track 集合并集，重复轨道去重','CONFIRMED','继承条带位图并集，不减少固定需求','PhysicalModel20261103::build','rules_20261103; physical_resources'),
      ('P03','20261020','DEF IO PINS 计入固定需求','CONFIRMED','继承 IO 全部实际几何','physical_rectangles','rules_20261103'),
      ('P04','20261020','Track 在当前 GCell 内裁剪；外边界单独处理','CONFIRMED','保留半开格边界和外侧闭边界','PhysicalGrid; PhysicalModel20261103','rules_20261103; physical_resources'),
      ('P05','20261029','无 Track Pin 至少占一轨资源','CONFIRMED','保留最小占用和共享虚拟零容量 token，不虚增 Capacity','PhysicalModel20261103::build','rules_20261103'),
      ('P06','20261103','修复无条件归左 GCell；新轨道/格选择公式未公开','MODEL_ASSUMPTION','V4 已在几何所属格内取最近轨道；新增 nearest/lower/upper_in_cell 独立诊断，不称官方修复算法','no_track_pin_index','rules_20261103; models/case005'),
      ('G01','20261029','Guide 与合法 GCell 正面积重叠','CONFIRMED','继承矩形、坐标、覆盖、Guide 完整性检查','Router::validate_v34; reference_scorer/main_20261103.cpp','v42_regression; test_reference_scorer_20261103'),
      ('G02','20261020','走线符合 LEF Preferred Direction','CONFIRMED','继承按层方向的图边和 Guide 检查','Router::search_impl; Router::make_guides','v42_regression; core'),
      ('G03','20261029','Guide、全部 Pin、Via、唯一 TSV 连通','CONFIRMED','继承完整 Net 验证和独立 Guide 图遍历','Router::validate; Router::validate_v34','v42_regression; test_reference_scorer_20261103'),
      ('G04','20261103','实际布线发生在零容量格报错（会议纪要）','MEETING_SUMMARY','V4 的高代价可走改为 V4.2 搜索、接入、最终校验及参考评分拒绝；固定需求仍保留','src/router.cpp; router_v3_4_impl.hpp; main_20261103.cpp','rules_20261103; test_reference_scorer_20261103'),
      ('T01','20261027','5×5 µm TSV 在 M10/M11 按物理轨道占用','CONFIRMED','继承足迹、轨道、同 Net max 与跨 Net 累加；精确官方组合细节仍未知','Router::prepare_v34_route','v42_regression; core; independent TSV audit'),
      ('T02','20261029','TSV 中心 GCell 禁入 Pin/OBS','CONFIRMED','继承硬禁入、候选/全局移动/回滚共用约束，无固定 Pin 豁免','Router::tsv_legal','v42_regression; audit_submission_tsv.py'),
      ('T03','20261103','接口在 M10/M11；外框邻格、其他层禁入精确定义未公开','MODEL_ASSUMPTION','保留 ALL-layer 中心禁入保守策略；邻格矩阵只诊断，不冒称官方硬规则','Router::tsv_legal; audit_submission_tsv.py','independent TSV audit A_D/B_D'),
      ('T04','20261020','本网 Pin GCell 豁免 Guide/TSV 额外需求，放置禁令仍适用','CONFIRMED','保留 own_pin_cells 和 Net 完整需求抵扣，不扩展为 TSV 豁免','Router::prepare_v34_route','rules_20261103; physical_resources; reference fixtures'),
      ('C01','20261029','拥塞 Bug 修复和加强 Overflow 惩罚，完整百分制公式未知','UNKNOWN','保留真实资源统计、Overflow 和 Potential；Congestion Score=N/A','main_20261103.cpp','test_scoring_20261103'),
      ('C02','20261103','容量占满时忽略额外需求的适用条件未公开','UNKNOWN','不擅自截断 routing demand 或抹去 Overflow','PhysicalModel20261103; reference resources.csv','rules_20261103'),
      ('H01','20261103','热投影填充、G_case 及最终总分细节未公开','UNKNOWN','保留可校准 Pearson；百分制 Thermal/总分=N/A','DensityCorrelation; main_20261103.cpp','v42_regression; test_scoring_ui.js')]
    rows=[]
    for key,date,rule,clarity,behavior,code,test in specs:
        rows.append(dict(id=key,date=date,source_file=source,source_sha256=sha256(PROJECT.parent/('补充资料')/Path(source).name),source_provenance='ARCHIVED_COMPILATION; 20261103 raw update and original meeting screenshots not separately supplied',rule=rule,clarity=clarity,v4_implementation=code,scorer_implementation='reference_scorer/main_20261103.cpp; include/physical_resources_20261103.hpp',modification_required=key in {'P06','G04'},before_after=behavior,affected_cases=['toy','sample001','open_3d_case001','open_3d_case003','open_3d_case005'],regression_tests=test,validation='See test receipts and per-Case reports; unknown official details remain unverified'))
    write('rules/rule_matrix.json',rows)
    write('rules/rule_matrix.md','# 规则与证据矩阵\n\n20261103 汇总是转录归档，原始当天更新及会议截图未单独提供；不能据此恢复未公布算法。原 20261027/29 Word 与来源 SHA 见 source_texts.json。\n\n'+table(['ID','日期','明确程度','规则','修改/保留行为','源码','测试'],[[r['id'],r['date'],r['clarity'],r['rule'],r['before_after'],r['v4_implementation'],r['regression_tests']] for r in rows]))

def generate():
    rules();build=json_file(ROOT/'build.json');results={};gates={};summaries=[]
    for suffix in ['001','003','005']:
        case='open_3d_case'+suffix;rows=[]
        for version in ['v3_6','v4','v4_2']:
            label='case'+suffix+'_'+version;folder=PROJECT/'outputs/runs'/case/label
            run=load_run(folder) if (folder/'run.json').exists() else {};score=report(case,label)
            gate=json_file(ROOT/'runtime'/case/'resource_gate.json') if version=='v4_2' else {}
            status=run.get('status',gate.get('status','NOT_STARTED'))
            if version=='v4_2':
                audit=json_file(ROOT/'audit'/case/'summary.json');conflicts=audit.get('rule_matrix_conflicting_candidates',{}).get('A_D')
                results[case]={'router_status':status,'run':run,'reference':score,'guide_sha256':sha256(folder/'route.guide') if (folder/'route.guide').is_file() else None,'audit':audit,'resource_gate':gate}
                gates[case]={'routed':run.get('status')=='ROUTED' and run.get('returncode')==0,'reference_valid':score.get('static_legality')=='REFERENCE_VALID','center_tsv_conflicts_zero':conflicts==0,'complete_unique_audit':bool(audit) and not any(audit.get('counts',{}).get(k,0) for k in ['ambiguous_nets','missing_tsv_nets'])}
            row=[version,status,score.get('static_legality'),score.get('wirelength_dbu',0)/score.get('dbu_per_micron',1) if score else None,score.get('overflow'),*[score.get('by_layer',{}).get(l,{}).get('overflow') for l in ['1','10','11']],score.get('fixed_baseline_overflow'),score.get('routing_induced_overflow'),score.get('normalized_overflow_squared'),score.get('thermal_correlation'),run.get('runtime_seconds'),run.get('router_peak_rss_bytes') or run.get('peak_memory_bytes')]
            rows.append(row)
        write('case'+suffix+'_comparison.md','# '+case+' 同口径比较\n\n'+table(['版本','Router','REFERENCE_20261103','WL µm','Overflow','M1','M10','M11','Fixed baseline','Routing incremental','Potential','Pearson','Router s','Peak bytes'],rows)+'\nN/A 表示未运行/缺少同身份报告，不能用 20261029 指标补表。原官方 V3.5=45.39、V3.6=45.92 不改写，也不能与本表当作同一评分模型。\n\n```json\n'+json.dumps(results[case].get('resource_gate',{}),ensure_ascii=False,indent=2)+'\n```\n')
        n=results[case];summaries.append([case,n['router_status'],n['reference'].get('static_legality'),n['reference'].get('overflow'),n['reference'].get('thermal_correlation'),n['guide_sha256']])
    ready=all(all(g.values()) for g in gates.values())
    source=PROJECT/'outputs/submissions/submission_v4_2_source.zip';guide=PROJECT/'outputs/submissions/3DIC_guide_v4_2.zip'
    decision={'version':'v4_2','status':'CANDIDATE','release_status':'NOT_RELEASED','base_commit':'3f6cedd2318e0fded47fa2b9efd84e7d23554b6f','router_sha256':build.get('sha256'),'config_sha256':build.get('config_sha256'),'reference_source_sha256':source_identity()[0],'official_status':'NOT_EVALUATED','official_score':None,'official_track_assignment_confirmed':False,'cases':{k:{'router_status':v['router_status'],'reference_status':v['reference'].get('static_legality'),'guide_sha256':v['guide_sha256']} for k,v in results.items()},'gates':gates,'all_cases_passed':ready,'source_zip':str(source) if source.exists() else None,'guide_zip':str(guide) if guide.exists() else None,'commit_push_authorized_only_after_all_required_checks':True,'git_release_performed':False}
    binaries={x['reference'].get('reference_scorer_sha256') for x in results.values() if x['reference']}
    decision.update(reference_scorer_sha256=next(iter(binaries)) if len(binaries)==1 else None,
                    local_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT,encoding='utf8').strip(),
                    v4_2_commit=None,git_worktree='UNCOMMITTED',browser_visual_validation='NOT_EXECUTED: browser host unavailable')
    write('decision.json',decision)
    final={}
    for case,value in results.items():
        r=value['run'];s=value['reference'];audit=value['audit']
        label=case.removeprefix('open_3d_')+'_v4_2';directory=PROJECT/'outputs/runs'/case/label
        final[case]={'router_status':value['router_status'],'completed_nets':s.get('evaluated_nets'),'expected_nets':s.get('nets'),'complete_guide':r.get('status')=='ROUTED' and r.get('returncode')==0 and (directory/'route.guide').is_file(),'reference_legality':s.get('static_legality'),'wirelength_um':s.get('wirelength_dbu',0)/s.get('dbu_per_micron',1) if s else None,'overflow':s.get('overflow'),'by_layer':s.get('by_layer',{}),'thermal_correlation':s.get('thermal_correlation'),'router_runtime_seconds':r.get('runtime_seconds'),'router_peak_rss_bytes':r.get('router_peak_rss_bytes') or r.get('peak_memory_bytes'),'tsv_center_conflicts':audit.get('rule_matrix_conflicting_candidates',{}).get('A_D'),'audit_classification':audit.get('classification'),'guide_sha256':value['guide_sha256'],'guide_path':str(directory/'route.guide') if value['guide_sha256'] else None,'reference_report_path':str(reference_directory(case,label,scorer=REFERENCE_LATEST)/'reference_report.json') if s else None}
    write('final_results.json',final)
    write('summary.md','# V4.2 真实验证汇总\n\n'+table(['Case','Router','参考合法性','Overflow','Pearson','Guide SHA256'],summaries)+'\nV4.2 基于真实 V4，保留三项优化及事务保护。百分制分数与官方总分为 N/A。原官方评分器、旧参考源码及历史结果保留。\n\n全部 Case 门槛：'+str(ready)+'。未全部验证时，不执行 commit/push，不删除远程分支，不生成缺少 Guide 的评分包。\n\n20261103 没有公布新 Track/GCell 归属算法；本实现显式保留 nearest_in_cell 模型假设，并提供 lower/upper_in_cell 诊断，不能称为复现官方最新模型。\n')
    write('v4_to_v4_2_changes.md','# 实际改动\n\n继承 V4 Layer Escape、高 Pin 局部修复、区域 TSV 调度、热优化和原子回滚。新增 GR_V4_2；搜索端点/邻接点、Pin 接入、TSV 中心与最终 route/footprint 验证拒绝零容量实际布线。\n\n无 Track Pin 在几何所属 GCell 内归属，而不是无条件左格；此机制 V4 已存在。V4.2 不虚构一个新的官方 Tie-Break，改为独立模型类及三种标注假设。\n\nConfig JSON 参数与 V4 完全相同；V4.2 精确字节另有 SHA。CMake/build.sh/run.sh 真正启用全部五个宏；run.sh 校验二进制和源码收据，避免误用旧 router。源包包括必要 Config 与参考源文件，禁止覆盖旧 ZIP。\n')
    write('physical_model_changes.md','# 物理模型与边界\n\n旧 physical_resources.hpp 和 REFERENCE_20261029 冻结。新 physical_resources_20261103.hpp 复用分层条带位图、流式几何、Pin/OBS 集合并集、所有实际 Layer/Port、IO Pins 和固定工作区。\n\nNearest/lower/upper 都在几何实际所属 GCell 内挑选 Track，绝不把“左 Track”当“左 GCell”。距离相等取较小轨道只是默认模型假设。零容量固定 Pin 保留一份共享虚拟需求；实际布线另行拒绝，既不虚增 Capacity，也不删除固定需求。\n\nTSV 仍为 5×5 µm，M10/M11 真实足迹；同 Net max 与跨 Net 求和继承既有定义。ALL-layer 中心禁入和外框邻格规则保留保守/模型依赖标签，不冒称正式细节。模型诊断见 models/。\n')
    model_rows=[];model_groups={}
    for path in sorted((ROOT/'models/case005').rglob('reference_report.json')):
        model=json_file(path);m=model.get('by_layer',{}).get('1',{})
        model_groups.setdefault(model.get('pin_assignment_policy'),[]).append((path,model))
    model_evidence={}
    for policy,items in model_groups.items():
        path,model=items[0];m=model['by_layer']['1'];consistent=all(x['by_layer']==model['by_layer'] for _,x in items)
        model_rows.append([policy,m.get('fixed_demand'),m.get('fixed_baseline_overflow'),model.get('runtime_seconds'),model.get('peak_rss_bytes'),len(items)])
        model_evidence[policy]={'reports':[str(p) for p,_ in items],'repeated_fixed_by_layer_consistent':consistent,'by_layer':model['by_layer'],'reference_source_sha256':model['reference_source_sha256']}
    write('model_comparison.json',model_evidence)
    with (ROOT/'physical_model_changes.md').open('a',encoding='utf8') as f:
        f.write('\ncase005 只读固定模型实测：\n\n'+table(['轨道假设','M1 fixed demand','M1 fixed baseline Overflow','秒','Peak bytes','诊断次数'],model_rows)+'\n这几种模型比较的是同格内轨道选择；相同总量不能证明逐格分布相同，更不能恢复官方未公布的 GCell 归属。lower 的独立重复诊断保留，两次分层固定统计一致；它们没有输入 Guide，不是重复 Router 或 Guide 评分。未执行 case005 V4.2 Router，因此不能把固定需求变化当作算法成绩提升。默认 M1 fixed demand 与 V4/20261029 模型相同，为 7,606,297；没有伪造官方修复后的数值。\n')
    test_rows=[]
    for p in sorted((ROOT/'tests').glob('*.receipt.json')):
        r=json_file(p);test_rows.append([p.stem,r.get('returncode'),r.get('seconds')])
    write('unit_tests.md','# 测试执行证据\n\n'+table(['测试/构建','退出码','秒'],test_rows)+'\n旧 V4/V3.6/V3.5、core、物理资源与新版专项测试分开构建执行。Python/JS/API/源码 ZIP 的独立收据另见本目录。合成测试不充当比赛成绩。\n')
    write('runtime_validation.md','# 顺序运行与资源\n\n'+table(['Case','状态','秒','Peak RSS bytes'],[[k,v['router_status'],v['run'].get('runtime_seconds'),v['run'].get('router_peak_rss_bytes') or v['run'].get('peak_memory_bytes')] for k,v in results.items()])+'\n正式 Label 每个只启动一次，未重试。case005 要求主机 Commit 与 WSL MemAvailable 均至少 6 GiB；门槛不满足属于 NOT_STARTED_RESOURCE_GATE，不是 Guide 非法。原始进度、内存和退出证据在 outputs/runs 与 runtime/。\n')
    write('scoring_summary.md','# 评分身份与规则隔离\n\nREFERENCE_20261103 源码 SHA：`'+source_identity()[0]+'`。所有同口径对比读取新版独立目录；20261029 历史原位保留。Scorer binary、输入、Router、Config、Guide SHA 和内存/耗时在每份 invocation/report 中。\n\nWirelength 仍含旧 ELF 的 Via/TSV 3/6 代理假设，精确三维参考树和新版百分制公式未公开，故各百分制和最终总分 N/A。资源 Fixed/Routing/Overflow 不使用人为拟合抵扣。\n')
    write('ui_validation.md','# UI 验证\n\n评分选择新增 REFERENCE_20261103 并设为默认；旧 20261029、原版官方入口继续存在。新旧评分规则、源码、输入或模型参数不同的 A/B 比较明确禁止直接比较。百分制缺失为 N/A，不以零分代替。\n\n评分接口只读小 JSON；未增加读取完整资源 CSV 的逻辑。result_store.py、app.js 的 2D/3D 几何逻辑未改，Net 按需读取继续保留。实际 HTTP/JS 检查见 tests/，未执行的浏览器渲染项目不会声称通过。\n')
    http=json_file(ROOT/'ui_http_validation.json')
    with (ROOT/'ui_validation.md').open('a',encoding='utf8') as f:
        f.write('\n'+table(['实际请求','HTTP','bytes','秒'],[[x['path'],x['http_status'],x['bytes'],x['seconds']] for x in http.get('checks',[])])+'\n浏览器视觉交互验收：'+http.get('browser_visual_verification','NOT_EXECUTED')+'。任务专用测试服务器已关闭，未占用 8501。\n')
        live=json_file(ROOT/'ui_real_result_validation.json')
        f.write('\n真实 result.json 全文件索引与单 Net 读取：\n\n'+table(['Case','文件 bytes','Net 总量','跨 Die Net','索引秒','概要 bytes','选中 Net bytes','Python peak working set'],[[x['case'],x['real_file_bytes'],x['all_nets'],x['cross_die_nets'],x['index_seconds'],x['indexed_summary_bytes'],x['selected_net_bytes'],x['python_memory']['PeakWorkingSet64']] for x in live.get('checks',[])])+'\n概要中的 nets/resources 均为空数组，单 Net 按偏移读取；原结果文件只读。临时 SQLite 索引树自动清理。此项是数据链路验证，不冒充浏览器视觉验收。\n')
    write('submission_validation.md','# 提交产物验证\n\nGuide ZIP 需三个正式 Guide 全部完整、合法审计和参考检查通过，缺少任何一个不生成。包内命名依据已成功 prediction_result_3_6.zip 的根目录 `<case>.output.guide`，不复制其 ingestion 回执冒充新成绩。\n\n源码 ZIP 由本次脚本创建，包含 V4.2 Config、全部必要头文件、build.sh/run.sh 以及 CMake 所需参考源文件，不含旧 Router。必须在临时空目录重编译并运行小型 Case；实际收据见 source_validation.json，缺失即尚未验证。\n')
    source_proof=json_file(ROOT/'source_validation.json')
    with (ROOT/'submission_validation.md').open('a',encoding='utf8') as f:
        f.write('\n```json\n'+json.dumps(source_proof,ensure_ascii=False,indent=2)+'\n```\n\n正式 CMake 和 build.sh 采用不同编译配方，ELF SHA 不同。源码 ZIP 内全部 src/include 与正式源码逐文件相同，宏和 Config 已核对，toy/sample001 Guide 逐字节相同；只以实际证据登记独立的 CANDIDATE 重构建收据，不声称两个 ELF 字节相同或所有大型 Case 输出必然相同。没有 Router/Scorer 历史文件被覆盖。\n')
    git={}
    for args in [['git','branch','--show-current'],['git','rev-parse','HEAD'],['git','status','--short'],['git','branch','-r']]:
        git[' '.join(args)]=subprocess.check_output(args,cwd=PROJECT,encoding='utf8')
    write('git_release.md','# Git 整合与发布状态\n\n本地 main 从 f9db70d 快进整合到 3f6cedd，保留 V4 两个原提交。连接器核验过远程 main/V4/V3 分支；Git CLI 连接曾超时。\n\n只有全部必要验证通过后才允许正式 commit、普通 push 和分支删除；当前未达条件时保留全部分支。\n\n```json\n'+json.dumps(git,ensure_ascii=False,indent=2)+'\n```\n')
    return decision

def document(decision):
    path=PROJECT.parents[1]/'算法版本迭代说明.md';original=path.read_bytes();nl=b'\r\n' if b'\r\n' in original else b'\n';data=original
    anchor=b'<a id="v4-2"></a>';following=b'<a id="version-6"></a>';toc='- [V4.2：20261103 规则核对与独立参考模型](#v4-2)'.encode('utf8')
    if anchor in data:
        start=data.index(anchor);end=data.index(following,start);data=data[:start]+data[end:]
    if toc not in data:
        pos=data.index(b'6. [');data=data[:pos]+toc+nl+data[pos:]
    body='<a id="v4-2"></a>\n## V4.2：20261103 规则核对与独立参考模型（2026-11-04）\n\n'
    body+='V4.2 基于真实 V4 提交 `3f6cedd2318e0fded47fa2b9efd84e7d23554b6f`，本地 main 已快进保留原历史。继承 Layer Escape、全局 TSV、Thermal Refinement、高 Pin 局部修复与统一回滚；不重新创造 V4。状态保持 **CANDIDATE / NOT_RELEASED**。\n\n'
    body+='新增零容量实际布线拒绝：搜索端点、邻接点、Pin 接入、TSV 和完整输出检查共同生效。固定 Pin 虚拟需求仍保留，不能删去固定占用来提高成绩。其依据是 11 月 3 日会议纪要，精确官方实现仍待平台核验。\n\n'
    body+='11 月 3 日明确修复无条件归左 GCell，但没有提供新轨道选择公式。V4 原本已按几何所属格内最近 Track 处理；V4.2 将该假设显式隔离，并提供 nearest/lower/upper_in_cell 三种诊断。**不能把最近轨道或较小索引 Track 冒称官方修复规则。** 多层 Pin、IO Pin、Pin/OBS 位图并集与有界工作区继续保留。\n\n'
    body+='新参考模型为 **REFERENCE_20261103**；旧 REFERENCE_20261029 的源码与报告冻结。百分制 Congestion、Thermal、最终总分均 N/A；线长的三维参考基准和旧 ELF Via/TSV 3/6 代理假设仍明确标注。旧官方 V3.5 **45.39**、V3.6 **45.92** 保持历史口径，不拿来证明新规则质量。\n\n'
    body+='Router SHA：`'+str(decision['router_sha256'])+'`；Config SHA：`'+str(decision['config_sha256'])+'`；参考源码 SHA：`'+str(decision['reference_source_sha256'])+'`。\n\n'
    body+=table(['Case','Router','新版参考','Guide SHA'],[[k,v['router_status'],v['reference_status'],v['guide_sha256']] for k,v in decision['cases'].items()])
    body+='\n完整同口径数据与未执行项目见 [V4.2 报告](./EDA/code_3DIC%20EDA/outputs/reports/v4_2/summary.md)；[规则矩阵](./EDA/code_3DIC%20EDA/outputs/reports/v4_2/rules/rule_matrix.md) 保留来源级别和未知细节。源码包的干净目录编译、Config/版本校验和 UI 检查分别保存真实回执。三个 Case 未全部通过时不生成不完整 Guide 包，不 commit/push，不删远程分支。\n\n'
    pos=data.index(following);data=data[:pos]+body.replace('\n',nl.decode()).encode('utf8')+data[pos:]
    def outside(value):
        value=value.replace(toc+nl,b'')
        if anchor in value:
            start=value.index(anchor);end=value.index(following,start);value=value[:start]+value[end:]
        return value
    if outside(data)!=outside(original):raise ValueError('Historical document bytes would change')
    path.write_bytes(data);write('documentation_change.json',{'outside_v4_2_section_bytes_identical':True,'sha256':sha256(path)})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--update-document',action='store_true');a=p.parse_args();d=generate()
    if a.update_document:document(d)
    print(json.dumps({k:v for k,v in d.items() if k!='gates'},ensure_ascii=False))
