/* Scoring is independent of Source, geometry indexes, Trace and 3D rendering. */
"use strict";
window.ScoringUI = (() => {
    let sequence = 0;
    const el = id => document.getElementById(id);
    const names = {reference_20261103:'20261103 参考评分',reference_20261029:'20261029 参考评分',official_original:'原版官方评分'};
    const unknown = 'N/A — 最新官方公式或参数尚未公开';
    const method = () => el('scorer').value;
    function clear(){sequence++;for(const id of ['metrics','scoringMessage','layerScores','scoreErrors','scoreFiles','compareNotice','comparelayers','comparetable','report'])el(id).textContent='';}
    const value = (record,key) => record?.metrics?.[key] ?? null;
    const memory = v => v == null ? null : v / 1048576;
    function rows(a){
        const reference = a.scorer_id.startsWith('REFERENCE_');
        return [['评分器',a.scorer_id],['Router 版本（SHA256 识别）',a.router_version],['Router 状态',a.router_status],['评分合法性',a.status],
            ['Wirelength (µm)',value(a,'wirelength_um')],
            [reference?a.scorer_id.replace('REFERENCE_','Reference ')+' Overflow':'Official Original Overflow',value(a,'overflow')],
            ['Congestion Potential',value(a,'congestion_potential')],['Thermal Correlation',value(a,'thermal_correlation')],
            ['TSV Pin/OBS 冲突',value(a,'tsv_conflicts')],['Guide 错误',value(a,'guide_errors')],['连通性错误',value(a,'connectivity_errors')],
            ['Router Runtime (s)',a.router_runtime_seconds],['Router Peak RSS (MiB)',memory(a.router_peak_rss_bytes)],
            ['评分 Runtime (s)',a.runtime_seconds],['评分 Peak RSS (MiB)',memory(a.peak_rss_bytes)],
            ['Wirelength Score',reference?unknown:value(a,'wirelength_score')],
            ['Congestion Score',reference?unknown:value(a,'congestion_score')],
            ['Thermal Score',reference?unknown:value(a,'thermal_score')],
            ...(!reference?[['原版报告单 Case 分数',value(a,'case_score')],['单 Case 辅助加权质量（45/30/25）',value(a,'auxiliary_quality')]]:[]),
            ['比赛最终总分',reference?unknown:value(a,'final_total_score')]];
    }
    function cards(a,data){
        const reference=a.scorer_id.startsWith('REFERENCE_');
        const items=[['Net 总数',data?.metrics?.nets??a.report?.nets,'实际结果 / 报告'],
            ['Router 状态',a.router_status,'原记录：'+a.router_recorded_status],
            ['评分合法性',a.status,names[method()]],['Wirelength',value(a,'wirelength_um'),'µm · 所选评分器'],
            [reference?a.scorer_id.replace('REFERENCE_','Reference ')+' Overflow':'Official Original Overflow',value(a,'overflow'),'所选评分器，非 Router Local Overflow'],
            ['Congestion Potential',value(a,'congestion_potential'),'正容量归一化过载平方和'],
            ['Thermal Correlation',value(a,'thermal_correlation'),'所选评分器 Pearson']];
        el('metrics').innerHTML=items.map(([k,v,hint])=>`<div class="metric${typeof v==='string'?' text-metric':''}"><small>${esc(k)}</small><strong>${esc(fmt(v,4))}</strong><em>${esc(hint)}</em></div>`).join('');
    }
    function render(payload,data){
        const {a,b,comparison}=payload;cards(a,data);
        const invalid=['REFERENCE_INVALID','FAIL'].includes(a.status);
        const note=[names[method()]+' · '+a.scorer_id,a.note,...a.identity_warnings,
            invalid?'Guide 未通过所选检查；仅作诊断，不能认定可提交。':'',
            a.source==='reference'?'当前 Source 是比赛参考 Guide，不是评分器；评分保持 N/A。':''].filter(Boolean).join(' · ');
        el('scoringMessage').textContent=note;el('scoringMessage').classList.toggle('scoring-alert',invalid||a.identity_warnings.length>0);
        el('badge').textContent=a.source==='reference'?'比赛参考 Guide · 未评分':a.scorer_id+' · '+a.status;
        el('badge').classList.toggle('fail',invalid||!a.router_complete||a.status==='ANALYSIS_ERROR');
        el('report').textContent=JSON.stringify({scorer_id:a.scorer_id,status:a.status,identity:{source_sha256:a.scorer_source_sha256,binary_sha256:a.scorer_binary_sha256,guide_sha256:a.guide_sha256,input_sha256:a.input_sha256,router_sha256:a.router_sha256,config_sha256:a.config_sha256},report_path:a.report_path,report:a.report},null,2);
        const qs=new URLSearchParams({id:el('run').value,scorer:method(),source:el('source').value});
        el('scoreFiles').innerHTML=Object.keys(a.files).map(file=>`<a target="_blank" rel="noopener" href="/api/scoring/file?${esc(qs.toString())}&amp;file=${file}">${{report:'原始评分报告',invocation:'调用与身份记录',log:'评分日志（末尾 30 KB）'}[file]}</a>`).join(' · ');
        el('layerScores').innerHTML=table(['Metal Layer','所选评分器 Overflow'],Array.from({length:20},(_,i)=>['M'+(i+1),fmt(a.by_layer[String(i+1)]?.overflow,0)]));
        el('scoreErrors').textContent=JSON.stringify({counts:a.error_counts,details:a.errors},null,2);
        const ar=rows(a),br=b?rows(b):[];
        el('comparetable').innerHTML=table(['指标','A · '+a.label,b?'B · '+b.label:'B · 未选择'],ar.map(([key,v],i)=>[key,fmt(v,6),fmt(br[i]?.[1],6)]));
        el('comparelayers').innerHTML=table(['层','A Overflow','B Overflow'],Array.from({length:20},(_,i)=>['M'+(i+1),fmt(a.by_layer[String(i+1)]?.overflow,0),fmt(b?.by_layer[String(i+1)]?.overflow,0)]));
        el('compareNotice').textContent=comparison.compatible?'评分来源与输入身份一致，可按同口径查看。'+(comparison.diagnostic_only?' 包含非法 Guide，只可诊断，不能据此声称有效优化或可提交。':''):comparison.reasons.join('；');
        el('compareNotice').classList.toggle('scoring-alert',!!b&&(!comparison.compatible||comparison.diagnostic_only));
        el('fidelity').textContent=method().startsWith('reference_')?'项目自行实现的参考评分器，不是 官方新版。百分制分数和最终总分：'+unknown+'。': 'OFFICIAL_ORIGINAL 是原版官方 ELF；latest 仅是历史 CLI 名称。辅助加权质量不是比赛最终总分，proxy / mock-DRT 不代表详细布线签核。';
    }
    async function load(data=null){
        const token=++sequence,id=el('run').value;if(!id)return;
        const q=new URLSearchParams({id,scorer:method(),source:el('source').value,b:el('compare').value});
        for(const name of ['metrics','report','comparetable','comparelayers','layerScores','scoreErrors','scoreFiles','compareNotice','fidelity'])el(name).textContent='';
        el('badge').textContent='SCORING · LOADING';el('badge').classList.remove('fail');
        el('scoringMessage').textContent='正在读取评分概要（不启动 Router 或评分器）…';
        try{
            const payload=await api('/api/scoring?'+q);
            if(token!==sequence||id!==el('run').value)return;
            render(payload,data);
        }catch(error){
            if(token!==sequence||error.name==='AbortError'||error.requestSignal?.aborted)return;
            el('scoringMessage').textContent='评分读取停止：'+error.message;el('scoringMessage').classList.add('scoring-alert');
            el('badge').textContent='SCORING ERROR';el('badge').classList.add('fail');
            el('compareNotice').textContent='评分身份或请求异常，未进行比较；几何查看不受影响。';
        }
    }
    return {clear,load,method};
})();
