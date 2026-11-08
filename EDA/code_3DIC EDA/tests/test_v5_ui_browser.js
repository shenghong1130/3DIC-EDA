/* Real offline UI regression, Node 22+ and an isolated Chrome CDP session.
 * Start ui/app.py on a test port and Chrome with --headless=new,
 * --remote-debugging-port and a temporary --user-data-dir, then:
 * node tests/test_v5_ui_browser.js http://127.0.0.1:8502 http://127.0.0.1:9338
 * Never runs a Router or scorer. Optional third argument: screenshot file.
 */
const assert=require('node:assert/strict'),fs=require('node:fs');
const [base='http://127.0.0.1:8502',cdp='http://127.0.0.1:9338',screenshot]=process.argv.slice(2);
const pause=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
    const target=await (await fetch(cdp+'/json/new?about:blank',{method:'PUT'})).json();
    const ws=new WebSocket(target.webSocketDebuggerUrl),pending=new Map(),errors=[];let sequence=0;
    await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
    ws.onmessage=event=>{const message=JSON.parse(event.data);if(message.id){const job=pending.get(message.id);pending.delete(message.id);message.error?job.reject(Error(JSON.stringify(message.error))):job.resolve(message.result);}else if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails);};
    const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
    const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
    const wait=async expression=>{for(let i=0;i<150;i++){if(await evaluate(expression))return;await pause(200);}throw Error('Timed out: '+expression);};
    const load=async(caseId,label)=>{
        await evaluate(`(async()=>{const c=S.catalog.cases.find(c=>c.id===${JSON.stringify(caseId)});$('dataset').value=c.dataset;setCases();$('case').value=c.id;setRuns();$('run').value=c.id+'/'+${JSON.stringify(label)};$('source').value='own';await load();})()`);
        await wait(`!S.loading&&S.data?.run.label===${JSON.stringify(label)}&&!$('error').textContent`);
    };
    try{
        await send('Runtime.enable');await send('Page.enable');
        await send('Emulation.setDeviceMetricsOverride',{width:1600,height:1100,deviceScaleFactor:1,mobile:false});
        await send('Page.navigate',{url:base});await wait('typeof S!=="undefined"&&!!S.catalog');
        await load('open_3d_case001','case001_v5');
        assert.equal(await evaluate('S.data.metrics.nets'),3152);
        assert.equal(await evaluate('S.data.metrics.guide_tsv_count'),657);
        assert.equal(await evaluate("$('paths').disabled&&$('guides').checked&&!$('tsvs').disabled"),true);
        await evaluate("(async()=>{const r=await S.remote.get('nets',{cross:'1',limit:1});await selectNet(r.items[0].net,{autoFit:false});})()");
        await wait("$('bottom')._guides.length>0&&$('top')._guides.length>0");
        assert.equal(await evaluate("S.data.nets[0].edges.length"),0);
        assert.equal(await evaluate("$('bottom')._edges.some(e=>e.kind==='guide_tsv')"),true);
        assert.equal(await evaluate("$('netdetail').textContent.includes('N/A')"),true);
        await evaluate("setDimension('3d')");await wait('!!S.layerView3d?.scene?.guides.length');
        const scene=await evaluate('({layers:S.layerView3d.scene.planes.length,guides:S.layerView3d.scene.guides.length,tsvs:S.layerView3d.scene.tsvs.length,paths:S.layerView3d.scene.paths.length,vias:S.layerView3d.scene.vias.length,net:S.selected})');
        assert.equal(scene.layers,20);assert.equal(scene.tsvs,1);assert.equal(scene.paths,0);assert.equal(scene.vias,0);
        assert.equal(await evaluate("$('layers3dStats').textContent.includes('N/A')"),true);
        if(screenshot){const image=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(screenshot,Buffer.from(image.data,'base64'));}
        await evaluate("$('layer').value='5';$('layer').onchange()");await wait('S.layerView3d.scene.planes.length===1');
        assert.equal(await evaluate('S.layerView3d.scene.planes[0].layer'),5);assert.equal(await evaluate('S.layerView3d.scene.tsvs.length'),0);
        await evaluate("$('layer').value='all';$('tsvs').checked=false;$('guides').checked=false;draw()");await wait('S.layerView3d.scene.guides.length===0');
        assert.equal(await evaluate('S.layerView3d.scene.tsvs.length'),0);
        await evaluate("$('tsvs').checked=true;$('guides').checked=true;setDimension('2d')");
        const old=await evaluate("S.catalog.runs.find(r=>r.case==='toy'&&r.complete&&r.result_bytes&&!r.indexed)");assert.ok(old,'Existing small C++ run required');
        await load('toy',old.label);assert.equal(await evaluate('guideOnly()'),false);assert.equal(await evaluate("$('paths').disabled"),false);
        assert.ok(await evaluate('S.data.nets.some(n=>n.edges.length>0)'));
        await load('toy','toy_v5');assert.equal(await evaluate('S.data.metrics.nets'),203);
        await load('open_3d_case001','case001_v5');assert.equal(await evaluate('S.data.metrics.nets'),3152);
        await wait("$('badge').textContent.includes('REFERENCE_INVALID')");
        // case001 has no official reference Guide; toy does.
        await load('toy','toy_v5');
        await evaluate("(async()=>{$('source').value='reference';await load();})()");
        await wait("!S.loading&&S.data?.source==='official_reference_guide'");
        assert.equal(await evaluate('S.data.capabilities.guide_tsv'),false);
        assert.equal(await evaluate("$('tsvs').disabled"),true);
        await evaluate("(async()=>{$('source').value='own';await load();})()");
        await wait("!S.loading&&S.data?.source==='v5_guide_readonly'");
        await load('open_3d_case001','case001_v5');
        // Bad record must clear previous geometry and metrics; retry restores V5.
        await evaluate("(async()=>{const option=new Option('missing','missing/run');$('run').add(option);$('run').value='missing/run';await load();})()");
        assert.equal(await evaluate("S.data===null&&$('localmetrics').textContent===''&&$('error').textContent.includes('HTTP')"),true);
        await evaluate("(async()=>{$('run').value='open_3d_case001/case001_v5';await $('retryLoad').onclick();})()");
        await wait("!S.loading&&S.data?.metrics.nets===3152&&!$('error').textContent");
        // A delayed previous request cannot overwrite the final selection.
        await evaluate("(async()=>{$('run').value='missing/run';const stale=load();$('run').value='open_3d_case001/case001_v5';await load();await stale;})()");
        assert.equal(await evaluate('S.data.metrics.nets'),3152);assert.equal(await evaluate("$('error').textContent"),'');
        assert.deepEqual(errors,[]);
        console.log(JSON.stringify({status:'PASS',case001_nets:3152,guide_tsvs:657,focused_3d:scene,legacy_run:old.id,checks:['2D Guides/TSV','20-layer 3D','layer/toggle filters','C++ compatibility','V5 toy/case001 switching','HTTP failure clear/retry','stale request cancellation'],browser_errors:errors},null,2));
    }finally{await send('Page.close').catch(()=>{});ws.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
