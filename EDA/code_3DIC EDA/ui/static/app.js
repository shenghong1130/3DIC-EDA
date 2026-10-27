"use strict";
const $ = id => document.getElementById(id);
const S = {
    catalog:null, data:null, trace:[], fullTrace:[], traceByNet:new Map(), largeTrace:false,
    raw:null, view:'routing', index:-1, active:new Map(), pending:new Map(), selected:'all',
    ghost:null, playing:null, zoom:1, panX:0, panY:0, loadId:0, traceRequest:0, selectionVersion:0,
    netMap:new Map(), allPins:[], pinIndex:new Map(), matches:[], allowed:new Set(),
    page:0, colors:new Map(), resourceMap:new Map(), stats:null, inspect:null,
    choices:[], choicePage:0, loading:false, filterKey:null, filteredPins:[], frame:0
};
const PAGE_SIZE = 500, PIN_BUCKETS = 128, CHOICE_SIZE = 20;
const esc = x => String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (x,d=2) => x==null||typeof x==='number'&&!Number.isFinite(x)?'N/A':typeof x==='number'?x.toLocaleString('zh-CN',{maximumFractionDigits:d}):String(x);
const api = async url => {const r=await fetch(url),d=await r.json();if(!r.ok)throw Error(d.error||r.statusText);return d;};
const yieldUI = () => new Promise(resolve=>setTimeout(resolve,0));
function error(e){$('error').hidden=!e;$('error').textContent=e?.message||e||'';}
function options(el,items,value){el.innerHTML=items.map(([v,t])=>'<option value="'+esc(v)+'">'+esc(t)+'</option>').join('');if(value!==undefined&&items.some(x=>x[0]===value))el.value=value;}
function table(headers,rows){return '<table><thead><tr>'+headers.map(h=>'<th>'+esc(h)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(x=>'<td>'+esc(x)+'</td>').join('')+'</tr>').join('')+'</tbody></table>';}
function hash(s){let h=2166136261;for(const c of s){h^=c.codePointAt(0);h=Math.imul(h,16777619);}return h>>>0;}
function netColor(name){if(!S.colors.has(name))S.colors.set(name,'hsl('+(hash(name)%360)+',65%,42%)');return S.colors.get(name);}
function layerVisible(l,tier){return (tier==='bottom'?l>=1&&l<=10:l>=11&&l<=20)&&($('layer').value==='all'||+$('layer').value===l);}
function tierVisible(tier){return $('tier').value==='both'||$('tier').value===tier;}
function reference(){return S.data?.source==='official_reference_guide';}
async function init(){try{
    S.catalog=await api('/api/catalog');
    options($('dataset'),[...new Set(S.catalog.cases.map(c=>c.dataset))].map(x=>[x,x]),$('dataset').value);
    setCases();
}catch(e){error(e);}}
function setCases(){
    const cs=S.catalog.cases.filter(c=>c.dataset===$('dataset').value);
    const available=c=>S.catalog.runs.some(r=>r.case===c.id&&r.status==='ROUTED');
    const ready=cs.find(c=>c.id==='toy'&&available(c))||cs.find(available);
    options($('case'),cs.map(c=>[c.id,c.id]),cs.some(c=>c.id===$('case').value)?$('case').value:ready?.id);
    setRuns();
}
function setRuns(){
    const rs=S.catalog.runs.filter(r=>r.case===$('case').value&&r.status==='ROUTED');
    options($('run'),rs.map(r=>[r.id,r.label+' · '+r.official_status]),rs.some(r=>r.id===$('run').value)?$('run').value:rs.find(r=>r.label==='final')?.id);
    options($('compare'),[['','选择另一条真实记录'],...rs.map(r=>[r.id,r.label])]);load();
}
function clearData(){
    S.data=null;S.allPins=[];S.pinIndex=new Map();S.netMap=new Map();S.active=new Map();S.pending=new Map();
    S.trace=[];S.fullTrace=[];S.traceByNet=new Map();S.largeTrace=false;S.index=-1;S.ghost=null;
    S.inspect=null;S.choices=[];S.selected='all';S.filterKey=null;S.stats=null;S.learningKey=null;
    $('pinchoices').hidden=true;$('choiceList').innerHTML='';$('tooltip').hidden=true;$('pinStats').textContent='';
    $('inspectdetail').innerHTML='<p class="small">点击 Pin 查看 GCell 与真实 Node ID。</p>';
    options($('net'),[['all','全部符合条件网络']]);options($('layer'),[['all','全部金属层']]);
    for(const id of ['prev','play','next','timeline','fitNet','focus','mst'])$(id).disabled=true;
}
async function load(){
    const id=++S.loadId;S.traceRequest++;stop();error(null);clearData();S.loading=true;draw();
    try{
        const ref=$('source').value==='reference',run=$('run').value;
        if(!run&&!ref){$('subtitle').textContent='该用例尚无完整运行记录。请先使用 scripts/run_case.py 运行。';$('badge').textContent='NOT RUN';$('metrics').innerHTML='';$('netdetail').textContent='N/A';$('report').textContent='Not Tested';$('logs').textContent='';return;}
        $('subtitle').textContent='读取真实结果并建立全量 Pin 索引…';
        const data=await api(ref?'/api/reference?case='+encodeURIComponent($('case').value):'/api/run?id='+encodeURIComponent(run));
        const tr=ref?{events:[]}:await api('/api/trace?id='+encodeURIComponent(run));
        if(id!==S.loadId)return;
        S.data=data;S.fullTrace=tr.events;S.largeTrace=!!tr.requires_net;S.raw=null;S.colors=new Map();
        if(!await buildIndex(id))return;
        options($('layer'),[['all','全部金属层'],...data.layers.map(l=>[String(l.number),l.name+' · '+(l.direction==='HORIZONTAL'?'水平':'垂直')])]);
        S.active=new Map(data.nets.map(n=>[n.net,n]));S.zoom=1;S.panX=0;S.panY=0;setNets(false);installTrace(S.fullTrace);
        $('subtitle').textContent=$('case').value+' / '+(ref?'官方参考 Guide':data.run.label)+' · '+fmt(data.nets.length,0)+' Nets · '+(data.x_lines.length-1)+' × '+(data.y_lines.length-1)+' GCells';
        $('badge').textContent=ref?'REFERENCE · 仅展示':'官方 '+data.run.official_status;
        $('badge').classList.toggle('fail',!ref&&data.run.official_status!=='PASS');
        $('paths').disabled=ref;$('overviewPaths').disabled=ref;$('tsvs').disabled=ref;
        metrics();details();reports();compare();if(S.view==='raw')await raw();
    }catch(e){if(id===S.loadId){clearData();error(e);$('subtitle').textContent='加载失败，请检查所选记录与下方错误。';}}
    finally{if(id===S.loadId){S.loading=false;draw();}}
}
// Store every recorded terminal once. Path display limits never enter this index.
async function buildIndex(loadId=S.loadId){
    S.netMap=new Map();S.allPins=[];S.pinIndex=new Map();S.resourceMap=new Map();S.filterKey=null;
    const [x1,y1,x2,y2]=S.data.die;let budget=performance.now();
    for(const n of S.data.nets){
        S.netMap.set(n.net,n);
        for(let i=0;i<(n.pins||[]).length;i++){
            const p=n.pins[i],pin={p,net:n.net,cross:!!n.cross_die,key:S.allPins.length};
            S.allPins.push(pin);
            const bx=Math.max(0,Math.min(PIN_BUCKETS-1,Math.floor((p.x-x1)/(x2-x1)*PIN_BUCKETS)));
            const by=Math.max(0,Math.min(PIN_BUCKETS-1,Math.floor((p.y-y1)/(y2-y1)*PIN_BUCKETS)));
            const key=by*PIN_BUCKETS+bx;if(!S.pinIndex.has(key))S.pinIndex.set(key,[]);S.pinIndex.get(key).push(pin);
        }
        if(performance.now()-budget>8){await yieldUI();if(loadId!==S.loadId)return false;budget=performance.now();}
    }
    for(let i=0;i<(S.data.resources||[]).length;i++){
        const r=S.data.resources[i];S.resourceMap.set(r[0],r);
        if(i%8192===0&&performance.now()-budget>8){await yieldUI();if(loadId!==S.loadId)return false;budget=performance.now();}
    }
    return true;
}
function setNets(resetPage=true){
    if(!S.data)return;
    const q=$('search').value.toLowerCase(),cross=$('crossOnly').checked;
    S.matches=S.data.nets.filter(n=>(!cross||n.cross_die)&&n.net.toLowerCase().includes(q));
    S.allowed=new Set(S.matches.map(n=>n.net));S.filterKey=null;
    if(!S.allowed.has(S.selected)){S.selected='all';S.inspect=null;}
    if(resetPage)S.page=0;
    netOptions();details();draw();
}
function netOptions(){
    S.page=Math.max(0,Math.min(S.page,Math.max(0,Math.ceil(S.matches.length/PAGE_SIZE)-1)));
    const start=S.page*PAGE_SIZE,items=S.matches.slice(start,start+PAGE_SIZE);
    if(S.selected!=='all'&&!items.some(n=>n.net===S.selected)){const n=S.netMap.get(S.selected);if(n)items.push(n);}
    options($('net'),[['all','全部符合条件网络 ('+fmt(S.matches.length,0)+')'],...items.map(n=>[n.net,n.net+(n.cross_die?' ↕':'')])],S.selected);
    $('netPage').textContent=S.matches.length?(start+1)+'–'+Math.min(start+PAGE_SIZE,S.matches.length)+' / '+fmt(S.matches.length,0):'0 / 0';
    $('netPrev').disabled=S.page===0;$('netNext').disabled=start+PAGE_SIZE>=S.matches.length;
}
async function selectNet(name,{autoFit=true,pin=null}={}){
    if(S.loading||!S.data||name!=='all'&&!S.netMap.has(name))return false;
    const version=++S.selectionVersion,data=S.data;
    stop();S.selected=name;S.inspect=pin?{kind:'pin',pin}:null;S.choices=[];$('pinchoices').hidden=true;
    const index=S.matches.findIndex(n=>n.net===name);if(index>=0)S.page=Math.floor(index/PAGE_SIZE);
    netOptions();details();inspectDetails();if(autoFit&&name!=='all')fitNet();else draw();
    try{await scopedTrace();}catch(e){if(S.data===data&&S.selectionVersion===version)error(e);return false;}
    return S.data===data&&S.selectionVersion===version;
}
function metrics(){const m=S.data.metrics;const rows=[['Net 总数',m.nets,'来自实际输入'],['已布线 Net',m.routed_nets,'自主路由结果'],['图边总线长',m.wirelength_dbu_local===null?null:m.wirelength_dbu_local/S.data.dbu_per_micron,'µm · 本地几何估计'],['Overflow',m.overflow_local,'本地近似 · 含 Pin/OBS'],['TSV 数量',m.tsv_count,'实际跨 Die 边'],['热相关 Pearson',m.thermal_correlation_local,'本地映射 · 非官方得分'],['算法运行时间',m.runtime_seconds,'s · 不含 WSL 启动']];$('metrics').innerHTML=rows.map(([k,v,desc])=>`<div class="metric"><small>${esc(k)}</small><strong>${fmt(v,3)}</strong><em>${esc(desc)}</em></div>`).join('');}
function reports(){const d=S.data,r=d.official_latest;$('report').textContent=r?JSON.stringify(r,null,2):'Not Tested · 当前结果未执行新版官方评分。';$('logs').textContent=d.router_log;$('warnings').textContent=JSON.stringify({source:d.source,inputs:d.run.inputs,warnings:d.warnings,trace_truncated:d.trace_truncated},null,2);$('fidelity').textContent='官方工具评估 GRT Guide 的合法性、线长、拥塞与热指标；报告中的 mock-DRT / proxy 不代表详细布线 DRC 签核。';}
function reportMetrics(d){const r=d.official_latest?.report;return [['本地图边线长 (DBU)',d.metrics.wirelength_dbu_local],['本地 Overflow',d.metrics.overflow_local],['TSV 数量',d.metrics.tsv_count],['本地 Pearson',d.metrics.thermal_correlation_local],['Router 时间 (s)',d.metrics.runtime_seconds],['峰值内存 (MiB)',d.run.peak_memory_bytes==null?null:d.run.peak_memory_bytes/1048576],['官方合法性',d.official_latest?.status??'Not Tested'],['官方 Wirelength 得分',r?.score?.wirelength_score],['官方 Congestion 得分',r?.score?.congestion_score],['官方 Thermal 得分',r?.score?.thermal_score]];}
async function compare(){if(!S.data)return;const current=S.data,key=$('compare').value;try{const b=key?await api('/api/run?id='+encodeURIComponent(key)):null;if(S.data!==current)return;const a=reportMetrics(current),bb=b?reportMetrics(b):[];$('comparetable').innerHTML=table(['指标',`A · ${current.source==='official_reference_guide'?'参考 Guide':current.run.label}`,b?`B · ${b.run.label}`:'B · 未选择'],a.map(([k,v],i)=>[k,fmt(v,4),fmt(bb[i]?.[1],4)]));}catch(e){error(e);}}
function node(id){
    if(!S.data||!Number.isSafeInteger(id)||id<0)return null;
    const d=S.data,nx=d.x_lines.length-1,ny=d.y_lines.length-1,gx=id%nx,gy=Math.floor(id/nx)%ny,l=Math.floor(id/(nx*ny));
    if(!d.layers.some(layer=>layer.number===l))return null;
    const box=[d.x_lines[gx],d.y_lines[gy],d.x_lines[gx+1],d.y_lines[gy+1]];
    return {id,x:(box[0]+box[2])/2,y:(box[1]+box[3])/2,l,gx,gy,box};
}
function pinInfo(pin){
    const p=pin.p,v=node(p.node);
    return [['Pin',p.name],['Net',pin.net],['Die / Tier',p.layer<=10?'Die 1 · Bottom':'Die 2 · Top'],['Metal Layer','M'+p.layer],['Pin 坐标 (DBU)',fmt(p.x,3)+', '+fmt(p.y,3)],['GCell (gx, gy)',v?'('+v.gx+', '+v.gy+')':'N/A · 无有效 Node'],['GCell 边界 (DBU)',v?'['+v.box.map(x=>fmt(x,3)).join(', ')+']':'N/A'],['Node ID',p.node??'N/A'],['Node 中心 (DBU)',v?fmt(v.x,3)+', '+fmt(v.y,3):'N/A']];
}
function filteredPins(){
    const key=$('tier').value+'|'+$('layer').value;
    if(S.filterKey!==key){
        S.filteredPins=S.allPins.filter(pin=>S.allowed.has(pin.net)&&tierVisible(pin.p.layer<=10?'bottom':'top')&&layerVisible(pin.p.layer,pin.p.layer<=10?'bottom':'top'));
        S.filterKey=key;
    }
    return S.filteredPins;
}
function queryPins(box,tier){
    const [x1,y1,x2,y2]=S.data.die,out=[];
    if(box[2]<x1||box[0]>x2||box[3]<y1||box[1]>y2)return out;
    const bucket=(v,lo,hi)=>Math.max(0,Math.min(PIN_BUCKETS-1,Math.floor((v-lo)/(hi-lo)*PIN_BUCKETS)));
    for(let by=bucket(box[1],y1,y2);by<=bucket(box[3],y1,y2);by++)
        for(let bx=bucket(box[0],x1,x2);bx<=bucket(box[2],x1,x2);bx++)
            for(const pin of S.pinIndex.get(by*PIN_BUCKETS+bx)||[]){
                const p=pin.p;
                if(S.allowed.has(pin.net)&&layerVisible(p.layer,tier)&&p.x>=box[0]&&p.x<=box[2]&&p.y>=box[1]&&p.y<=box[3])out.push(pin);
            }
    return out;
}
function visibleNets(){
    if(!S.data)return [];
    if(S.selected!=='all'){
        const n=S.active.get(S.selected),pending=S.pending.get(S.selected);
        if(pending)return [{net:S.selected,edges:pending,guides:[],cross_die:S.netMap.get(S.selected)?.cross_die}];
        return n?[n]:[];
    }
    const out=[];for(const n of S.active.values()){if(S.allowed.has(n.net))out.push(n);if(out.length>=150)break;}return out;
}
function fit(){S.zoom=1;S.panX=0;S.panY=0;draw();}
function fitNet(){
    if(!S.data||S.selected==='all')return;
    const n=S.netMap.get(S.selected),current=S.active.get(S.selected);
    let xmin=Infinity,ymin=Infinity,xmax=-Infinity,ymax=-Infinity;
    const add=(x,y)=>{xmin=Math.min(xmin,x);ymin=Math.min(ymin,y);xmax=Math.max(xmax,x);ymax=Math.max(ymax,y);};
    for(const p of n.pins||[])if(tierVisible(p.layer<=10?'bottom':'top')&&layerVisible(p.layer,p.layer<=10?'bottom':'top')){add(p.x,p.y);const v=node(p.node);if(v){add(v.box[0],v.box[1]);add(v.box[2],v.box[3]);}}
    for(const id of current?.nodes||[]){const v=node(id);if(v&&tierVisible(v.l<=10?'bottom':'top')&&layerVisible(v.l,v.l<=10?'bottom':'top'))add(v.x,v.y);}
    if(!Number.isFinite(xmin))return fit();
    const canvas=$($('tier').value==='top'?'top':'bottom'),r=canvas.getBoundingClientRect(),[x1,y1,x2,y2]=S.data.die;
    const base=Math.min((r.width-48)/(x2-x1),(r.height-48)/(y2-y1)),dx=Math.max(xmax-xmin,(x2-x1)/80),dy=Math.max(ymax-ymin,(y2-y1)/80);
    S.zoom=Math.max(.3,Math.min(60,Math.min((r.width-90)/dx,(r.height-90)/dy)/base));
    S.panX=-((xmin+xmax-x1-x2)/2)*base*S.zoom;S.panY=((ymin+ymax-y1-y2)/2)*base*S.zoom;draw();
}
function heatColor(t){t=Math.max(0,Math.min(1,t));return 'rgb('+Math.round(52+193*t)+','+Math.round(139-64*t)+','+Math.round(213-130*t)+')';}
function draw(){if(!S.frame)S.frame=requestAnimationFrame(()=>{S.frame=0;render();});}
function render(){
    const tier=$('tier').value;
    $('bottomPanel').hidden=S.view==='raw'?false:tier==='top';$('topPanel').hidden=S.view==='raw'?true:tier==='bottom';
    if(!S.data||S.loading){
        for(const id of ['bottom','top']){const c=$(id);c.getContext('2d').clearRect(0,0,c.width,c.height);c._world=null;}
        $('pinStats').textContent=S.loading?'正在建立全量 Pin 索引…':'无可显示结果';$('process').innerHTML='';return;
    }
    const ns=visibleNets(),learn=learning();
    S.stats={total:S.allPins.length,filtered:filteredPins().length,inView:0,points:0,clusters:0,represented:0};
    document.querySelector('#bottomPanel .plotlabel').innerHTML=S.view==='raw'?'原始温度节点 · XY 投影':'<span class="dot blue"></span>Die 1 <span>Bottom · M1–M10</span>';
    for(const t of ['bottom','top'])if(!$(t+'Panel').hidden)drawPanel(t,ns,learn);
    const stats=S.stats;
    $('pinStats').textContent='总 Pin '+fmt(stats.total,0)+' · 筛选 '+fmt(stats.filtered,0)+' · 视口 '+fmt(stats.inView,0)+' · 绘制 '+fmt(stats.points,0)+' 个单 Pin 点 + '+fmt(stats.clusters,0)+' 个聚合点（覆盖 '+fmt(stats.represented,0)+' Pin）';
    const notes={
        routing:(S.selected==='all'?'总览：全部符合筛选的 Pin 独立绘制；完整路径默认隐藏。':'聚焦 '+S.selected+'：高亮其端子与真实路径，其他网络 Pin 弱化。')+' Guide 区域和 GCell 网格是独立图层。'+($('overviewPaths').checked&&S.selected==='all'?'路径总览最多 150 Net；Pin 不受此限制。':'')+' Pin 为结果中的端子访问点；聚合点可点击展开。',
        congestion:'最终资源快照：(Usage + Fixed) / max(1, Capacity)。灰色表示无记录（N/A），不表示容量为零；回放不重算历史资源。',
        thermal:'投影热图：按 XY 合并不同 z 温度、落格均值与缺格填充，属于本地映射。',
        density:reference()?'参考 Guide 无实际 TSV 密度数据，不推断。':'TSV 密度来自最终 Route 的实际跨 Die 边，与普通 Via 不同。',
        raw:'原始 Thermal JSON 的 XY 投影，保留温度，不由 z 正负判断 Die。超过约 10,000 节点时抽样。'
    };
    $('viewnote').textContent=notes[S.view];
    $('overview').classList.toggle('active',S.selected==='all');$('focus').classList.toggle('active',S.selected!=='all');
    $('focus').disabled=S.selected==='all';$('fitNet').disabled=S.selected==='all';
    $('mst').disabled=reference()||S.selected==='all'||!learn.topologies.length;
    $('mstnote').textContent=reference()?'参考 Guide 没有 MST / A* 过程。':S.selected==='all'?'选中 Net 后读取真实 topology 事件。':learn.topologies.length?'来自当前 Route / 回放对应的 topology；虚线不是金属走线。':'该步骤没有可用过程数据（缺失、截断或无法对应当前 Route），不重建 MST。';
    legend(learn);processPanel(learn);
}
function legend(learn){
    const items=[];
    if(S.view!=='routing'){
        items.push(['heat-low','低'],['heat-high','高']);
        if(S.view==='congestion')items.push(['missing','N/A · 无资源记录']);
    }else{
        if($('grid').checked)items.push(['grid-key','GCell 网格']);
        if($('instances').checked)items.push(['instance-key','Instance 轮廓']);
        if($('guides').checked)items.push(['guide-key','Guide 区域']);
        if($('paths').checked&&!reference()&&(S.selected!=='all'||$('overviewPaths').checked))items.push(['route-key','真实 Route / A* 实线'],['via-key','普通 Via 方形']);
        if($('mst').checked&&!$('mst').disabled&&learn.topologies.length)items.push(['mst-key','MST 拓扑虚线 · 非导线']);
        if($('pins').checked)items.push(['pin-key','Pin · 同 Net 同色，无方向含义'],['cluster-key','密集 Pin 聚合 · 点击选择']);
        if($('tsvs').checked&&!reference())items.push(['tsv-key','TSV · M10 ↔ M11']);
        if(S.inspect?.kind==='pin')items.push(['mapping-key','Pin → Node 抽象映射辅助线']);
        if(S.ghost&&$('paths').checked&&(S.selected!=='all'||$('overviewPaths').checked))items.push([S.ghost.remove?'removed-key':'added-key',S.ghost.remove?'本步移除边':'本步新增边']);
    }
    $('legend').innerHTML=items.map(([c,t])=>'<span><i class="'+c+'"></i>'+esc(t)+'</span>').join('');
}
function drawPanel(tier,ns,learn){
    const canvas=$(tier),bounds=canvas.getBoundingClientRect(),ratio=devicePixelRatio||1,w=bounds.width,h=bounds.height;
    if(!w||!h)return;
    if(canvas.width!==Math.round(w*ratio)||canvas.height!==Math.round(h*ratio)){canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);}
    const ctx=canvas.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,w,h);
    const d=S.data,[x1,y1,x2,y2]=d.die,base=Math.min((w-48)/(x2-x1),(h-48)/(y2-y1)),scale=base*S.zoom,cx=w/2+S.panX,cy=h/2+S.panY;
    const xy=(x,y)=>[cx+(x-(x1+x2)/2)*scale,cy-(y-(y1+y2)/2)*scale];
    canvas._world=(px,py)=>[(px-cx)/scale+(x1+x2)/2,(cy-py)/scale+(y1+y2)/2];canvas._xy=xy;canvas._scale=scale;canvas._edges=[];canvas._guides=[];
    const rect=(r,fill,stroke,width=1)=>{const[a,b]=xy(r[0],r[3]),[c,e]=xy(r[2],r[1]);if(c<0||a>w||e<0||b>h)return;if(fill){ctx.fillStyle=fill;ctx.fillRect(a,b,c-a,e-b);}if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=width;ctx.strokeRect(a,b,c-a,e-b);}};
    const line=(a,b,color,width=1.5,dash=[])=>{const[p,q]=xy(a.x,a.y),[r,s]=xy(b.x,b.y);if(Math.max(p,r)<-8||Math.min(p,r)>w+8||Math.max(q,s)<-8||Math.min(q,s)>h+8)return false;ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dash);ctx.beginPath();ctx.moveTo(p,q);ctx.lineTo(r,s);ctx.stroke();ctx.setLineDash([]);return true;};
    rect(d.die,'#fcfdfe','#dce1e7');
    const nx=d.x_lines.length-1,ny=d.y_lines.length-1;
    if(['thermal','density','congestion'].includes(S.view)){
        let values=S.view==='thermal'?d.heat:S.view==='density'?d.tsv_density:new Float64Array(nx*ny).fill(NaN),info=new Map();
        if(S.view==='congestion')for(const r of d.resources||[]){
            const v=node(r[0]);if(!v||!layerVisible(v.l,tier))continue;
            const key=v.gy*nx+v.gx,score=(r[2]+r[3])/Math.max(1,r[1]);
            if(!Number.isFinite(values[key])||score>=values[key]){values[key]=score;info.set(key,{cap:r[1],usage:r[2],fixed:r[3],overflow:r[4],l:v.l});}
        }
        let min=Infinity,max=-Infinity;for(const v of values)if(Number.isFinite(v)){min=Math.min(min,v);max=Math.max(max,v);}
        for(let y=0;y<ny;y++)for(let x=0;x<nx;x++){
            const v=values[y*nx+x];if(!Number.isFinite(v))continue;
            rect([d.x_lines[x],d.y_lines[y],d.x_lines[x+1],d.y_lines[y+1]],heatColor((v-min)/Math.max(1e-12,max-min)),null);
        }
        canvas._heat={values,info};
    }else canvas._heat=null;
    if(S.view==='raw'&&S.raw){
        let min=Infinity,max=-Infinity;for(const n of S.raw.nodes){min=Math.min(min,n[3]);max=Math.max(max,n[3]);}
        for(const n of S.raw.nodes){const[a,b]=xy(x1+n[0]*d.dbu_per_micron,y1+n[1]*d.dbu_per_micron);ctx.fillStyle=heatColor((n[3]-min)/Math.max(1e-12,max-min));ctx.beginPath();ctx.arc(a,b,2.2,0,2*Math.PI);ctx.fill();}
    }
    if($('grid').checked){ctx.strokeStyle='#d9dee5';ctx.lineWidth=.55;ctx.beginPath();let last=-Infinity;
        for(const x of d.x_lines){const[a,b]=xy(x,y1),[c,e]=xy(x,y2);if(a<0||a>w||a-last<3)continue;last=a;ctx.moveTo(a,b);ctx.lineTo(c,e);}
        last=Infinity;for(const y of d.y_lines){const[a,b]=xy(x1,y),[c,e]=xy(x2,y);if(b<0||b>h||last-b<3)continue;last=b;ctx.moveTo(a,b);ctx.lineTo(c,e);}ctx.stroke();
    }
    if($('instances').checked){let count=0;for(const i of d.instances||[]){if(!i.tiers.includes(tier==='bottom'?1:2))continue;if(++count>12000)break;rect(i.box,null,'#aeb8c12e',.6);}}
    const focused=S.selected!=='all',showPaths=$('paths').checked&&!reference()&&(focused||$('overviewPaths').checked);
    function drawEdge(edge,color,width,kind='path'){
        const a=node(edge[0]),b=node(edge[1]);if(!a||!b)return;
        const shown=[a,b].filter(v=>layerVisible(v.l,tier));if(!shown.length)return;
        if(a.l===b.l){if(!line(a,b,color,width))return;}
        else if(Math.min(a.l,b.l)===10)return; // TSV has its own orange layer.
        else{const[x,y]=xy(shown[0].x,shown[0].y);if(x<0||x>w||y<0||y>h)return;ctx.fillStyle=color;ctx.fillRect(x-2,y-2,4,4);}
        canvas._edges.push({kind,net:edge._net,edge,a:xy(a.x,a.y),b:xy(b.x,b.y),layers:shown.map(v=>v.l)});
    }
    if(S.view==='routing'){
        for(const n of ns){
            const color=netColor(n.net);
            if($('guides').checked)for(const g of n.guides||[])if(layerVisible(g[4],tier)){
                ctx.globalAlpha=.10;rect(g,color,null);ctx.globalAlpha=.35;rect(g,null,color,.7);ctx.globalAlpha=1;canvas._guides.push({kind:'guide',net:n.net,g});
            }
            if(showPaths)for(const edge of n.edges||[])drawEdge(Object.assign([...edge],{_net:n.net}),color,focused?2.3:1);
        }
        if($('mst').checked&&!reference()&&focused)for(const topo of learn.topologies)if(topo.tier===tier){
            for(const edge of topo.mst_edges||[]){
                const a=node(edge[0]),b=node(edge[1]);if(!a||!b||!layerVisible(a.l,tier)&&!layerVisible(b.l,tier))continue;
                if(line(a,b,'#9169b5',1.5,[6,5]))canvas._edges.push({kind:'mst',net:S.selected,edge,a:xy(a.x,a.y),b:xy(b.x,b.y),layers:[a.l,b.l]});
            }
            for(const id of topo.terminal_nodes||[]){const v=node(id);if(!v||!layerVisible(v.l,tier))continue;const[x,y]=xy(v.x,v.y);ctx.strokeStyle='#9169b5';ctx.lineWidth=1.6;ctx.strokeRect(x-3,y-3,6,6);}
        }
        if($('tsvs').checked&&!reference())for(const n of ns)if(n.cross_die&&n.tsv_node!=null&&($('layer').value==='all'||+$('layer').value===(tier==='bottom'?10:11))){
            const v=node(n.tsv_node);if(!v)continue;const[x,y]=xy(v.x,v.y);ctx.strokeStyle='#d58218';ctx.fillStyle='#fff5da';ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,5.2,0,2*Math.PI);ctx.fill();ctx.stroke();ctx.beginPath();ctx.moveTo(x-3,y);ctx.lineTo(x+3,y);ctx.moveTo(x,y-3);ctx.lineTo(x,y+3);ctx.stroke();
            const other=n.tsv_node+nx*ny;canvas._edges.push({kind:'tsv',net:n.net,edge:[n.tsv_node,other],a:[x,y],b:[x,y],layers:[tier==='bottom'?10:11]});
        }
        if(showPaths&&S.ghost&&(S.selected==='all'||S.ghost.net===S.selected))for(const edge of S.ghost.edges||[])drawEdge(Object.assign([...edge],{_net:S.ghost.net}),S.ghost.remove?'#d95062':'#12a178',2.8);
    }
    const lo=canvas._world(0,h),hi=canvas._world(w,0),pins=queryPins([lo[0],lo[1],hi[0],hi[1]],tier);
    if(S.view!=='raw')S.stats.inView+=pins.length;
    if(S.view==='routing'&&$('pins').checked){
        const groups=new Map(),selected=[];
        for(const pin of pins){
            if(pin.net===S.selected){selected.push(pin);continue;}
            const[x,y]=xy(pin.p.x,pin.p.y),key=Math.floor(x/6)+','+Math.floor(y/6);
            let group=groups.get(key);if(!group){group={x:0,y:0,count:0,net:pin.net,mixed:false,pins:[]};groups.set(key,group);}
            group.x+=x;group.y+=y;group.count++;group.mixed||=group.net!==pin.net;if(group.pins.length<3)group.pins.push(pin);
        }
        const point=(pin,r,alpha)=>{const[x,y]=xy(pin.p.x,pin.p.y);ctx.globalAlpha=alpha;ctx.fillStyle=netColor(pin.net);ctx.strokeStyle='#fff';ctx.lineWidth=.8;ctx.beginPath();ctx.arc(x,y,r,0,2*Math.PI);ctx.fill();ctx.stroke();ctx.globalAlpha=1;S.stats.points++;S.stats.represented++;};
        for(const group of groups.values()){
            if(group.count<3){for(const pin of group.pins)point(pin,2.5,focused?.22:.72);}
            else{
                ctx.globalAlpha=focused?.22:.65;ctx.fillStyle=group.mixed?'#7e8b9b':netColor(group.net);
                ctx.beginPath();ctx.arc(group.x/group.count,group.y/group.count,Math.min(5,2+Math.log10(group.count)),0,2*Math.PI);ctx.fill();ctx.globalAlpha=1;
                S.stats.clusters++;S.stats.represented+=group.count;
            }
        }
        for(const pin of selected)point(pin,4.8,1);
    }
    if(S.view==='routing'&&S.inspect?.kind==='pin'){
        const pin=S.inspect.pin,p=pin.p,v=node(p.node);
        if(v&&layerVisible(p.layer,tier)){
            rect(v.box,null,'#9ca6b2',2);
            line({x:p.x,y:p.y},v,'#73818e',1.3,[4,4]);
            const[x,y]=xy(v.x,v.y);ctx.strokeStyle='#34465c';ctx.fillStyle='#fff';ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(x,y,3.5,0,2*Math.PI);ctx.fill();ctx.stroke();
            const[a,b]=xy(p.x,p.y);ctx.fillStyle=netColor(pin.net);ctx.strokeStyle='#fff';ctx.beginPath();ctx.arc(a,b,5.5,0,2*Math.PI);ctx.fill();ctx.stroke();
            ctx.font='10px "Microsoft YaHei",sans-serif';const label='抽象映射辅助线',tx=Math.max(6,Math.min(w-110,(a+x)/2+10)),ty=Math.max(14,Math.min(h-8,(b+y)/2-10));
            ctx.fillStyle='#ffffffed';ctx.fillRect(tx-3,ty-12,108,16);ctx.fillStyle='#687889';ctx.fillText(label,tx,ty);
        }
    }
    const metadata=S.netMap.get(S.selected),hasPin=(metadata?.pins||[]).some(p=>layerVisible(p.layer,tier));
    const hasRoute=ns.some(n=>(n.edges||[]).some(e=>{const a=node(e[0]),b=node(e[1]);return a&&b&&(layerVisible(a.l,tier)||layerVisible(b.l,tier));})||(n.guides||[]).some(g=>layerVisible(g[4],tier)));
    $(tier+'Empty').hidden=S.view!=='routing'||!focused||hasPin||hasRoute;
    $(tier+'Empty').textContent='当前 Net 在此 Die / 金属层没有 Pin 或路径；背景 Pin 仍可见。';
    rect(d.die,null,'#bbc5d0',1);ctx.fillStyle='#8b97a5';ctx.font='9px Segoe UI';const[a,b]=xy(x1,y1);ctx.fillText(fmt(x1,0)+', '+fmt(y1,0),Math.max(2,a),Math.min(h-4,b+14));
}
function details(){
    if(!S.data)return;
    const n=S.netMap.get(S.selected);$('nettitle').textContent=n?n.net:'全部网络';
    $('netdetail').innerHTML=n?table(['属性','实际值'],[['引脚',n.pins.length],['类型',n.cross_die?'跨 Die':'Die 内'],['Guide 矩形',n.guides.length],['最终真实路径边',n.edges.length],['Via / TSV',fmt(n.via_count)+' / '+fmt(n.tsv_count)],['图边长度 (DBU)',fmt(n.wirelength_dbu_local)],['状态',n.status]]):'<p class="small">总览显示全量端子分布。点击 Pin / 路径或搜索任意 Net 后聚焦；网络下拉分页不限制 Pin 数据。</p>';
    $('iterations').innerHTML=table(['轮次','本地 Overflow','接受','重布 Net'],(S.data.iterations||[]).map(i=>[i.iteration,i.overflow_local,i.iteration===0?'初始':i.accepted?'是':'否，已回滚',i.rerouted_nets??'—']));
}
function sameRoute(a,b){return a&&b&&a.tsv_node===b.tsv_node&&a.edges?.length===b.edges?.length&&a.edges.every((e,i)=>e[0]===b.edges[i][0]&&e[1]===b.edges[i][1]);}
function learning(){
    const key=S.loadId+'|'+S.traceVersion+'|'+S.selected+'|'+S.index;
    if(S.learningKey===key)return S.learningValue;
    const target=S.active.get(S.selected),events=[],attempts=[];let pending=null,committed=null;
    for(const item of S.traceByNet.get(S.selected)||[]){
        if(S.index>=0&&item.index>S.index)break;
        const e=item.event;events.push(e);
        if(e.event==='net_selected'){pending={topologies:[],paths:[],start:e};}
        else if(e.event==='topology'&&pending)pending.topologies.push(e);
        else if(e.event==='path_found'&&pending)pending.paths.push(e);
        else if(e.event==='net_removed'){pending=null;committed=null;}
        else if(e.event==='net_added'){
            if(pending){pending.route=e;attempts.push(pending);committed=pending;pending=null;}
            else committed=[...attempts].reverse().find(a=>sameRoute(a.route,e))||null;
        }
    }
    let chosen;
    if(S.index>=0)chosen=pending||committed;
    else chosen=[...attempts].reverse().find(a=>sameRoute(a.route,target));
    const value={topologies:chosen?.topologies||[],paths:chosen?.paths||[],events,hasPending:!!pending};
    S.learningKey=key;S.learningValue=value;return value;
}
function processPanel(learn){
    const n=S.netMap.get(S.selected);
    if(!n){$('process').innerHTML='<p class="small">选择一个 Net，沿真实事件观察连接拓扑与实际路径。</p>';return;}
    const counts={};for(const e of learn.events)counts[e.event]=(counts[e.event]||0)+1;
    const no='该步骤没有可用过程数据';
    const cards=[
        ['Net Selected','选择网络',counts.net_selected?'Trace · net_selected × '+counts.net_selected:reference()?'参考 Guide 无 Trace':no],
        ['TSV Selection','跨 Die 选址',!n.cross_die?'Die 内 Net · 不适用':'无独立选址事件；仅 Route 记录 TSV 位置，'+no],
        ['MST Topology','端点连接拓扑',learn.topologies.length?'Trace · '+learn.topologies.map(t=>t.tier+': '+t.terminal_nodes.length+' 端点 / '+t.mst_edges.length+' 边').join('；'):no],
        ['A* Search','实际路径搜索',counts.search_started||counts.path_found?'Trace · search_started '+(counts.search_started||0)+' / path_found '+(counts.path_found||0)+'；不含 Open Set 扩展':!reference()?'仅最终 Route；'+no:no],
        ['Route Accepted','提交路径与资源',counts.net_added?'Trace · net_added × '+counts.net_added+'（优化轮次可能回滚）':!reference()?'最终 Route 存在；'+no:no],
        ['Guide Generated','生成 Guide',reference()?'仅官方参考 Guide':S.index>=0?'无独立生成事件；在 net_added 前构建':'最终 Guide 已保存；无独立生成事件']
    ];
    $('process').innerHTML=cards.map(([en,cn,status])=>'<div class="process-step"><small>'+esc(en)+'</small><strong>'+esc(cn)+'</strong><p>'+esc(status)+'</p></div>').join('');
}
function installTrace(events){
    S.trace=events;S.traceByNet=new Map();S.traceVersion=(S.traceVersion||0)+1;S.learningKey=null;S.index=-1;S.ghost=null;S.pending=new Map();
    for(let i=0;i<events.length;i++){const e=events[i];if(e.net){if(!S.traceByNet.has(e.net))S.traceByNet.set(e.net,[]);S.traceByNet.get(e.net).push({event:e,index:i});}}
    $('timeline').max=Math.max(0,events.length-1);$('timeline').value=0;$('timeline').disabled=!events.length;
    for(const id of ['prev','play','next'])$(id).disabled=!events.length;
    $('eventtitle').textContent=events.length?'最终结果 · 可回放真实步骤':S.largeTrace&&S.selected==='all'?'大型 Trace · 请选择单个 Net':'仅最终结果，无过程记录';
    $('eventdetail').textContent=events.length?events.length+' 条真实事件'+(S.data.trace_truncated?' · Trace 截断，过程不完整':''):S.largeTrace&&S.selected==='all'?'按 Net 读取，避免整份大 Trace 加载。':'该记录没有可用 Trace；不重建 MST 或搜索过程。';
    $('step').textContent=events.length?'最终 / '+events.length:'—';$('eventjson').textContent='';
}
async function scopedTrace(){
    if(!S.data)return;const request=++S.traceRequest,data=S.data,selected=S.selected,run=$('run').value;stop();
    S.index=-1;S.active=new Map(data.nets.map(n=>[n.net,n]));S.pending=new Map();S.ghost=null;
    if(reference()){installTrace([]);draw();return;}
    if(S.largeTrace){
        installTrace([]);$('eventtitle').textContent='读取该 Net 的真实 Trace…';draw();
        const tr=await api('/api/trace?id='+encodeURIComponent(run)+(selected==='all'?'':'&net='+encodeURIComponent(selected)));
        if(request!==S.traceRequest||S.data!==data||S.selected!==selected)return;
        installTrace(tr.events);
    }else installTrace(selected==='all'?S.fullTrace:S.fullTrace.filter(e=>!e.net||e.net===selected));
    draw();
}
function applyEvent(e){
    S.ghost=null;
    if(e.event==='net_selected'){S.pending.set(e.net,[]);}
    else if(e.event==='net_added'){S.active.set(e.net,e);S.pending.delete(e.net);S.ghost={net:e.net,edges:e.edges,remove:false};}
    else if(e.event==='net_removed'){const old=S.active.get(e.net);S.ghost={net:e.net,edges:old?.edges,remove:true};S.active.delete(e.net);S.pending.delete(e.net);}
    else if(e.event==='path_found'){
        const edges=[];for(let i=1;i<e.nodes.length;i++)edges.push([e.nodes[i-1],e.nodes[i]]);
        const all=S.pending.get(e.net)||[];for(const edge of edges)all.push(edge);S.pending.set(e.net,all);S.ghost={net:e.net,edges,remove:false};
    }
}
const eventNames={parse_started:'开始解析输入',parse_complete:'输入解析完成',graph_built:'建立 Routing Graph',thermal_mapped:'热数据映射',fixed_demand_built:'固定需求统计',net_selected:'选择 Net',topology:'生成 MST 拓扑',search_started:'开始 A* 路径搜索',path_found:'找到真实路径',net_added:'提交路径与容量',net_removed:'拆除旧路径',iteration_done:'优化轮次完成',routing_complete:'完成布线',trace_truncated:'Trace 达到大小上限'};
function seek(index){
    if(!S.trace.length)return;index=Math.max(0,Math.min(S.trace.length-1,index));
    if(S.index<0||index<S.index){S.active=new Map();S.pending=new Map();S.ghost=null;S.index=-1;}
    for(let i=S.index+1;i<=index;i++)applyEvent(S.trace[i]);S.index=index;
    const e=S.trace[index];$('timeline').value=index;$('eventtitle').textContent=eventNames[e.event]||e.event;
    $('eventdetail').textContent=(e.net||S.data.design)+(e.iteration!==undefined?' · 轮次 '+e.iteration:'')+' · '+e.event+' · seq '+e.seq;
    $('step').textContent=(index+1)+' / '+S.trace.length;$('eventjson').textContent=JSON.stringify(e,null,2);draw();
}
function stop(){if(S.playing)clearInterval(S.playing);S.playing=null;$('play').textContent='播放';}
function play(){if(S.playing){stop();return;}if(!S.trace.length)return;if(S.index<0||S.index===S.trace.length-1)seek(0);$('play').textContent='暂停';S.playing=setInterval(()=>{if(S.index>=S.trace.length-1){stop();return;}seek(S.index+1);},+$('speed').value);}
function finalResult(){stop();if(!S.data)return;S.index=-1;S.active=new Map(S.data.nets.map(n=>[n.net,n]));S.pending=new Map();S.ghost=null;$('eventtitle').textContent='最终真实结果';$('step').textContent='最终 / '+S.trace.length;$('eventjson').textContent='';draw();}
async function raw(){if(S.raw||!S.data)return;const data=S.data,run=$('run').value;try{const result=await api('/api/thermal?id='+encodeURIComponent(run));if(S.data===data){S.raw=result;draw();}}catch(e){if(S.data===data)error(e);}}
function locate(lines,x){if(x<lines[0]||x>lines.at(-1))return -1;let a=0,b=lines.length;while(a<b){const m=(a+b)>>1;if(lines[m]<=x)a=m+1;else b=m;}return Math.min(lines.length-2,a-1);}
function cellAt(x,y,tier){
    const gx=locate(S.data.x_lines,x),gy=locate(S.data.y_lines,y);if(gx<0||gy<0)return null;
    const nx=S.data.x_lines.length-1,ny=S.data.y_lines.length-1;
    const ls=S.data.layers.filter(l=>layerVisible(l.number,tier));
    return {kind:'cell',gx,gy,tier,box:[S.data.x_lines[gx],S.data.y_lines[gy],S.data.x_lines[gx+1],S.data.y_lines[gy+1]],rows:ls.map(l=>{
        const id=(l.number*ny+gy)*nx+gx,r=S.resourceMap.get(id);
        return ['M'+l.number,id,r?.[1]??'N/A',r?.[2]??'N/A',r?.[3]??'N/A',r?.[4]??'N/A'];
    })};
}
function inspectDetails(){
    const obj=S.inspect;if(!obj){$('inspectdetail').innerHTML='<p class="small">点击 Pin、实际路径或 GCell 查看；虚线仅表示抽象映射或 MST 拓扑。</p>';return;}
    if(obj.kind==='pin')$('inspectdetail').innerHTML=table(['Pin → GCell → Node','实际数据'],pinInfo(obj.pin))+'<p class="small">虚线为抽象映射辅助线，不是物理导线。方向未由结果提供，不推断。</p>';
    else if(obj.kind==='cell')$('inspectdetail').innerHTML=table(['GCell','实际数据'],[['索引','('+obj.gx+', '+obj.gy+')'],['Die',obj.tier==='bottom'?'Die 1 · Bottom':'Die 2 · Top'],['边界 (DBU)','['+obj.box.join(', ')+']']])+table(['层','Node ID','Capacity','Usage','Fixed','Overflow'],obj.rows)+'<p class="small">N/A 表示结果未记录该资源，不表示容量为零。此为最终资源快照。</p>';
    else $('inspectdetail').innerHTML=table(['对象','实际数据'],[['类型',obj.kind==='mst'?'MST 抽象拓扑（非金属走线）':obj.kind==='tsv'?'跨 Die TSV':obj.kind==='guide'?'Guide 区域':'实际图边'],['Net',obj.net],['Metal Layer',obj.layers?.map(l=>'M'+l).join(' ↔ ')||'M'+obj.g?.[4]],['Node IDs',obj.edge?.join(' → ')??'N/A · Guide 不含内部边'],['Guide 边界',obj.g?.slice(0,4).join(', ')??'—']]);
}
function choiceList(){
    $('pinchoices').hidden=!S.choices.length;if(!S.choices.length)return;
    const start=S.choicePage*CHOICE_SIZE,items=S.choices.slice(start,start+CHOICE_SIZE);
    $('choiceTitle').textContent='此位置 '+fmt(S.choices.length,0)+' 个重合 / 邻近 Pin · 选择具体端子';
    $('choiceList').innerHTML=items.map((pin,i)=>'<button data-choice="'+(start+i)+'"><i style="background:'+netColor(pin.net)+'"></i><span>'+esc(pin.p.name)+'<small>'+esc(pin.net)+' · M'+pin.p.layer+' · Node '+esc(pin.p.node)+'</small></span></button>').join('');
    $('choicePage').textContent=(start+1)+'–'+Math.min(start+CHOICE_SIZE,S.choices.length)+' / '+S.choices.length;
    $('choicePrev').disabled=S.choicePage===0;$('choiceNext').disabled=start+CHOICE_SIZE>=S.choices.length;
}
function pointDistance(px,py,a,b){const dx=b[0]-a[0],dy=b[1]-a[1],t=Math.max(0,Math.min(1,((px-a[0])*dx+(py-a[1])*dy)/(dx*dx+dy*dy||1)));return Math.hypot(px-a[0]-t*dx,py-a[1]-t*dy);}
function hitAt(tier,px,py){
    const c=$(tier);if(!S.data||S.loading||!c._world||S.view==='raw')return null;
    const [x,y]=c._world(px,py);
    if(S.view==='routing'&&$('pins').checked){
        const radius=10/c._scale,pins=queryPins([x-radius,y-radius,x+radius,y+radius],tier).filter(pin=>{const[a,b]=c._xy(pin.p.x,pin.p.y);return Math.hypot(a-px,b-py)<=10;});
        if(pins.length){pins.sort((a,b)=>(a.net===S.selected?-1:0)-(b.net===S.selected?-1:0)||a.key-b.key);return {kind:'pins',pins};}
    }
    for(let i=c._edges.length-1;i>=0;i--){const hit=c._edges[i];if(pointDistance(px,py,hit.a,hit.b)<5)return hit;}
    for(let i=c._guides.length-1;i>=0;i--){const hit=c._guides[i],g=hit.g;if(x>=g[0]&&x<=g[2]&&y>=g[1]&&y<=g[3])return hit;}
    return cellAt(x,y,tier);
}
function hover(e,tier){
    const c=$(tier),box=c.getBoundingClientRect(),hit=hitAt(tier,e.clientX-box.left,e.clientY-box.top);
    let text='';
    if(hit?.kind==='pins'){
        text=pinInfo(hit.pins[0]).map(([k,v])=>k+': '+v).join('\n');
        if(hit.pins.length>1)text+='\n此位置 '+hit.pins.length+' 个 Pin；点击后可分页选择全部端子。\n'+hit.pins.slice(1,4).map(p=>p.p.name+' · '+p.net+' · M'+p.p.layer).join('\n');
    }else if(hit?.kind==='cell'){
        text='GCell ('+hit.gx+', '+hit.gy+') · '+(tier==='bottom'?'Die 1':'Die 2')+'\n边界 ['+hit.box.join(', ')+']\n'+hit.rows.map(r=>r[0]+' · Node '+r[1]+' · Capacity '+r[2]+' Usage '+r[3]+' Fixed '+r[4]+' Overflow '+r[5]).join('\n');
        if(c._heat){const v=c._heat.values[hit.gy*(S.data.x_lines.length-1)+hit.gx];text+='\n当前统计值 '+fmt(v,5);const info=c._heat.info.get(hit.gy*(S.data.x_lines.length-1)+hit.gx);if(info)text+=' · 代表层 M'+info.l;}
    }else if(hit)text=hit.net+'\n'+(hit.kind==='mst'?'MST 抽象拓扑 · 非金属走线':hit.kind==='guide'?'Guide 区域':'实际图边')+'\n层 '+(hit.layers?.join(' ↔ ')||hit.g[4])+'\nNode '+(hit.edge?.join(' → ')||'N/A');
    const tip=$('tooltip');tip.hidden=!text;tip.textContent=text;
    if(text){const r=tip.getBoundingClientRect(),left=e.clientX+18+r.width<innerWidth?e.clientX+18:e.clientX-r.width-18,top=e.clientY+18+r.height<innerHeight?e.clientY+18:e.clientY-r.height-18;tip.style.left=Math.max(4,left)+'px';tip.style.top=Math.max(4,top)+'px';}
    return hit;
}
async function clickHit(hit){
    if(!hit)return;
    if(hit.kind==='pins'){
        const pins=hit.pins;if(!await selectNet(pins[0].net,{autoFit:false,pin:pins[0]}))return;
        S.choices=pins.length>1?pins:[];S.choicePage=0;choiceList();draw();
    }else if(hit.net){
        if(!await selectNet(hit.net,{autoFit:false}))return;S.inspect=hit;inspectDetails();draw();
    }else{S.selectionVersion++;S.inspect=hit;S.choices=[];choiceList();inspectDetails();draw();}
}
for(const tier of ['bottom','top']){
    const c=$(tier);let drag=null,moved=false;
    c.addEventListener('wheel',e=>{if(S.loading||!S.data)return;e.preventDefault();const prev=S.zoom;S.zoom=Math.max(.3,Math.min(60,S.zoom*Math.exp(-e.deltaY*.001)));const r=c.getBoundingClientRect(),px=e.clientX-r.left-r.width/2,py=e.clientY-r.top-r.height/2;S.panX=px-(px-S.panX)*S.zoom/prev;S.panY=py-(py-S.panY)*S.zoom/prev;$('tooltip').hidden=true;draw();},{passive:false});
    c.addEventListener('pointerdown',e=>{if(S.loading)return;drag={x:e.clientX,y:e.clientY,px:S.panX,py:S.panY};moved=false;c.setPointerCapture(e.pointerId);});
    c.addEventListener('pointerup',e=>{if(drag&&!moved){const r=c.getBoundingClientRect();clickHit(hitAt(tier,e.clientX-r.left,e.clientY-r.top)).catch(error);}drag=null;});
    c.addEventListener('pointercancel',()=>{drag=null;});
    c.addEventListener('pointermove',e=>{if(drag){if(Math.abs(e.clientX-drag.x)+Math.abs(e.clientY-drag.y)>3)moved=true;S.panX=drag.px+e.clientX-drag.x;S.panY=drag.py+e.clientY-drag.y;draw();}else hover(e,tier);});
    c.addEventListener('pointerleave',()=>{$('tooltip').hidden=true;});
}
$('dataset').onchange=setCases;$('case').onchange=setRuns;$('source').onchange=load;$('run').onchange=load;$('reload').onclick=init;
let searchTimer;
$('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{setNets();scopedTrace().catch(error);},120);};
$('crossOnly').onchange=()=>{setNets();scopedTrace().catch(error);};
$('net').onchange=()=>selectNet($('net').value);
$('netPrev').onclick=()=>{S.page--;netOptions();};$('netNext').onclick=()=>{S.page++;netOptions();};
$('choicePrev').onclick=()=>{S.choicePage--;choiceList();};$('choiceNext').onclick=()=>{S.choicePage++;choiceList();};
$('choiceList').onclick=async e=>{const b=e.target.closest('[data-choice]');if(!b)return;const pin=S.choices[+b.dataset.choice],choices=S.choices,page=S.choicePage;if(!await selectNet(pin.net,{autoFit:false,pin}))return;S.choices=choices;S.choicePage=page;choiceList();};
for(const id of ['tier','layer','guides','paths','overviewPaths','mst','pins','tsvs','grid','instances'])$(id).onchange=()=>{S.filterKey=null;draw();};
$('fit').onclick=fit;$('fitNet').onclick=fitNet;$('overview').onclick=()=>selectNet('all',{autoFit:false});$('focus').onclick=fitNet;
$('compare').onchange=compare;$('prev').onclick=()=>{stop();seek(S.index<0?S.trace.length-1:S.index-1);};$('next').onclick=()=>{stop();seek(S.index+1);};$('play').onclick=play;
$('speed').onchange=()=>{if(S.playing){stop();play();}};$('timeline').oninput=()=>{stop();seek(+$('timeline').value);};$('final').onclick=finalResult;
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=async()=>{S.view=b.dataset.view;document.querySelectorAll('[data-view]').forEach(x=>x.classList.toggle('active',x===b));if(S.view==='raw')await raw();draw();});
new ResizeObserver(draw).observe(document.querySelector('.canvases'));init();
