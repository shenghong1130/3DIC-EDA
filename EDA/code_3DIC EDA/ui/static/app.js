"use strict";
const $=id=>document.getElementById(id), palette=['#3775d6','#22a199','#d78532','#8c62c4','#d85e81','#4e9c5c','#6598bf','#af9747'];
const S={catalog:null,data:null,trace:[],largeTrace:false,raw:null,view:'routing',index:-1,active:new Map(),selected:'all',ghost:null,playing:null,zoom:1,panX:0,panY:0,hit:[],loadId:0};
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(x,d=2)=>x===null||x===undefined?'N/A':typeof x==='number'?x.toLocaleString('zh-CN',{maximumFractionDigits:d}):String(x);
const api=async url=>{const r=await fetch(url),d=await r.json();if(!r.ok)throw Error(d.error||r.statusText);return d;};
function error(e){$('error').hidden=!e;$('error').textContent=e?.message||e||'';}
function options(el,items,value){el.innerHTML=items.map(([v,t])=>`<option value="${esc(v)}">${esc(t)}</option>`).join('');if(value!==undefined&&items.some(x=>x[0]===value))el.value=value;}
function table(headers,rows){return `<table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(x=>`<td>${esc(x)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;}
async function init(){try{S.catalog=await api('/api/catalog');const old=$('dataset').value;options($('dataset'),[...new Set(S.catalog.cases.map(c=>c.dataset))].map(x=>[x,x]),old);setCases();}catch(e){error(e);}}
function setCases(){const cs=S.catalog.cases.filter(c=>c.dataset===$('dataset').value);const available=c=>S.catalog.runs.some(r=>r.case===c.id&&r.status==='ROUTED');const ready=cs.find(c=>c.id==='toy'&&available(c))||cs.find(available);options($('case'),cs.map(c=>[c.id,c.id]),$('case').value||ready?.id);setRuns();}
function setRuns(){const rs=S.catalog.runs.filter(r=>r.case===$('case').value&&r.status==='ROUTED');options($('run'),rs.map(r=>[r.id,`${r.label} · ${r.official_status}`]),$('run').value||rs.find(r=>r.label==='final')?.id);options($('compare'),[['','选择另一条真实记录'],...rs.map(r=>[r.id,r.label])]);load();}
async function load(){const id=++S.loadId;stop();error(null);try{
    const reference=$('source').value==='reference',run=$('run').value;
    if(!run&&!reference){S.data=null;S.trace=[];S.active=new Map();$('subtitle').textContent='该用例尚无完整运行记录。请先使用 scripts/run_case.py 运行。';$('metrics').innerHTML='';$('viewnote').textContent='尚无布线结果';$('badge').textContent='NOT RUN';$('netdetail').textContent='N/A';$('report').textContent='Not Tested';$('logs').textContent='';$('timeline').disabled=true;draw();return;}
    const data=await api(reference?'/api/reference?case='+encodeURIComponent($('case').value):'/api/run?id='+encodeURIComponent(run));
    const tr=reference?{events:[]}:await api('/api/trace?id='+encodeURIComponent(run));const trace=tr.events;if(id!==S.loadId)return;
    S.data=data;S.trace=trace;S.largeTrace=!!tr.requires_net;S.raw=null;S.selected='all';S.index=-1;S.active=new Map(data.nets.map(n=>[n.net,n]));S.ghost=null;
    options($('layer'),[['all','全部金属层'],...data.layers.map(l=>[String(l.number),`${l.name} · ${l.direction==='HORIZONTAL'?'水平':'垂直'}`])]);
    $('subtitle').textContent=`${$('case').value} / ${reference?'官方参考 Guide':data.run.label} · ${data.nets.length} Nets · ${data.x_lines.length-1} × ${data.y_lines.length-1} GCells`;
    $('badge').textContent=reference?'REFERENCE · 仅展示':`官方 ${data.run.official_status}`;$('badge').classList.toggle('fail',!reference&&data.run.official_status!=='PASS');
    setNets();metrics();details();reports();fit();$('timeline').max=Math.max(0,trace.length-1);$('timeline').value=0;$('timeline').disabled=!trace.length;
    $('eventtitle').textContent=trace.length?'最终结果 · 可回放真实步骤':S.largeTrace?'大型 Trace · 请选择单个 Net':'仅最终 Guide，无过程记录';$('eventdetail').textContent=trace.length?`${trace.length} 条真实事件${data.trace_truncated?' · Trace 达到大小上限，过程不完整':''}`:S.largeTrace?'按单个 Net 读取真实事件，避免一次加载整份大型 Trace。':'没有真实 Trace，无法回放布线过程。';$('step').textContent=trace.length?`最终 / ${trace.length}`:'—';$('eventjson').textContent='';
    ['prev','play','next'].forEach(k=>$(k).disabled=!trace.length);compare();if(S.view==='raw')await raw();
}catch(e){if(id===S.loadId)error(e);}}
function setNets(){if(!S.data)return;const q=$('search').value.toLowerCase(),cross=$('crossOnly').checked;const ns=S.data.nets.filter(n=>(!cross||n.cross_die)&&n.net.toLowerCase().includes(q));options($('net'),[['all',`全部符合条件网络 (${ns.length})`],...ns.map(n=>[n.net,n.net+(n.cross_die?' ↕':'')])],S.selected);S.selected=$('net').value;draw();details();}
function metrics(){const m=S.data.metrics;const rows=[['Net 总数',m.nets,'来自实际输入'],['已布线 Net',m.routed_nets,'自主路由结果'],['图边总线长',m.wirelength_dbu_local===null?null:m.wirelength_dbu_local/S.data.dbu_per_micron,'µm · 本地几何估计'],['Overflow',m.overflow_local,'本地近似 · 含 Pin/OBS'],['TSV 数量',m.tsv_count,'实际跨 Die 边'],['热相关 Pearson',m.thermal_correlation_local,'本地映射 · 非官方得分'],['算法运行时间',m.runtime_seconds,'s · 不含 WSL 启动']];$('metrics').innerHTML=rows.map(([k,v,desc])=>`<div class="metric"><small>${esc(k)}</small><strong>${fmt(v,3)}</strong><em>${esc(desc)}</em></div>`).join('');}
function details(){if(!S.data)return;const n=S.data.nets.find(n=>n.net===S.selected);$('nettitle').textContent=n?n.net:'全部网络';$('netdetail').innerHTML=n?table(['属性','实际值'],[['引脚',n.pins.length],['类型',n.cross_die?'跨 Die':'Die 内'],['Guide 矩形',n.guides.length],['真实路径边',n.edges.length],['Via / TSV',`${fmt(n.via_count)} / ${fmt(n.tsv_count)}`],['图边长度 (DBU)',fmt(n.wirelength_dbu_local)],['状态',n.status]]):'<p class="small">点击 Guide 或从左侧选择一个 Net，两侧视图会同步高亮。</p>';
    $('iterations').innerHTML=table(['轮次','本地 Overflow','接受','重布 Net'],S.data.iterations.map(i=>[i.iteration,i.overflow_local,i.iteration===0?'初始':i.accepted?'是':'否，已回滚',i.rerouted_nets??'—']));}
function reports(){const d=S.data,r=d.official_latest;$('report').textContent=r?JSON.stringify(r,null,2):'Not Tested · 当前结果未执行新版官方评分。';$('logs').textContent=d.router_log;$('warnings').textContent=JSON.stringify({source:d.source,inputs:d.run.inputs,warnings:d.warnings,trace_truncated:d.trace_truncated},null,2);$('fidelity').textContent='官方工具评估 GRT Guide 的合法性、线长、拥塞与热指标；报告中的 mock-DRT / proxy 不代表详细布线 DRC 签核。';}
function reportMetrics(d){const r=d.official_latest?.report;return [['本地图边线长 (DBU)',d.metrics.wirelength_dbu_local],['本地 Overflow',d.metrics.overflow_local],['TSV 数量',d.metrics.tsv_count],['本地 Pearson',d.metrics.thermal_correlation_local],['Router 时间 (s)',d.metrics.runtime_seconds],['峰值内存 (MiB)',d.run.peak_memory_bytes==null?null:d.run.peak_memory_bytes/1048576],['官方合法性',d.official_latest?.status??'Not Tested'],['官方 Wirelength 得分',r?.score?.wirelength_score],['官方 Congestion 得分',r?.score?.congestion_score],['官方 Thermal 得分',r?.score?.thermal_score]];}
async function compare(){if(!S.data)return;const current=S.data,key=$('compare').value;try{const b=key?await api('/api/run?id='+encodeURIComponent(key)):null;if(S.data!==current)return;const a=reportMetrics(current),bb=b?reportMetrics(b):[];$('comparetable').innerHTML=table(['指标',`A · ${current.source==='official_reference_guide'?'参考 Guide':current.run.label}`,b?`B · ${b.run.label}`:'B · 未选择'],a.map(([k,v],i)=>[k,fmt(v,4),fmt(bb[i]?.[1],4)]));}catch(e){error(e);}}
function node(id){const d=S.data,nx=d.x_lines.length-1,ny=d.y_lines.length-1,x=id%nx,y=Math.floor(id/nx)%ny,l=Math.floor(id/nx/ny);return {x:(d.x_lines[x]+d.x_lines[x+1])/2,y:(d.y_lines[y]+d.y_lines[y+1])/2,l,gx:x,gy:y};}
function visibleNets(){if(!S.data)return [];const q=$('search').value.toLowerCase(),cross=$('crossOnly').checked;let all=[...S.active.values()].filter(n=>(!cross||n.cross_die)&&n.net.toLowerCase().includes(q));if(S.selected!=='all')all=all.filter(n=>n.net===S.selected);return all.slice(0,150);}
function layerVisible(l,tier){return (tier==='bottom'?l<=10:l>=11)&&($('layer').value==='all'||+$('layer').value===l);}
function fit(){S.zoom=1;S.panX=0;S.panY=0;draw();}
function heatColor(t){t=Math.max(0,Math.min(1,t));return `rgb(${Math.round(52+193*t)},${Math.round(139-64*t)},${Math.round(213-130*t)})`;}
function draw(){S.hit=[];if(!S.data){for(const id of ['bottom','top']){$(id).getContext('2d').clearRect(0,0,$(id).width,$(id).height);}return;}
    const tier=$('tier').value;$('bottomPanel').hidden=S.view==='raw'?false:tier==='top';$('topPanel').hidden=S.view==='raw'?true:tier==='bottom';const ns=visibleNets();
    document.querySelector('#bottomPanel .plotlabel').innerHTML=S.view==='raw'?'原始温度节点 · XY 投影':'<span class="dot blue"></span>Die 1 <span>Bottom · M1–M10</span>';
    for(const t of ['bottom','top'])if(!$(t+'Panel').hidden)drawPanel(t,ns);
    const note={routing:`Guide 区域与真实图边分别绘制。当前渲染 ${ns.length} 个网络${S.selected==='all'?'（最多 150 个；选中单个 Net 可看完整结果）':''}。`,congestion:'蓝色低拥塞 → 红色高拥塞。颜色为 (Usage + 固定 Pin/OBS 需求) / Capacity；悬停查看容量、用量与溢出。',thermal:'投影热图：按 XY 合并不同 z 温度、落格求均值，缺失格采用确定性填充。此为本地映射。',density:'TSV 密度来自自主算法实际跨 Die 边，官方参考 Guide 不推断内部 TSV 数量。',raw:'原始 Thermal JSON 节点的 XY 投影，保留原始温度，不由 z 正负猜测 Die 归属。超过约 10,000 个节点时抽样，原文件保持不变。'};
    $('viewnote').textContent=note[S.view];
    $('legend').innerHTML=S.view==='routing'?'<span><i style="background:#4b7eda55"></i>Guide 区域</span><span><i style="background:#4076d0"></i>真实搜索路径</span><span><i style="background:#223650"></i>Pin</span><span><i style="background:#e49a32"></i>TSV</span><span><i style="background:#1bb894"></i>新增路径</span><span><i style="background:#df6370"></i>拆除路径</span>':`<span><i style="background:${heatColor(0)}"></i>低</span><span><i style="background:${heatColor(1)}"></i>高</span><span>本地 ${S.view==='thermal'?'温度 (°C)':'网格统计'}</span>`;
}
function drawPanel(tier,ns){
    const canvas=$(tier),box=canvas.getBoundingClientRect(),ratio=window.devicePixelRatio||1;canvas.width=box.width*ratio;canvas.height=box.height*ratio;const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);const w=box.width,h=box.height;ctx.clearRect(0,0,w,h);
    const d=S.data,[x1,y1,x2,y2]=d.die,base=Math.min((w-48)/(x2-x1),(h-48)/(y2-y1)),scale=base*S.zoom,cx=w/2+S.panX,cy=h/2+S.panY;
    const xy=(x,y)=>[cx+(x-(x1+x2)/2)*scale,cy-(y-(y1+y2)/2)*scale];
    canvas._world=(px,py)=>[(px-cx)/scale+(x1+x2)/2,(cy-py)/scale+(y1+y2)/2];
    const rect=(r,fill,stroke,width=1)=>{const[a,b]=xy(r[0],r[3]),[c,e]=xy(r[2],r[1]);if(c<0||a>w||e<0||b>h)return;ctx.fillStyle=fill;if(fill)ctx.fillRect(a,b,c-a,e-b);if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=width;ctx.strokeRect(a,b,c-a,e-b);}};
    rect(d.die,'#fbfcfe','#c6d2e2');
    const nx=d.x_lines.length-1,ny=d.y_lines.length-1;
    if(S.view==='thermal'||S.view==='density'||S.view==='congestion'){
        let values=S.view==='thermal'?d.heat:S.view==='density'?d.tsv_density:new Array(nx*ny).fill(0),info=new Map();
        if(S.view==='congestion'){for(const [id,cap,usage,fixed,overflow]of d.resources){const n=node(id);if(!layerVisible(n.l,tier))continue;const key=n.gy*nx+n.gx,score=(usage+fixed)/Math.max(1,cap);if(score>=values[key]){values[key]=score;info.set(key,{cap,usage,fixed,overflow,l:n.l});}}}
        let min=Infinity,max=-Infinity;for(const v of values){min=Math.min(min,v);max=Math.max(max,v);}if(!values.length){min=max=0;}
        for(let y=0;y<ny;y++)for(let x=0;x<nx;x++){const i=y*nx+x,v=values[i];if(v===undefined)continue;rect([d.x_lines[x],d.y_lines[y],d.x_lines[x+1],d.y_lines[y+1]],heatColor((v-min)/Math.max(1e-12,max-min)),null);}
        canvas._heat={values,info,min,max};
    }else canvas._heat=null;
    if(S.view==='raw'&&S.raw){let min=Infinity,max=-Infinity;for(const n of S.raw.nodes){min=Math.min(min,n[3]);max=Math.max(max,n[3]);}for(const n of S.raw.nodes){const[a,b]=xy(x1+n[0]*d.dbu_per_micron,y1+n[1]*d.dbu_per_micron);ctx.fillStyle=heatColor((n[3]-min)/Math.max(1e-12,max-min));ctx.beginPath();ctx.arc(a,b,2.2,0,2*Math.PI);ctx.fill();}}
    if($('grid').checked){ctx.strokeStyle='#8299bb30';ctx.lineWidth=.5;ctx.beginPath();for(const x of d.x_lines){const[a,b]=xy(x,y1),[c,e]=xy(x,y2);ctx.moveTo(a,b);ctx.lineTo(c,e);}for(const y of d.y_lines){const[a,b]=xy(x1,y),[c,e]=xy(x2,y);ctx.moveTo(a,b);ctx.lineTo(c,e);}ctx.stroke();}
    if($('instances').checked){let count=0;for(const i of d.instances){if(!i.tiers.includes(tier==='bottom'?1:2))continue;if(++count>12000)break;rect(i.box,'#71859e08','#768ba62a',.6);}}
    const line=(a,b,color,width=1.5)=>{const[p,q]=xy(a.x,a.y),[r,s]=xy(b.x,b.y);ctx.strokeStyle=color;ctx.lineWidth=width;ctx.beginPath();ctx.moveTo(p,q);ctx.lineTo(r,s);ctx.stroke();};
    if(S.view==='routing')for(let k=0;k<ns.length;k++){
        const n=ns[k],color=palette[hash(n.net)%palette.length];
        if($('guides').checked)for(const g of n.guides||[])if(layerVisible(g[4],tier)){rect(g,color+'20',color+'60',.6);S.hit.push({tier,n:n.net,g});}
        if($('paths').checked)for(const e of n.edges||[]){const a=node(e[0]),b=node(e[1]);if(a.l===b.l&&layerVisible(a.l,tier))line(a,b,color,S.selected==='all'?1.2:2);else if(a.l!==b.l&&Math.min(a.l,b.l)!==10&&layerVisible(a.l,tier)){const[x,y]=xy(a.x,a.y);ctx.fillStyle=color;ctx.fillRect(x-1.5,y-1.5,3,3);}}
        if($('pins').checked)for(const p of n.pins||[])if(layerVisible(p.layer,tier)){const[x,y]=xy(p.x,p.y);ctx.fillStyle='#203b58';ctx.beginPath();ctx.arc(x,y,2.7,0,Math.PI*2);ctx.fill();}
    }
    if($('tsvs').checked)for(const n of ns)if(n.tsv_node!=null&&n.cross_die){const p=node(n.tsv_node);if($('layer').value!=='all'&&!['10','11'].includes($('layer').value))continue;const[x,y]=xy(p.x,p.y);ctx.strokeStyle='#d58928';ctx.fillStyle='#fff4d2';ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(x,y,4.5,0,Math.PI*2);ctx.fill();ctx.stroke();ctx.beginPath();ctx.moveTo(x-2,y);ctx.lineTo(x+2,y);ctx.moveTo(x,y-2);ctx.lineTo(x,y+2);ctx.stroke();}
    if(S.ghost){const color=S.ghost.remove?'#e05d6c':'#13ad88';for(const e of S.ghost.edges||[]){const a=node(e[0]),b=node(e[1]);if(a.l===b.l&&layerVisible(a.l,tier))line(a,b,color,2.8);}}
    rect(d.die,null,'#8da1bb',1);ctx.fillStyle='#98a5b5';ctx.font='9px Segoe UI';const[a,b]=xy(x1,y1);ctx.fillText(`${fmt(x1,0)}, ${fmt(y1,0)}`,Math.max(2,a),Math.min(h-4,b+14));const[c,e]=xy(x2,y2);ctx.fillText(`${fmt(x2,0)}, ${fmt(y2,0)} DBU`,Math.min(w-125,c-115),Math.max(11,e-7));
}
function hash(s){let h=0;for(const c of s)h=((h<<5)-h+c.charCodeAt(0))|0;return h>>>0;}
function applyEvent(e){S.ghost=null;if(e.event==='net_added'){S.active.set(e.net,e);S.ghost={edges:e.edges,remove:false};}else if(e.event==='net_removed'){const old=S.active.get(e.net);S.ghost={edges:old?.edges,remove:true};S.active.delete(e.net);}else if(e.event==='path_found'){const edges=[];for(let i=1;i<e.nodes.length;i++)edges.push([e.nodes[i-1],e.nodes[i]]);S.ghost={edges,remove:false};}}
const eventNames={parse_started:'开始解析输入',parse_complete:'输入解析完成',graph_built:'建立 Routing Graph',thermal_mapped:'热数据映射',fixed_demand_built:'固定需求统计',net_selected:'选择 Net',topology:'生成 MST 拓扑',search_started:'开始 A* 路径搜索',path_found:'找到真实路径',net_added:'提交路径与容量',net_removed:'拆除旧路径',iteration_done:'优化轮次完成',routing_complete:'完成布线',trace_truncated:'Trace 达到大小上限'};
function seek(index){if(!S.trace.length)return;index=Math.max(0,Math.min(S.trace.length-1,index));if(S.index<0||index<S.index){S.active=new Map();S.index=-1;}for(let i=S.index+1;i<=index;i++)applyEvent(S.trace[i]);S.index=index;const e=S.trace[index];$('timeline').value=index;$('eventtitle').textContent=eventNames[e.event]||e.event;$('eventdetail').textContent=`${e.net||S.data.design}${e.iteration!==undefined?' · 轮次 '+e.iteration:''} · ${e.event}`;$('step').textContent=`${index+1} / ${S.trace.length}`;$('eventjson').textContent=JSON.stringify(e,null,2);draw();}
function stop(){if(S.playing)clearInterval(S.playing);S.playing=null;$('play').textContent='播放';}
async function scopedTrace(){
    if(!S.largeTrace||S.data?.source==='official_reference_guide')return;stop();const selected=S.selected;
    const tr=await api('/api/trace?id='+encodeURIComponent($('run').value)+(selected==='all'?'':'&net='+encodeURIComponent(selected)));
    if(selected!==S.selected)return;S.trace=tr.events;S.index=-1;S.ghost=null;S.active=new Map(S.data.nets.map(n=>[n.net,n]));
    $('timeline').max=Math.max(0,S.trace.length-1);$('timeline').value=0;$('timeline').disabled=!S.trace.length;['prev','play','next'].forEach(k=>$(k).disabled=!S.trace.length);
    $('eventtitle').textContent=selected==='all'?'大型 Trace · 请选择单个 Net':`${selected} · 真实事件回放`;$('eventdetail').textContent=selected==='all'?'按单个 Net 读取过程。':`${tr.matched_events} 条属于该 Net 的事件${S.data.trace_truncated?' · 原始 Trace 已达上限，后续记录可能缺失':''}`;$('step').textContent=`最终 / ${S.trace.length}`;draw();
}
function play(){if(S.playing){stop();return;}if(S.index<0||S.index===S.trace.length-1)seek(0);$('play').textContent='暂停';S.playing=setInterval(()=>{if(S.index>=S.trace.length-1){stop();return;}seek(S.index+1);},+$('speed').value);}
async function raw(){if(S.raw||!S.data)return;try{S.raw=await api('/api/thermal?id='+encodeURIComponent($('run').value));draw();}catch(e){error(e);}}
for(const t of ['bottom','top']){
    const c=$(t);let drag=null,moved=false;
    c.addEventListener('wheel',e=>{e.preventDefault();const prev=S.zoom;S.zoom=Math.max(.3,Math.min(60,S.zoom*Math.exp(-e.deltaY*.001)));const r=c.getBoundingClientRect(),px=e.clientX-r.left-r.width/2,py=e.clientY-r.top-r.height/2;S.panX=px-(px-S.panX)*S.zoom/prev;S.panY=py-(py-S.panY)*S.zoom/prev;draw();},{passive:false});
    c.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,px:S.panX,py:S.panY};moved=false;c.setPointerCapture(e.pointerId);});
    c.addEventListener('pointerup',e=>{if(drag&&!moved){const hit=hover(e,t);if(hit){S.selected=hit.n;$('net').value=hit.n;details();draw();scopedTrace().catch(error);}}drag=null;});
    c.addEventListener('pointermove',e=>{if(drag){if(Math.abs(e.clientX-drag.x)+Math.abs(e.clientY-drag.y)>3)moved=true;S.panX=drag.px+e.clientX-drag.x;S.panY=drag.py+e.clientY-drag.y;draw();}else hover(e,t);});
    c.addEventListener('pointerleave',()=>{$('tooltip').hidden=true;});
}
function hover(e,t){const c=$(t);if(!c._world||!S.data)return null;const box=c.getBoundingClientRect(),[x,y]=c._world(e.clientX-box.left,e.clientY-box.top);const hit=[...S.hit].reverse().find(v=>v.tier===t&&x>=v.g[0]&&x<=v.g[2]&&y>=v.g[1]&&y<=v.g[3]);let text=hit?`${hit.n}\nMetal${hit.g[4]} · Guide [${hit.g.slice(0,4).join(', ')}]`:'';
    if(c._heat){const d=S.data,gx=d.x_lines.findIndex(v=>v>x)-1,gy=d.y_lines.findIndex(v=>v>y)-1,nx=d.x_lines.length-1,ny=d.y_lines.length-1;if(gx>=0&&gx<nx&&gy>=0&&gy<ny){const k=gy*nx+gx,info=c._heat.info.get(k);text=`GCell (${gx}, ${gy}) · ${fmt(c._heat.values[k],5)}`;if(info)text+=`\nM${info.l} Capacity=${info.cap}\nUsage=${info.usage} Fixed=${info.fixed} Overflow=${info.overflow}`;}}
    $('tooltip').hidden=!text;$('tooltip').textContent=text;$('tooltip').style.left=Math.min(innerWidth-300,e.clientX+14)+'px';$('tooltip').style.top=Math.min(innerHeight-120,e.clientY+12)+'px';return hit;}
