/* Real headless Chrome validation against production API and rendering. */
const assert=require('node:assert/strict'),fs=require('node:fs');
const fixture=JSON.parse(fs.readFileSync('outputs/reports/v5_trace/ui_fixture.json','utf8'));
const [base='http://127.0.0.1:8502',cdp='http://127.0.0.1:9349']=process.argv.slice(2);
const pause=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const target=await(await fetch(cdp+'/json/new?about:blank',{method:'PUT'})).json();
 const ws=new WebSocket(target.webSocketDebuggerUrl),pending=new Map(),errors=[];let sequence=0;
 await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
 ws.onmessage=event=>{const m=JSON.parse(event.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);};
 const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
 const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
 const wait=async expression=>{for(let i=0;i<150;i++){if(await evaluate(expression))return;await pause(200);}throw Error('Timeout: '+expression+' '+await evaluate("$('error').textContent"));};
 const load=async(label,caseId='toy')=>{
  await evaluate(`(async()=>{const c=S.catalog.cases.find(c=>c.id===${JSON.stringify(caseId)});$('dataset').value=c.dataset;setCases();$('case').value=c.id;setRuns();$('run').value=c.id+'/'+${JSON.stringify(label)};$('source').value='own';await load();})()`);
  await wait(`!S.loading&&S.data?.run.label===${JSON.stringify(label)}&&!$('error').textContent`);
 };
 try{
  await send('Runtime.enable');await send('Page.enable');await send('Emulation.setDeviceMetricsOverride',{width:1600,height:1200,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:base});await wait('typeof S!=="undefined"&&!!S.catalog');
  await load(fixture.label);
  await evaluate(`selectNet(${JSON.stringify(fixture.net.net)},{autoFit:true})`);
  await wait("S.data.nets[0]?.route_edges_available&&$('top')._edges.concat($('bottom')._edges).some(e=>e.kind==='path')");
  assert.equal(await evaluate("$('paths').disabled"),false);
  assert.equal(await evaluate("$('netdetail').textContent.includes('FINAL_ACCEPTED_VERIFIED')"),true);
  const paths2d=await evaluate("$('top')._edges.concat($('bottom')._edges).filter(e=>e.kind==='path').length");
  const replay=await evaluate("(()=>{const i=S.trace.findIndex(e=>e.a||e.node);if(i<0)return null;seek(i);return {provenance:S.traceReplay.provenance,source:S.traceReplay.source,edges:S.active.get(S.selected)?.edges.length};})()");
  assert.ok(replay?.source);assert.equal(replay.provenance,'INTERMEDIATE_ONLY');assert.equal(replay.edges,fixture.net.edges.length);
  await evaluate('finalResult()');
  let shot=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync('outputs/reports/v5_trace/ui_2d.png',Buffer.from(shot.data,'base64'));
  await evaluate("setDimension('3d')");await wait('S.layerView3d?.scene?.paths.length>0');
  const scene=await evaluate('({paths:S.layerView3d.scene.paths.length,vias:S.layerView3d.scene.vias.length,tsvs:S.layerView3d.scene.tsvs.length,layers:S.layerView3d.scene.planes.length,invalid:S.layerView3d.scene.invalidEdges})');
  assert.equal(scene.layers,20);assert.equal(scene.invalid,0);assert.ok(scene.vias>0);
  if(fixture.net.tsv_count)assert.ok(scene.tsvs>0);
  shot=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync('outputs/reports/v5_trace/ui_3d.png',Buffer.from(shot.data,'base64'));
  await evaluate("$('guides').checked=false;draw()");await wait('S.layerView3d.scene.guides.length===0');assert.ok(await evaluate('S.layerView3d.scene.paths.length>0'));
  await evaluate("$('paths').checked=false;draw()");await wait('S.layerView3d.scene.paths.length===0&&S.layerView3d.scene.vias.length===0');
  await evaluate("$('paths').checked=true;$('layer').value='5';draw()");await wait('S.layerView3d.scene.planes.length===1');assert.equal(await evaluate('S.layerView3d.scene.planes[0].layer'),5);
  await evaluate("$('layer').value='all';$('guides').checked=true;setDimension('2d')");
  if(fixture.lost){await evaluate(`selectNet(${JSON.stringify(fixture.lost.net)},{autoFit:true})`);await wait("S.data.nets[0]?.path_provenance==='PATH_PROVENANCE_LOST'");assert.equal(await evaluate('S.data.nets[0].edges.length'),0);}
  await load('toy_v5');assert.equal(await evaluate('guideOnly()'),true);assert.equal(await evaluate("$('paths').disabled"),true);
  const old=await evaluate("S.catalog.runs.find(r=>r.complete&&/v4_5/.test(r.label))");
  assert.ok(old,'Existing V4.5 display fixture required');await load(old.label,old.case);assert.equal(await evaluate('guideOnly()'),false);assert.equal(await evaluate("$('paths').disabled"),false);
  assert.deepEqual(errors,[]);
  const result={status:'PASS',label:fixture.label,net:fixture.net.net,paths2d,scene3d:scene,replay,guide_toggle:true,layer_filter:true,original_v5_unchanged:true,v45_checked:old.id,exceptions:errors};
  fs.writeFileSync('outputs/reports/v5_trace/ui_browser_result.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await send('Page.close').catch(()=>{});ws.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
