/* Executes production 2D/3D render and picking code with a Canvas/DOM test double.
 * Uses real existing-run API fixtures; this is NOT a live browser visual test.
 */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const fixture=JSON.parse(fs.readFileSync('outputs/reports/v5/v5_ui_render_fixture.json','utf8'));
const calls=[],elements=new Map();
function element(id){
    if(elements.has(id))return elements.get(id);
    const ctx=new Proxy({}, {get:(o,k)=>k in o?o[k]:(...a)=>{calls.push([k,...a]);},set:(o,k,v)=>(o[k]=v,true)});
    const e={id,value:'',checked:false,disabled:false,hidden:false,style:{},dataset:{},textContent:'',innerHTML:'',width:0,height:0,
        classList:{toggle(){}},setAttribute(){},addEventListener(){},getContext:()=>ctx,
        getBoundingClientRect:()=>({left:0,top:0,width:id==='layers3d'?1200:700,height:id==='layers3d'?850:600})};
    elements.set(id,e);return e;
}
const sandbox={console,performance,Map,Set,Float64Array,Number,Math,JSON,URL,AbortController,DOMException,setTimeout,clearTimeout,
    document:{getElementById:element,querySelector:element,querySelectorAll:()=>[]},window:{},devicePixelRatio:1,
    ResizeObserver:class{observe(){}},requestAnimationFrame:()=>1};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('ui/static/app.js','utf8').replace(/;init\(\);\s*$/,';'),sandbox);
vm.runInContext(fs.readFileSync('ui/static/layers3d.js','utf8'),sandbox);
vm.runInContext('globalThis.test={S,buildIndex,setNets,render,fitNet,pinInfo,inspectDetails,filteredPins,cellAt,node,netColor};',sandbox);
const t=sandbox.test;
async function install(summary,net){
    t.S.data={...summary,nets:[net]};t.S.selected=net.net;t.S.active=new Map([[net.net,net]]);t.S.remote=null;t.S.dimension='2d';t.S.zoom=1;t.S.panX=t.S.panY=0;t.S.inspect=null;t.S.learningKey=null;
    element('tier').value='both';element('layer').value='all';element('search').value='';
    for(const id of ['guides','paths','pins','tsvs','grid','pinRects','labels3d'])element(id).checked=true;
    await t.buildIndex();t.setNets();t.S.selected=net.net;t.render();
}
(async()=>{
    const n=fixture.v5_net;await install(fixture.v5_summary,n);
    assert.ok(element('bottom')._guides.length&&element('top')._guides.length);
    assert.equal(element('bottom')._edges.some(x=>x.kind==='path'),false);
    assert.ok(element('bottom')._edges.some(x=>x.kind==='guide_tsv'));
    assert.ok(element('bottom')._guides.some(x=>x.kind==='pin_rect'));
    t.fitNet();t.render();
    for(const g of n.guides){const c=element(g[4]<=10?'bottom':'top');for(const p of [[g[0],g[1]],[g[2],g[3]]]){const[x,y]=c._xy(...p);assert.ok(x>=-1&&x<=701&&y>=-1&&y<=601,'fit includes Guide bounds');}}
    const pin=t.S.allPins[0];t.S.inspect={kind:'pin',pin};t.inspectDetails();
    assert.ok(element('inspectdetail').innerHTML.includes(pin.p.box.join(', ')));
    assert.ok(element('inspectdetail').innerHTML.includes('Router 实际接入点'));
    element('estimatedPaths').checked=true;t.render();
    assert.ok(element('bottom')._edges.some(x=>x.kind==='estimate'));
    assert.equal(n.edges.length,0);assert.ok(element('legend').innerHTML.includes('非 Router 真实 A*'));
    t.S.allPins.push({...pin,net:'background'});t.S.allowed.add('background');element('onlyNetPins').checked=true;t.S.filterKey=null;
    assert.ok(t.filteredPins().every(p=>p.net===n.net));
    t.S.remote={cells:new Map([['0,0',[]]]),cellMetadata:new Map([['0,0',{coverage:[{layer:1,rectangles:3,nets:2}]}]])};
    const d=t.S.data;t.S.inspect=t.cellAt((d.x_lines[0]+d.x_lines[1])/2,(d.y_lines[0]+d.y_lines[1])/2,'bottom');t.inspectDetails();
    assert.ok(element('inspectdetail').innerHTML.includes('3 RECT / 2 Net'));
    assert.ok(element('inspectdetail').innerHTML.includes('非 Usage'));t.S.remote=null;
    const viewer=new sandbox.window.LayerView3D(element('layers3d'),{requestDraw(){},pickPin(){},pickObject(){},hover:element('hover')});
    const state=()=>({data:t.S.data,pins:t.filteredPins(),active:t.S.active,allowed:t.S.allowed,nets:[t.S.netMap.get(t.S.selected)],selected:t.S.selected,index:-1,tier:'both',layer:'all',paths:false,overviewPaths:false,tsvs:true,guides:true,estimatedPaths:true,pinRects:true,grid:true,showPins:true,reference:false,ghost:null,node:t.node,cellAt:t.cellAt,color:t.netColor});
    const s=state();viewer.render(s,true);
    assert.equal(viewer.scene.planes.length,20);assert.equal(viewer.scene.paths.length,0);assert.equal(viewer.scene.vias.length,0);assert.equal(viewer.scene.tsvs.length,1);
    assert.ok(viewer.scene.estimates.length>0&&viewer.scene.pinRects.length>0);
    const link=viewer.projectedLinks.find(x=>x.kind==='guide_tsv');assert.ok(link);
    viewer.projectedPins=[];viewer.projectedLinks=[link];
    assert.equal(viewer.hit({clientX:(link.pa[0]+link.pb[0])/2,clientY:(link.pa[1]+link.pb[1])/2}).kind,'guide_tsv');
    viewer.render({...s,layer:'5'});assert.equal(viewer.scene.planes.length,1);assert.equal(viewer.scene.tsvs.length,0);
    const ambiguous={...n,tsv_markers:[]};viewer.render({...s,nets:[ambiguous],active:new Map([[n.net,ambiguous]])});
    assert.equal(viewer.scene.tsvs.length,0);assert.equal(viewer.scene.guides.filter(g=>g.kind==='tsv_candidate').length,n.tsv_candidates.length*2);
    await install(fixture.legacy_summary,fixture.legacy_net);t.render();
    assert.ok(element('bottom')._edges.some(x=>x.kind==='path'));
    assert.equal(element('bottom')._edges.some(x=>x.kind==='estimate'),false);
    viewer.render({...state(),paths:true,estimatedPaths:false},true);assert.ok(viewer.scene.paths.length>0);
    const report={status:'PASS',kind:'production renderer with DOM/Canvas test doubles; no live browser',v5:{net:n.net,guides:n.guides.length,pin_rects:n.pins.length,independent_pins:n.terminal_count,estimates:n.estimated_paths.segments.length,tsvs:n.tsv_markers.length},v45:{net:fixture.legacy_net.net,real_edges:fixture.legacy_net.edges.length},checks:['2D Guide and Pin RECT','fit includes Guide bounds','Pin RECT inspection','independent estimated path switch','current Net Pin filter','GCell coverage and N/A','20-layer 3D','TSV picking','single-layer filter','candidate TSV without cross-die edge','V4.5 true path compatibility'],canvas_calls:calls.length};
    fs.writeFileSync('outputs/reports/v5/v5_ui_render_checks.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
})().catch(e=>{console.error(e);process.exitCode=1;});
