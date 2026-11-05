import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {Presentation,PresentationFile} from '@oai/artifact-tool';
const B=path.dirname(fileURLToPath(import.meta.url)), W=path.dirname(B);
const D=JSON.parse(await fs.readFile(path.join(W,'assets/chart_data.json'),'utf8'));
const P=Presentation.create({slideSize:{width:1280,height:720}});
const C={navy:'#102A43',deep:'#071B2E',cyan:'#008C9E',aqua:'#35CED0',blue:'#3273BA',ink:'#132E45',muted:'#547084',gray:'#DFE8ED',light:'#F2F7FA',white:'#FFFFFF',orange:'#C66D1D',red:'#B24935'};
const FONT='Microsoft YaHei';const sources=[],scripts=[];
function rect(s,x,y,w,h,fill,line='none',lw=0){return s.shapes.add({geometry:'rect',position:{left:x,top:y,width:w,height:h},fill,line:{fill:line,width:lw}})}
function text(s,txt,x,y,w,h,size=26,color=C.ink,bold=false,align='left'){
 const z=s.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});z.text=txt;z.text.style={typeface:FONT,fontSize:size,color,bold,alignment:align,verticalAlignment:'middle',insets:0,wrap:'word'};return z;
}
function line(s,x1,y1,x2,y2,color=C.cyan,width=3){return s.shapes.add({geometry:'line',position:{left:Math.min(x1,x2),top:Math.min(y1,y2),width:Math.max(.1,Math.abs(x2-x1)),height:Math.max(.1,Math.abs(y2-y1)),horizontalFlip:(x2-x1)*(y2-y1)<0},fill:'none',line:{fill:color,width}});}
function dot(s,x,y,label='',color=C.cyan,r=9){s.shapes.add({geometry:'ellipse',position:{left:x-r,top:y-r,width:2*r,height:2*r},fill:color,line:{fill:C.white,width:2}});if(label)text(s,label,x+13,y-39,130,32,21,color,true);}
function arrow(s,x,y,w=36){s.shapes.add({geometry:'rightArrow',position:{left:x,top:y,width:w,height:20},fill:C.cyan,line:{fill:'none',width:0}});}
function box(s,title,sub,x,y,w,h=100,fill=C.light){rect(s,x,y,w,h,fill);text(s,title,x+16,y+12,w-32,35,26,C.navy,true);text(s,sub,x+16,y+51,w-32,h-60,21,C.muted);}
function add(title,subtitle,source,dark=false){let s=P.slides.add();s.background.fill=dark?C.deep:C.white;let n=P.slides.items.length;rect(s,54,44,8,38,dark?C.aqua:C.cyan);text(s,title,78,38,1148,67,43,dark?C.white:C.navy,true);if(subtitle)text(s,subtitle,78,112,1120,42,23,dark?'#A6C8D8':C.muted);rect(s,54,667,1172,1,dark?'#234658':C.gray);text(s,source,56,676,1095,24,15,dark?'#8CACBD':C.muted);text(s,String(n).padStart(2,'0'),1169,675,55,26,18,dark?C.aqua:C.cyan,true,'right');sources.push(source);return s;}
function table(s,values,x,y,w,h,widths,font=21){const t=s.tables.add({rows:values.length,columns:values[0].length,left:x,top:y,width:w,height:h,columnWidths:widths,values:values.map(r=>r.map(String))});t.borders.assign({fill:C.gray,width:1,style:'solid'});for(let r=0;r<values.length;r++)for(let c=0;c<values[0].length;c++){let cell=t.getCell(r,c);cell.fill=r===0?C.navy:r%2?C.light:C.white;cell.text.style={typeface:FONT,fontSize:font,color:r===0?C.white:C.ink,bold:r===0,alignment:c===0?'left':'center',verticalAlignment:'middle',insets:{left:12,right:8,top:9,bottom:8}};}return t;}
async function img(s,name,x,y,w,h){let b=await fs.readFile(path.join(W,'assets',name));s.images.add({blob:b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength),contentType:'image/png',alt:name,fit:'contain',position:{left:x,top:y,width:w,height:h}});}
function chart(s,categories,series,x,y,w,h,max,title='',format='0'){
 const ts=(size,color)=>({fontSize:size,fill:color});
 const ch=s.charts.add('bar',{position:{left:x,top:y,width:w,height:h},title,titleTextStyle:ts(22,C.ink),categories,series:series.map((r,i)=>({...r,fill:[C.cyan,C.blue,C.orange][i%3]})),barOptions:{direction:'column',grouping:'clustered',gapWidth:90},hasLegend:series.length>1,legend:{position:'bottom',textStyle:ts(20,C.ink)},xAxis:{textStyle:ts(20,C.ink),line:{fill:C.gray,width:1}},yAxis:{min:0,max,numberFormatCode:format,textStyle:ts(17,C.muted),majorGridlines:{fill:C.gray,width:1}},dataLabels:{showValue:true,position:'outEnd',numberFormatCode:format,textStyle:ts(21,C.ink)},chartFill:C.white,plotAreaFill:C.white});
 for(const style of [ch.titleTextStyle,ch.legend.textStyle,ch.xAxis.textStyle,ch.yAxis.textStyle,ch.dataLabels.textStyle])style.typeface=FONT;
 return ch;
}
function note(s,title,seconds,body,refs){scripts.push({title,seconds,body,refs});s.speakerNotes.text=body+'\n\n数据来源（相对项目根目录）：\n'+refs.join('\n')+'\n\n评分口径：'+sources.at(-1);}
const R=(c,v,m='reference_20261029')=>D.rows.find(x=>x.case===c&&x.version===v&&x.model===m);
const pct=(a,b)=>(100*(b-a)/a).toFixed(2);const f=(x,d=0)=>x.toLocaleString('en-US',{maximumFractionDigits:d,minimumFractionDigits:d});

