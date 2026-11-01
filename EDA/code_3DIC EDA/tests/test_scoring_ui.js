/* Real rendering module, synthetic API and DOM. No router/scorer execution. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:'',classList:{toggle(){},add(){},remove(){}}});return elements.get(id);};
element('run').value='case/label';element('source').value='own';element('scorer').value='reference_20261029';
let response,requests=[];
const context={window:{},document:{getElementById:element},URLSearchParams,
 esc:x=>String(x??'').replaceAll('<','&lt;'),fmt:x=>x==null?'N/A':String(x),
 table:(h,rows)=>JSON.stringify({h,rows}),api:async url=>{requests.push(url);return typeof response==='function'?response():response;}};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../ui/static/scoring.js'),'utf8'),context);
const ui=context.window.ScoringUI;
const record={label:'v3_5',router_status:'ROUTED',router_recorded_status:'ROUTED',router_complete:true,
 scorer_id:'REFERENCE_20261029',status:'REFERENCE_VALID',source:'own',identity_warnings:[],metrics:{overflow:19},
 by_layer:{1:{overflow:7}},files:{},report:{},errors:[],error_counts:{}};
const payload=()=>({a:structuredClone(record),b:null,comparison:{compatible:false,reasons:['未选择 B']}});
(async()=>{
 response=payload();await ui.load();
 assert.match(element('metrics').innerHTML,/Reference 20261029 Overflow/);
 assert.match(element('comparetable').innerHTML,/N\/A — 最新官方公式或参数尚未公开/);
 assert.match(requests.at(-1),/scorer=reference_20261029/);
 element('scorer').value='official_original';response=payload();response.a.scorer_id='OFFICIAL_ORIGINAL';response.a.status='PASS';response.a.metrics={auxiliary_quality:72,final_total_score:null};
 await ui.load();assert.match(element('comparetable').innerHTML,/单 Case 辅助加权质量/);
 const row=JSON.parse(element('comparetable').innerHTML).rows.find(r=>r[0]==='比赛最终总分');assert.equal(row[1],'N/A');
 assert.match(element('metrics').innerHTML,/Official Original Overflow/);
 element('source').value='reference';response=payload();response.a.source='reference';response.a.status='NOT_EVALUATED';response.a.metrics={};await ui.load();
 assert.match(element('scoringMessage').textContent,/不沿用|不是评分器/);assert.match(element('badge').textContent,/未评分/);
 // A late response for an old selection must never overwrite the new one.
 let resolve;response=()=>new Promise(r=>resolve=r);const old=ui.load();
 response=payload();response.a.label='new';await ui.load();resolve({...payload(),a:{...record,label:'old'}});await old;
 assert.match(element('comparetable').innerHTML,/A · new/);assert.doesNotMatch(element('comparetable').innerHTML,/A · old/);
 response=()=>Promise.reject(Error('SHA256 mismatch'));await ui.load();
 assert.match(element('scoringMessage').textContent,/SHA256 mismatch/);assert.equal(element('metrics').textContent,'');
 assert.equal(element('badge').textContent,'SCORING ERROR');
 console.log('Scoring UI: method switching, N/A, auxiliary score, Source isolation, race and failure isolation passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
