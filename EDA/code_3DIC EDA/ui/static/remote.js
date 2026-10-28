"use strict";
// Adapter for large immutable results. Existing drawing/replay consumes only
// the currently selected Net and bounded, explicitly labelled overview data.
window.RemoteResult=class RemoteResult{
    constructor(data,run,signal){this.data=data;this.run=run;this.signal=signal;this.total=0;this.background=[];this.pinKey='';this.netFilterKey='';this.filtered=0;this.tiles={};this.cells=new Map();this.pageRequest=0;this.viewRequest=0;this.wanted='';this.timer=0;this.geometryKey='';this.choices=null;}
    live(){return S.remote===this&&S.data===this.data&&!this.signal.aborted;}
    query(extra={}){return {id:this.run,version:this.data.version,source:this.data.source==='official_reference_guide'?'reference':'own',q:$('search').value,cross:$('crossOnly').checked?'1':'0',tier:$('tier').value,layer:$('layer').value,...extra};}
    url(name,extra={}){return '/api/'+name+'?'+new URLSearchParams(this.query(extra));}
    async get(name,extra={},signal=this.signal){return api(this.url(name,extra),{signal});}
    async start(){await this.search(false,false);await this.refresh(this.key());}
    async search(reset=true,refresh=true){
        const request=++this.pageRequest;this.pageAbort?.abort();this.pageAbort=new AbortController();
        const filterKey=JSON.stringify([$('search').value,$('crossOnly').checked]),changed=filterKey!==this.netFilterKey;
        if(reset||changed)S.page=0;
        if(changed){this.background=[];this.pinKey='';if(S.selected==='all'){S.data.nets=[];S.netMap=new Map();S.active=new Map();}this.installPins();draw();}
        setLoadStatus('正在服务端查询全部网络 / 分页…');
        const result=await this.get('nets',{offset:S.page*PAGE_SIZE,limit:PAGE_SIZE},AbortSignal.any([this.signal,this.pageAbort.signal]));if(!this.live()||request!==this.pageRequest)return;
        this.total=result.total;this.netFilterKey=filterKey;S.matches=result.items;netOptions();
        const n=S.netMap.get(S.selected),q=$('search').value.toLowerCase();
        if(S.selected!=='all'&&(!S.selected.toLowerCase().includes(q)||$('crossOnly').checked&&!n?.cross_die))await selectNet('all',{autoFit:false});
        if(changed){this.geometryKey='';this.wanted='';if(refresh)this.schedule();}else setLoadStatus('网络列表已更新 · '+fmt(this.total,0)+' Net · 第 '+(S.page+1)+' 页');details();draw();
    }
    async prepare(name,version){
        this.choices=null;S.choices=[];this.background=[];this.pinKey='';S.allPins=[];S.active=new Map();S.pending=new Map();S.netMap=new Map();S.data.nets=[];S.selected=name;S.ghost=null;S.index=-1;installTrace([]);this.installPins();draw();
        if(name==='all'){this.geometryKey='';this.wanted='';return true;}
        setLoadStatus('正在读取 Net '+name+' 的完整 Pin / Route / Guide…');
        const n=await this.get('net',{net:name});if(!this.live()||version!==S.selectionVersion)return false;
        S.data.nets=[n];S.netMap.set(n.net,n);S.active=new Map([[n.net,n]]);this.installPins();this.wanted='';setLoadStatus('已读取 '+name+' · '+fmt(n.pins.length,0)+' Pin · '+fmt(n.edges.length,0)+' 真实边');return true;
    }
    installPins(){
        const n=S.netMap.get(S.selected),tier=$('tier').value,layer=$('layer').value;
        const pins=(n?.pins||[]).filter(p=>(tier==='both'||(tier==='bottom'?p.layer<=10:p.layer>=11))&&(layer==='all'||p.layer===+layer)).map((p,i)=>({p,net:n.net,cross:!!n.cross_die,key:'selected:'+i}));
        S.allPins=[...this.background,...pins];S.pinIndex=new Map();const[x1,y1,x2,y2]=S.data.die;
        for(const pin of S.allPins){const bx=Math.max(0,Math.min(PIN_BUCKETS-1,Math.floor((pin.p.x-x1)/(x2-x1)*PIN_BUCKETS))),by=Math.max(0,Math.min(PIN_BUCKETS-1,Math.floor((pin.p.y-y1)/(y2-y1)*PIN_BUCKETS))),k=by*PIN_BUCKETS+bx;if(!S.pinIndex.has(k))S.pinIndex.set(k,[]);S.pinIndex.get(k).push(pin);}
        S.allowed=new Set([...S.allPins.map(p=>p.net),...S.data.nets.map(n=>n.net),...S.matches.map(n=>n.net)]);S.filterKey=null;
    }
    box(){
        const die=S.data.die;
        if(S.dimension==='3d')return S.layerView3d?.viewportBox(S.data)||die;
        const c=$($('tier').value==='top'?'top':'bottom'),r=c.getBoundingClientRect();if(!r.width||!r.height)return die;
        const[x1,y1,x2,y2]=die,scale=Math.min((r.width-48)/(x2-x1),(r.height-48)/(y2-y1))*S.zoom,cx=r.width/2+S.panX,cy=r.height/2+S.panY;
        return [Math.max(x1,-cx/scale+(x1+x2)/2),Math.max(y1,(cy-r.height)/scale+(y1+y2)/2),Math.min(x2,(r.width-cx)/scale+(x1+x2)/2),Math.min(y2,cy/scale+(y1+y2)/2)];
    }
    key(){return JSON.stringify([this.query(),this.box().map(n=>Math.round(n)),S.selected,S.view,S.dimension,$('paths').checked,$('overviewPaths').checked,$('guides').checked,$('tsvs').checked,$('instances').checked]);}
    schedule(){
        if(!this.live()||S.loading)return;const key=this.key();if(key===this.wanted)return;this.wanted=key;clearTimeout(this.timer);this.viewAbort?.abort();
        this.timer=setTimeout(()=>this.refresh(key).catch(e=>{if(this.live()&&e.name!=='AbortError'){error(e);this.wanted='';}}),140);
    }
    async refresh(key){
        if(!this.live())return;const request=++this.viewRequest;this.viewAbort?.abort();this.viewAbort=new AbortController();const signal=AbortSignal.any([this.signal,this.viewAbort.signal]);this.wanted=key;
        const box=this.box(),extra={box:box.join(','),exclude:S.selected==='all'?'':S.selected};
        const geometryKey=JSON.stringify([this.query(),S.selected,$('paths').checked,$('overviewPaths').checked,$('guides').checked,$('tsvs').checked]);
        const pinKey=JSON.stringify(this.query(extra)),work=[pinKey===this.pinKey?Promise.resolve({items:this.background,total:this.filtered}):this.get('pins',extra,signal)];
        const tileTiers=S.dimension==='2d'&&['thermal','density','congestion'].includes(S.view)?($('tier').value==='both'?['bottom','top']:[$('tier').value]):[];
        for(const tier of tileTiers)work.push(this.get('tiles',{box:extra.box,tier,view:S.view},signal));
        const geometry=S.selected==='all'&&geometryKey!==this.geometryKey;
        setLoadStatus(S.view==='raw'&&!S.raw?'正在读取原始温度节点 / 构建抽样缓存…':'正在更新当前视口的 Pin 聚合与显示数据…');
        if(geometry)work.push(($('guides').checked||$('paths').checked&&$('overviewPaths').checked||$('tsvs').checked)?this.get('geometry',{full:$('guides').checked||$('paths').checked&&$('overviewPaths').checked?'1':'0'},signal):Promise.resolve({nets:[]}));
        if($('instances').checked&&S.dimension==='2d')work.push(this.get('instances',{box:extra.box},signal));
        const results=await Promise.all(work);if(!this.live()||request!==this.viewRequest||key!==this.wanted)return;
        const pins=results.shift();this.background=pins.items;this.pinKey=pinKey;this.filtered=pins.total;this.tiles={};for(const tier of tileTiers)this.tiles[tier]=results.shift();
        if(geometry){const g=results.shift();S.data.nets=g.nets;S.netMap=new Map(g.nets.map(n=>[n.net,n]));S.active=new Map(g.nets.map(n=>[n.net,n]));this.geometryKey=geometryKey;}
        S.data.instances=$('instances').checked&&S.dimension==='2d'?results.shift().items:[];
        this.installPins();setLoadStatus(S.view==='raw'&&!S.raw?'正在读取原始温度节点；首次构建抽样缓存…':'按需加载 · '+fmt(this.total,0)+' 个符合条件 Net · 当前视口 '+fmt(this.filtered,0)+' 个背景 Pin / '+fmt(this.background.length,0)+' 个显示点；总览几何上限 150 Net / 4 MiB。');draw();
    }
    async cell(gx,gy){
        const key=gx+','+gy;if(this.cells.has(key))return this.cells.get(key);
        this.pendingCells??=new Map();if(this.pendingCells.has(key))return this.pendingCells.get(key);
        if(this.pendingCells.size>=8)return null;
        const promise=this.readCell(gx,gy);this.pendingCells.set(key,promise);
        try{return await promise;}finally{this.pendingCells.delete(key);}
    }
    async readCell(gx,gy){
        const key=gx+','+gy;
        const r=await this.get('cell',{gx,gy});if(!this.live())return null;
        this.cells.set(key,r.resources);if(this.cells.size>128)this.cells.delete(this.cells.keys().next().value);
        for(const row of r.resources)S.resourceMap.set(row[0],row);if(S.resourceMap.size>2560){S.resourceMap=new Map();for(const rows of this.cells.values())for(const row of rows)S.resourceMap.set(row[0],row);}
        return r.resources;
    }
    async openPins(pin){
        const query=this.query({box:pin.box.join(','),layer:pin.p.layer,exclude:S.selected==='all'?'':S.selected,detail:'1',cluster:'1'});this.choices={query,offset:0};await this.pinPage(0);
    }
    async pinPage(offset){
        const state=this.choices;if(!state)return;const r=await api('/api/pins?'+new URLSearchParams({...state.query,offset}),{signal:this.signal});if(!this.live()||this.choices!==state)return;
        Object.assign(state,{offset,total:r.total,items:r.items});S.choices=r.items;this.showChoices();
    }
    showChoices(){
        const s=this.choices;if(!s?.items)return;
        $('pinchoices').hidden=false;$('choiceTitle').textContent='聚合区域 '+fmt(s.total,0)+' 个真实 Pin · 服务端分页';
        $('choiceList').innerHTML=s.items.map((pin,i)=>'<button data-choice="'+i+'"><i style="background:'+netColor(pin.net)+'"></i><span>'+esc(pin.p.name)+'<small>'+esc(pin.net)+' · M'+pin.p.layer+' · Node '+esc(pin.p.node)+'</small></span></button>').join('');
        $('choicePage').textContent=(s.offset+1)+'–'+Math.min(s.offset+20,s.total)+' / '+fmt(s.total,0);$('choicePrev').disabled=s.offset===0;$('choiceNext').disabled=s.offset+20>=s.total;
    }
};