// 1
{
let s=add('3DIC 多目标全局布线','', '项目算法汇报 · 2026-11-04 · 基于既有运行与原始评分记录',true);
text(s,'算法设计与\n版本迭代优化',77,172,615,180,62,C.white,true);
text(s,'EDA 精英挑战赛项目汇报',80,387,610,50,30,'#A6C8D8');
text(s,'V1 基础连接  ·  V3 质量升级\nV3.6 TSV 再分配  ·  V4.2 规则适配',80,481,610,88,25,C.aqua);
await img(s,'ui_case001_v4_2_3d.png',696,173,530,397);text(s,'真实 case001 V4.2 单 Net / UI 3D 分层视图\n层距为教学示意，非工艺厚度',704,581,500,50,18,'#A6C8D8');
note(s,'封面',35,'本次汇报总结我们参加 3DIC 全局布线比赛的算法演进。主线是从能够连接所有引脚，发展到在拥塞、线长和热目标之间做有约束的优化。汇报覆盖 V1 到 V4.2，重点解释为什么迭代、源码实际改变了什么、实验是否支持改进。右侧是现有 case001 V4.2 结果的真实三维视图。后面的数字会明确区分比赛平台回执与自研参考模型，不把参考合法性当成新版官方通过。',['算法版本迭代说明.md','EDA/code_3DIC EDA/ui/static/layers3d.js','汇报PPT/assets/ui_case001_v4_2_3d.png']);}
//2
{
let s=add('比赛任务：在连接合法的前提下优化布线质量','DEF / LEF / Thermal JSON 输入，Routing Guide 输出','背景：比赛代码逻辑.md、理论知识.md；Guide 不等于详细布线结果');
box(s,'DEF + LEF','实例、Pin、OBS、Track',76,199,300,122);arrow(s,400,247,50);box(s,'3D Global Router','拓扑、路径、资源、TSV',480,199,320,122);arrow(s,825,247,50);box(s,'Routing Guide','逐 Net / Layer 矩形区域',906,199,300,122);
text(s,'合法性约束',79,374,510,43,30,C.navy,true);text(s,'所有 Pin 连通；层方向与 Via 合法\nTSV 跨 Die，避开禁入 GCell\nGuide 矩形与实际 GCell 相交',80,433,540,143,27);
rect(s,674,374,2,226,C.gray);text(s,'优化目标',731,374,460,43,30,C.navy,true);text(s,'Wirelength  ↓  导线及互连代价\nOverflow     ↓  超容量与热点\nThermal       ↑  TSV 与热分布匹配',731,433,470,143,27);
note(s,'比赛任务与优化目标',55,'全局布线的输入包括 DEF 中的实例和网络、LEF 中的物理几何和金属层信息，以及 Thermal JSON。输出不是最终的制造级金属图形，而是交给后续阶段使用的 Routing Guide。一个可用结果首先必须连接所有 Pin，并满足层方向、Via、TSV 和矩形合法性等约束。然后才比较线长、拥塞和热指标。三个目标相互制约：绕开拥塞可能增长线长，向热点移动 TSV 也可能集中接口层需求。因此项目的关键并不是只优化一个数，而是让每一次变化在合法性和资源预算内产生可以核验的收益。',['比赛代码逻辑.md','理论知识.md','EDA/赛题问题与解答汇总.md']);}
//3
{
let s=add('整体架构：拓扑决定连接关系，搜索决定物理路径','固定资源模型贯穿搜索、占用、重布和最终验证','源码：src/router.cpp、router_v3_impl.hpp、router_v4_impl.hpp');
const blocks=[['输入与资源','几何变换 / Track / GCell'],['拓扑构建','V1 MST / V3 Steiner 候选'],['路径搜索','A* / Multi-Target A*'],['占用与重布','Negotiated / Multi-Net'],['TSV 与热优化','位置迁移 / 局部路径重接'],['验证与输出','连通 / 禁入 / Guide']];
blocks.forEach((b,i)=>{let row=Math.floor(i/3),col=i%3;box(s,(i+1)+'. '+b[0],b[1],76+col*405,200+row*190,320,118);if(col<2)arrow(s,412+col*405,247+row*190,50);});
text(s,'统一资源账本',80,573,270,43,31,C.cyan,true);text(s,'试探修改只提交完整合法候选；失败恢复 Route、Usage 与 TSV 占用',358,574,839,49,26);
note(s,'整体算法架构',55,'从单条 Net 来看，程序先把实际 Pin 几何映射为可接入的布线节点，再决定如何组织多 Pin 的连接拓扑。早期使用 MST，后续引入受限的 Steiner 候选与树增长。A* 则负责在真实三维图上避开昂贵资源，生成同层路径、普通 Via 和跨 Die TSV。占用更新后，多个 Net 通过拥塞历史和拆线重布相互协调。更后的版本增加局部层逃逸和 TSV 迁移。这里一个重要工程选择是统一账本：搜索和质量判断使用相同的资源需求，事务中断或候选不合格时完整回滚，最后再检查所有 Net 的连通与 Guide 输出。',['比赛代码逻辑.md','EDA/code_3DIC EDA/src/router.cpp','EDA/code_3DIC EDA/include/router_v3_impl.hpp','EDA/code_3DIC EDA/include/router_v4_impl.hpp']);}
//4
{
let s=add('V1：Prim MST 选连接，A* 在网格上找路','多 Pin Net 分解为连接任务，再用拥塞代价协调竞争','算法教学示意，非实际布线截图；V1 历史来源与 Router::mst/search/route');
text(s,'① MST：连接关系',80,185,485,40,30,C.navy,true);text(s,'② A*：实际路径',681,185,490,40,30,C.navy,true);
const a=[[116,305],[341,260],[245,447],[477,434]];[[0,1],[0,2],[2,3]].forEach(([i,j])=>line(s,...a[i],...a[j],C.blue,4));a.forEach((p,i)=>dot(s,...p,'P'+(i+1),C.cyan,10));
for(let k=0;k<7;k++){line(s,700+k*65,258,700+k*65,510,C.gray,1);line(s,700,258+k*42,1090,258+k*42,C.gray,1)}rect(s,829,300,130,84,'#F5D8CD');text(s,'高代价区域',837,317,117,48,20,C.red);
[[700,468,765,468],[765,468,765,258],[765,258,1090,258],[1090,258,1090,342]].forEach(e=>line(s,...e,C.cyan,5));dot(s,700,468,'S');dot(s,1090,342,'T');
text(s,'f(n) = g(n) + h(n)',80,535,510,48,34,C.navy,true);text(s,'g：线长、Via、拥塞及历史成本\nh：与所选边代价相容的下界',80,590,530,60,22,C.muted);
text(s,'局限：拓扑不共享 Steiner 分支\n早期 TSV 候选局部化，拥塞需反复修复',680,548,535,87,26);
note(s,'V1 基础算法',65,'V1 首先解决布线能否完整运行。左图是教学示意，Prim MST 以估计距离选择连接关系，它还不是金属导线。右图中的 A* 才在 GCell 和金属层图上寻找路径，代价既包含长度，也包含 Via、拥塞和历史成本。跨 Die 网络需要增加 TSV 接口，两侧分别连接到接口节点。完成初始布线后，拆线重布尝试消除竞争。这个版本的局限是，终端 MST 难以主动构造共享分支，而且局部 TSV 决策可能把大量网络集中到相同接口资源。后续迭代分别从计算效率、拓扑质量和全局资源分布解决这些问题。',['算法版本迭代说明.md V1','EDA/code_3DIC EDA/src/router.cpp: mst/search/route']);}
//5
{
let s=add('V2：减少搜索与恢复开销，保持原有拓扑语义','Dense / Sparse 状态、缓存、Move 与增量回滚','未取得严格同条件 V1/V2 基准，不制作加速比；源码与历史快照为机制证据');
table(s,[['成本来源','V2 的处理'],['A* 反复分配 / 清空','Dense 状态复用；大空间使用 Sparse'],['几何与资源重复查询','缓存层属性、Track 与资源计算'],['TSV 候选重复搜索','候选和可行性查询加速'],['试探时复制完整结果','Move 旧 Route，仅保存增量变化']],78,190,1122,275,[345,777],25);
box(s,'移出旧 Route','减去旧需求',79,505,238,105);arrow(s,335,547,43);box(s,'试探候选','增量安装与验证',402,505,238,105);arrow(s,658,547,43);box(s,'接受 / 回滚','恢复对应资源',725,505,238,105);
text(s,'效率升级\n≠ Steiner 拓扑升级',995,520,205,78,25,C.cyan,true);
note(s,'V2 效率改进',50,'V2 的核心贡献是减少相同算法做无用工作的成本。A* 根据空间规模选择 Dense 或 Sparse 状态结构，避免每一次搜索都初始化巨大容器；几何和资源查询也尽量缓存。对于需要尝试多个候选的阶段，程序不再反复复制整个路由集合，而是移动旧 Route、更新受影响的资源，并在失败时恢复。这样做支持后续更多优化试探，但并不意味着拓扑已经变成 Steiner Tree。当前没有足够严格的同硬件、同输入、同参数 V1 与 V2 基准，所以这里展示机制，不给出未经验证的加速倍数。',['算法版本迭代说明.md V2','EDA/code_3DIC EDA/src/router.cpp','EDA/code_3DIC EDA/include/router.hpp']);}
//6
{
let s=add('V3：从终端连边转向共享路由树与协同重布','Hanan 候选 + Multi-Target A*；实现是启发式，不是精确 RSMT / FLUTE','源码：router_v3_impl.hpp；REF20261029 case001 V3.1–V3.3 各检出 429 条 TSV_PIN_OBS');
text(s,'终端连接',80,183,440,40,29,C.navy,true);text(s,'共享 Steiner 分支',655,183,490,40,29,C.navy,true);
[[130,275,310,430],[310,430,490,275]].forEach(e=>line(s,...e,C.blue,4));[[130,275],[490,275],[310,430]].forEach((p,i)=>dot(s,...p,'P'+(i+1)));
[[710,275,890,275],[890,275,1070,275],[890,275,890,430]].forEach(e=>line(s,...e,C.cyan,5));[[710,275],[1070,275],[890,430]].forEach((p,i)=>dot(s,...p,'P'+(i+1)));dot(s,890,275,'S',C.orange,8);
text(s,'示意：候选分支可让多个终端共享连接\nMulti-Target A* 连接到已有树，而非反复指定终点',82,474,1117,71,26);
const stages=[['V3.1–V3.3','拓扑 / 动态拥塞 / 多 Net'],['V3.4','严格 TSV 禁入与 Guide 检查'],['V3.5','真实 Pin Geometry 多格接入']];stages.forEach((x,i)=>{rect(s,78+i*382,571,358,65,i===2?C.navy:C.light);text(s,x[0],91+i*382,576,334,27,22,i===2?C.aqua:C.cyan,true);text(s,x[1],91+i*382,607,334,26,20,i===2?C.white:C.ink);});
note(s,'V3 算法升级',80,'V3 的变化包括拓扑、搜索和资源模型。拓扑在受限 Hanan 候选中寻找能降低连接成本的 Steiner 点；Multi-Target A* 将新终端连接到已经生长的树，有机会形成共享路径。这是带预算的启发式，不应称为精确 RSMT 或声称调用了 FLUTE。拥塞方面引入动态价格与多 Net 协同试探，避免一条网络改善却把邻居推入热点。规则方面出现了关键教训：V3.1 到 V3.3 的 case001 在统一新参考检查下各有 429 条 TSV 与 Pin/OBS 禁入冲突，旧评分通过并不代表新模型合法。V3.4 加入严格禁入，V3.5 再用真实 Pin 几何选择合法接入格，使 sample001 也能在不豁免规则的情况下完成布线。',['EDA/code_3DIC EDA/include/router_v3_impl.hpp','EDA/code_3DIC EDA/include/router_v3_5_impl.hpp','算法版本迭代说明.md §5.10–5.11','EDA/code_3DIC EDA/outputs/reports/reference_20261029/open_3d_case001/']);}
//7
{
let s=add('V3.6：把 TSV 迁移从局部试探扩展到全局拥塞区域','Global Congestion-Aware TSV Redistribution','REF20261029；同输入 / 同参考源码；收益与平台分数分别呈现');
const chain=[['热点区域','资源边际 / 密度'],['迁移候选','合法位置 / 候选预算'],['两侧重接','保留完整 Net 连通'],['联合判定','线长 / O / 潜能 / 热']];chain.forEach((a,i)=>{box(s,a[0],a[1],78+i*299,192,252,108);if(i<3)arrow(s,342+i*299,233,25)});
table(s,[['参考指标','V3.5','V3.6','变化'],['case001 Overflow','15,461','12,096','−21.76%'],['case003 Overflow','1,177,977','1,155,039','−1.95%'],['case001 Pearson ρ','0.001688','0.001175','热相关性下降']],78,353,1122,207,[390,240,240,252],25);
text(s,'单 Net 事务：拆除旧需求、重接合法候选、提交或完整恢复',80,585,1120,39,29,C.cyan,true);
note(s,'V3.6 全局 TSV 优化',70,'早期 TSV 优化受限于局部候选与尝试次数，难以改变全芯片的拥塞分布。V3.6 先识别高代价 TSV 区域，再按预算选择迁移候选。每次迁移都要重新连接两侧路径，保持唯一接口和完整 Net 连通，评价的是移除旧需求后插入新需求带来的真实增量。单 Net 事务保证搜索失败或预算中断时恢复旧布线。在相同 REFERENCE_20261029 模型下，case001 Overflow 降低 21.76%，case003 降低 1.95%。但 case001 热相关性反而下降，这说明拥塞收益不能自动解释为热收益。case005 最终仅改变少量 TSV 位置，高 Pin 排除和候选预算仍限制了覆盖面，为 V4 的公平调度与局部修复提供了方向。',['EDA/code_3DIC EDA/include/router_v3_6_impl.hpp','EDA/code_3DIC EDA/outputs/reports/reference_20261029/open_3d_case001/','EDA/code_3DIC EDA/outputs/reports/reference_20261029/open_3d_case003/','EDA/code_3DIC EDA/outputs/reports/v4/research/']);}
//8
{
let s=add('V4 联合三类优化，V4.2 强化规则边界','局部层逃逸、接口再分配和热修正共享资源与回滚机制','源码：router_v4_impl.hpp、GR_V4_2；模型假设不等于官方实现');
box(s,'M1 Layer Escape','保留 Pin / 分叉 / TSV\n修复局部 degree-two 路径',78,190,343,146);
box(s,'Global TSV','区域公平调度与动态重计价\n高 Pin Net 只重接局部尾段',469,190,343,146);
box(s,'Thermal Refinement','增量提升 Pearson\n限制密度与拥塞损失',860,190,343,146);
line(s,250,350,250,398,C.cyan);line(s,640,350,640,398,C.cyan);line(s,1030,350,1030,398,C.cyan);line(s,250,398,1030,398,C.cyan);
rect(s,270,427,740,80,C.navy);text(s,'统一事务 + 完整连通检查 + 线长 / Via 预算',288,444,704,47,29,C.white,true,'center');
text(s,'V4.2 继承 V4 核心算法',80,555,450,42,31,C.cyan,true);text(s,'拒绝实际零容量布线；显式隔离 Pin–Track 归属假设\nREFERENCE_20261103 与旧模型冻结分离，等待平台核验',563,546,640,80,25);
note(s,'V4 与 V4.2',75,'V4 在 V3.6 基础上联合三个模块。Layer Escape 处理 M1 的局部可替代路径，保留必需 Pin、分叉点和 TSV。Global TSV 采用区域公平调度和动态候选重计价，也让高 Pin 网络通过局部尾段重接参与优化。Thermal Refinement 随后提升 Pearson，并明确限制密度与拥塞损失。三者共用资源账本和事务，但也共用预算，因此执行次序可能影响结果。V4.2 不是新的拓扑算法，而是继承这些模块，加强零容量实际布线的拒绝和规则检查。没有 Track 覆盖的 Pin 如何精确归属仍是模型假设，所以将 nearest_in_cell 等解释显式隔离，使用新的自研参考模型验证，不能据此宣布新版官方通过。',['EDA/code_3DIC EDA/include/router_v4_impl.hpp','EDA/code_3DIC EDA/src/router.cpp GR_V4_2','EDA/code_3DIC EDA/include/physical_resources_20261103.hpp','EDA/code_3DIC EDA/outputs/reports/v4_2/rules/rule_matrix.md']);}
//9
{
let s=add('版本演进：效率、质量和规则逐步成为共同约束','按算法演进分阶段；版本序列完整保留','Git 与构建回执核对；V4/V4.2 尚无比赛平台总分',true);
const rows=[['V1 → V2','基础连接与计算效率','Prim MST + A*；Dense/Sparse、缓存、增量回滚'],['V3.1 → V3.2 → V3.3','共享拓扑与拥塞协同','Hanan 候选、Multi-Target、动态价格、多 Net 事务'],['V3.4 → V3.5','规则与物理接入修复','严格 TSV 禁入；真实 Geometry 接入与有界资源模型'],['V3.6','全局 TSV 拥塞优化','2026-11-02 · 平台 45.92，较 V3.5 提高 0.53 分'],['V4 → V4.2','联合目标与规则适配','11-03 / 11-04 · 多模块联动；新模型验证待闭环']];
line(s,96,205,96,611,'#31566B',3);
rows.forEach((r,i)=>{let y=183+i*91;dot(s,96,y+23,'',C.aqua,7);text(s,r[0],130,y,370,39,28,C.aqua,true);text(s,r[1],517,y,685,35,28,C.white,true);text(s,r[2],517,y+42,685,38,21,'#A6C8D8');});
note(s,'完整版本时间线',45,'这条时间线展示了项目研究重心的变化。V1 建立能运行的基础，V2 主要让同样的搜索和试探更便宜。V3.1 到 V3.3 开始改变拓扑和多网络拥塞协调。V3.4 与 V3.5 则说明，质量优化必须建立在正确的物理接入和规则模型上。V3.6 把 TSV 分布提升到全局资源问题，并取得可核对的平台提升。V4 将 M1、TSV 与热协同，V4.2 进一步隔离规则假设。版本号增加本身不是成果，后面需要通过同口径数据判断每一步是否真正值得保留。',['算法版本迭代说明.md','git log --date=short','EDA/code_3DIC EDA/docs/router_versions.json']);}
//10
{
let s=add('平台总分 45.39 → 45.92，收益集中在拥塞分项','V3.5 / V3.6 原始平台报告 v1.4；三个 Case 均返回 PASS','平台回执；六份提交 Guide SHA 与本地一致；V4 / V4.2 平台总分 N/A');
chart(s,['V3.5','V3.6'],[{name:'平台总分',values:[45.39,45.92]}],64,191,485,397,100,'平台总分 / 分','0.00');
text(s,'+0.53 分',625,181,550,72,61,C.cyan,true);text(s,'拥塞均分 +1.80；线长与热均分各 −0.01',627,261,574,43,24,C.muted);
table(s,[['平台均分','V3.5','V3.6'],['Wirelength','70.63','70.62'],['Congestion','8.16','9.96'],['Thermal','44.63','44.62']],625,327,575,214,[275,150,150],26);
text(s,'Case001 / 003 / 005 平台 Overflow\n1749→1677  /  177760→168111  /  447082→447043',80,601,1122,48,23);
note(s,'历史比赛平台成绩',70,'这里使用本地保存的原始平台回执，而不是从参考模型换算总分。V3.5 为 45.39，V3.6 为 45.92，增加 0.53 分。三分项平均值显示拥塞提高 1.80 分，线长和热各下降约 0.01 分。为了确认对应结果，已经将两次 prediction ZIP 中六份 Guide 的完整 SHA256 与本地运行逐一匹配，全部一致。三个 Case 都返回 PASS，但报告本身也声明这是 Guide 级评价，线长是矩形 MST 代理，没有真实详细布线 DRC。平台与本地参考模型的 Overflow 数量级明显不同，后面会分别呈现。V4 和 V4.2 没有平台总分，所以不能声称已经超过 45.92。',['EDA/code_3DIC EDA/outputs/reports/official_platform/v3_5_24699/scores.json','EDA/code_3DIC EDA/outputs/reports/official_platform/v3_6_26152/scores.json','汇报PPT/assets/chart_data.json']);}
//11
{
let s=add('同一参考模型：拥塞下降伴随不同程度的代价','REFERENCE_20261029；同 Case 输入与参考源码 SHA 一致','WL=µm；RSS=MiB；Runtime/RSS 为单次共享主机记录，不作普适加速结论');
let vals=[['Case / 版本','WL / µm','Overflow','Pearson ρ','Router s','RSS MiB']];for(let c of ['case001','case003'])for(let v of ['v3_5','v3_6','v4']){let r=R(c,v);vals.push([c+' / '+v.replaceAll('_','.'),f(r.wirelength_um,1),f(r.overflow),r.rho.toFixed(6),r.runtime.toFixed(1),r.rss_mib.toFixed(1)]);}table(s,vals,77,190,1126,326,[233,211,190,180,155,157],22);
text(s,'V3.5 → V3.6',80,553,310,38,28,C.cyan,true);text(s,'Overflow：case001 −21.76%   case003 −1.95%',399,553,800,38,27);
text(s,'V3.6 → V4',80,605,310,38,28,C.cyan,true);text(s,'Overflow：case001 −3.94%     case003 −5.52%',399,605,800,38,27);
note(s,'分 Case 性能对比',85,'这一页只比较冻结的 REFERENCE_20261029，已经核对同 Case 的输入和参考源码身份一致，表中所有结果均参考合法。case001 从 V3.5 到 V3.6，Overflow 明显降低，但 Pearson 从约 0.001688 降到 0.001175。V4 再降低约 3.94% Overflow，并恢复一部分热相关性。case003 的 V3.6 改善较小，而 V4 降低约 5.52%，热相关性也提高。但是 V4 的 Router 时间增加，线长也增长，所以不是无代价改善。Runtime 和 RSS 是现有运行记录中的单次观测，受主机负载、阶段预算和采样影响，不作为普适性能结论。case005 缺少 V4 结果、V4.2 还未参考评分，没有放入这张同口径质量对照表。',['EDA/code_3DIC EDA/outputs/reports/reference_20261029/','EDA/code_3DIC EDA/outputs/reports/scoring/reference_20261029/','EDA/code_3DIC EDA/outputs/runs/','汇报PPT/assets/chart_data.json']);}
//12
{
let s=add('真实 TSV 分布：相同 Case、视口和显示条件对照','case001 真实 result.json；相同物理坐标，橙色为最终 TSV 位置变化的 Net','实际 TSV 坐标重绘 / µm；181 / 657 个 Net 的最终 TSV 位置改变；真实 UI 图另存 assets');
text(s,'V3.5',80,180,525,40,30,C.navy,true);text(s,'V3.6',679,180,525,40,30,C.navy,true);
await img(s,'tsv_case001_v3_5.png',66,225,552,360);await img(s,'tsv_case001_v3_6.png',666,225,552,360);
text(s,'相同 TSV 数量 657，调整位置与重接路径改变资源分布',80,595,1124,41,29,C.cyan,true);
note(s,'真实布线可视化',45,'这一页直接读取同一个 case001 两版真实 result.json 的 tsv_node，用相同网格映射与微米坐标重绘。两版都有 657 个 TSV，其中 181 条 Net 的最终 TSV 位置改变，以橙色标记同一组网络的前后位置。这是最终结果差异，不能等同于后处理阶段接受次数，也不能单独证明因果。实际 UI 密度截图另存 assets，封面使用真实三维 UI 图；没有补造 Trace 或冒用官方参考 Guide。空间图解释位置关系，拥塞与热收益仍以原始评价报告为准。',['汇报PPT/assets/ui_case001_v3_5_density.png','汇报PPT/assets/ui_case001_v3_6_density.png','EDA/code_3DIC EDA/ui/static/app.js','EDA/code_3DIC EDA/ui/static/layers3d.js']);}
//13
{
let s=add('消融揭示：联合优化未必支配单模块','case001 的 TSV-only 总拥塞优于 Joint，但 Joint 更有效降低 M1','REF20261029；v4/ablation_results.md、case003_comparison.md、decision.json');
chart(s,['基线','Escape','Thermal','TSV','Joint'],[{name:'Overflow',values:[12096,11845,12097,10995,11619]}],60,183,689,349,14000,'case001 Overflow / 资源单位','#,##0');
text(s,'case003 未达线长门槛',793,192,409,45,29,C.red,true);text(s,'+0.500443%',793,250,410,72,51,C.red,true);text(s,'目标 ≤ +0.5%\n参考线长超预算约 55.60 µm\n代理保护不等于外部指标保护',793,342,409,118,25);
table(s,[['case001 模块','M1 Overflow','Pearson ρ'],['TSV-only','686','0.001194'],['Joint','435','0.001322']],79,535,1122,112,[422,350,350],21);
note(s,'消融、瓶颈与不足',75,'消融实验帮助区分模块真正贡献。case001 的单独 TSV 模块将 Overflow 降到 10995，优于联合方案的 11619。联合方案则把 M1 Overflow 从 686 降到 435，并改善部分热相关性，所以它改变了收益分布，而不是全面支配单模块。toy 中还观察到 Layer Escape 先消耗线长预算，限制后续 Thermal 的改善。这说明执行次序和共享预算是实质算法问题。另外，case003 V4 的参考线长增长 0.500443%，确实略超过预先声明的 0.5% 门槛，约多出 55.60 微米。不能因为幅度小就隐藏失败，也不能把内部 Guide-grid 代理保护当作外部参考线长保护。下一步应优先校准接受条件与模块调度，而不是继续堆叠复杂度。',['EDA/code_3DIC EDA/outputs/reports/v4/ablation_results.md','EDA/code_3DIC EDA/outputs/reports/v4/case003_comparison.md','EDA/code_3DIC EDA/outputs/reports/v4/decision.json']);}
//14
{
let s=add('当前结论：已有收益，需要完成规则与大规模验证闭环','V4.2 最新状态以 run.json 和实际参考报告为准','截至 2026-11-04；REFERENCE_VALID ≠ 新版官方 PASS；V4.2 官方总分 N/A');
table(s,[['V4.2 Case','Router','REFERENCE_20261103'],['case001','ROUTED','REFERENCE_VALID'],['case003','ROUTED','REFERENCE_VALID'],['case005','ROUTED','NOT_EVALUATED']],78,189,1122,203,[300,330,492],26);
const items=[['01','算法成果','基础连接已发展为共享树、多 Net 与多目标协同。'],['02','实验证据','V3.6 平台提高 0.53 分，收益和代价均可追溯。'],['03','后续重点','校准官方规则与接受指标，完成 case005 参考验证。']];items.forEach((a,i)=>{let y=434+i*66;text(s,a[0],79,y,66,39,31,C.cyan,true);text(s,a[1],165,y,189,39,28,C.navy,true);text(s,a[2],369,y,833,43,26);});
note(s,'总结与后续方向',45,'总结来看，项目已经从基础 MST 与 A*，发展到共享路由树、多 Net 事务和拥塞、线长、热目标的协同优化。真实平台证据支持 V3.6 相比 V3.5 提高 0.53 分，但不支持所有指标同步改善。V4 和 V4.2 的价值需要继续在严格门槛下验证。最新记录显示 V4.2 三个公开 Case 已完成布线，其中 case001 和 case003 在新自研参考模型中合法，case005 尚未参考评分。后续优先完成这项验证、校准官方规则与外部线长接受条件，并通过消融研究模块预算分配。当前不把参考合法性等同于官方新版通过，也不从参考 Overflow 推算官方总分。',['EDA/code_3DIC EDA/outputs/runs/open_3d_case005/case005_v4_2/run.json','EDA/code_3DIC EDA/outputs/reports/scoring/reference_20261103/','汇报PPT/数据来源与版本核对.md']);}

let md='# 汇报讲稿\n\n主题：3DIC 多目标全局布线算法设计与版本迭代优化。14 页，建议约 '+(scripts.reduce((a,x)=>a+x.seconds,0)/60).toFixed(1)+' 分钟（含指图与停顿）。\n\n';let elapsed=0;
scripts.forEach((x,i)=>{md+=`## 第 ${i+1} 页 ${x.title}\n\n建议 ${x.seconds} 秒，累计 ${((elapsed+=x.seconds)/60).toFixed(1)} 分钟。\n\n${x.body}\n\n`;});
await fs.writeFile(path.join(W,'汇报讲稿.md'),md,'utf8');
await fs.writeFile(path.join(W,'assets/slide_notes.json'),JSON.stringify(scripts,null,2));
await(await PresentationFile.exportPptx(P)).save(path.join(B,'candidate.pptx'));
for(let i=0;i<P.slides.items.length;i++){const s=P.slides.items[i];await fs.writeFile(path.join(W,'preview',`slide-${String(i+1).padStart(2,'0')}.png`),new Uint8Array(await(await P.export({slide:s,format:'png',scale:1.5})).arrayBuffer()));console.log('rendered',i+1);}
console.log('DRAFT_RENDERED');