$('dataset').onchange=setCases;$('case').onchange=setRuns;$('source').onchange=load;$('run').onchange=load;$('reload').onclick=init;
$('search').oninput=setNets;$('crossOnly').onchange=setNets;$('net').onchange=()=>{S.selected=$('net').value;details();draw();scopedTrace().catch(error);};
for(const id of ['tier','layer','guides','paths','pins','tsvs','grid','instances'])$(id).onchange=draw;
$('fit').onclick=fit;$('compare').onchange=compare;$('prev').onclick=()=>{stop();seek(S.index<0?S.trace.length-1:S.index-1);};$('next').onclick=()=>{stop();seek(S.index+1);};$('play').onclick=play;$('speed').onchange=()=>{if(S.playing){stop();play();}};$('timeline').oninput=()=>{stop();seek(+$('timeline').value);};$('final').onclick=()=>{stop();S.index=-1;S.active=new Map(S.data?.nets.map(n=>[n.net,n]));S.ghost=null;$('eventtitle').textContent='最终真实结果';$('step').textContent=`最终 / ${S.trace.length}`;draw();};
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=async()=>{S.view=b.dataset.view;document.querySelectorAll('[data-view]').forEach(x=>x.classList.toggle('active',x===b));if(S.view==='raw')await raw();draw();});
new ResizeObserver(()=>draw()).observe(document.querySelector('.canvases'));init();
