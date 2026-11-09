"""Compare completed small-run receipts and persist independent search probes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from common import PROJECT,sha256
from run_v5_trace_case import source_identity


def semantic(value):
    if isinstance(value,dict):return {k:semantic(v) for k,v in value.items() if k not in ('seconds','logs_directory','seed')}
    if isinstance(value,list):return [semantic(v) for v in value]
    return value


def main():
    p=argparse.ArgumentParser();p.add_argument('--on',default='toy_v5_trace_release');p.add_argument('--off',default='toy_v5_trace_off');p.add_argument('--control',default='toy_v5_trace_control');a=p.parse_args()
    out=PROJECT/'outputs/reports/v5_trace';out.mkdir(parents=True,exist_ok=True)
    runs={}
    for mode,label in [('original',a.control),('on',a.on),('off',a.off)]:
        directory=PROJECT/'outputs/runs/toy'/label
        run=json.loads((directory/'run.json').read_text(encoding='utf8'))
        assert run['status']=='ROUTED' and run['returncode']==0
        runs[mode]={'directory':str(directory),'label':label,'guide_sha256':sha256(directory/'route.guide'),'runtime_seconds':run['runtime_seconds'],'validation':run['original_validation'],'report':json.loads((directory/'route.run.json').read_text(encoding='utf8')),'trace_enabled':run['trace_enabled']}
    base=runs['original']
    same_guide=all(r['guide_sha256']==base['guide_sha256'] for r in runs.values())
    same_validation=all(r['validation']==base['validation'] for r in runs.values())
    same_acceptance=all(semantic(r['report']['stages'])==semantic(base['report']['stages']) for r in runs.values())
    # Exact Guide bytes plus same validation proves identical final TSV geometry.
    source=PROJECT/'v5/engine'
    sys.path.insert(0,str(source))
    from guide_rules import tsv_candidates
    from validate import read_guide
    tsvs=[(name,tsv_candidates(rects)) for name,rects in read_guide(Path(base['directory'])/'route.guide') if tsv_candidates(rects)]
    tsv_hash=hashlib.sha256(json.dumps(tsvs,sort_keys=True).encode()).hexdigest()
    probe_root=out/('probes_'+time.strftime('%Y%m%d_%H%M%S'));probe_root.mkdir()
    probes={}
    for mode in ('original','on','off'):
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
        env.pop('V5_TRACE_DIR',None)
        with (probe_root/(mode+'.log')).open('wb') as log:
            subprocess.run([sys.executable,'-B',str(PROJECT/'tests/v5_trace_probe.py'),mode,str(probe_root/mode)],stdout=log,stderr=subprocess.STDOUT,env=env,timeout=120,check=True)
        probes[mode]=json.loads((probe_root/mode/'probe.json').read_text(encoding='utf8'))
    search_equal=True
    for kind in ('astar','manhattan_mst','steiner'):
        for mode in ('on','off'):
            for key in ('guide_sha256','validation','cross_die','decisions_sha256','search_operations','wire_proxy'):
                search_equal &= probes[mode]['results'][kind][key]==probes['original']['results'][kind][key]
    result={'status':'PASS' if same_guide and same_validation and same_acceptance and search_equal else 'FAIL','runs':runs,'same_guide_sha256':same_guide,'same_validation':same_validation,'same_stage_acceptance_and_metrics':same_acceptance,'same_probe_search_decisions_and_wire':search_equal,'tsv_nets':len(tsvs),'tsv_geometry_sha256':tsv_hash,'original_79_unchanged':source_identity()['file_count']==79,'probe_directory':str(probe_root),'scope':'Toy full original defaults + small synthetic fanout probes; no large Case reruns; no claim of clock-budget equivalence at large scale'}
    (out/'equivalence_result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('runs',)},ensure_ascii=False),flush=True)
    if result['status']!='PASS':raise SystemExit(2)


if __name__=='__main__':main()
