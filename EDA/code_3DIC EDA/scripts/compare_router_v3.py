#!/usr/bin/env python3
"""Bounded V2/V3 ablations on toy, sample001 and case001 only; new records only.

Production binaries are never built or replaced here. Scores come exclusively
from evaluate.py and its actual report. Existing experiments cannot be resumed
or overwritten. Large Cases are deliberately not accepted by this command.
"""
import argparse,csv,datetime,hashlib,json,subprocess,sys,time
from pathlib import Path
from common import PROJECT,OUTPUTS,sha256,safe_label

VARIANTS=[('v2','build/router_v2','default'),('legacy',None,'v3_legacy'),
          ('net_order',None,'v3_net_order'),('tree_aware',None,'v3_tree_aware'),
          ('congestion',None,'v3_congestion'),('combined',None,'v3_combined')]
MODES=['net_order_mode','tree_route_mode','reroute_priority_mode']


def read(path):return json.loads(Path(path).read_text(encoding='utf8'))


def write_new(path,value):
    with Path(path).open('x',encoding='utf8',newline='\n') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')


def normalized(data):
    # Result timing is observational, not an algorithm output.
    data=json.loads(json.dumps(data));data['metrics'].pop('runtime_seconds',None)
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def replay(directory,data):
    routes={};strategies=set();events=0
    with (directory/'trace.jsonl').open(encoding='utf8') as lines:
        for line in lines:
            event=json.loads(line);events+=1
            if event['event']=='trace_truncated':return {'complete':False,'reason':'size limit'}
            if event['event']=='net_removed':routes.pop(event['net'],None)
            if event['event']=='net_added':routes[event['net']]=event['edges']
            if event['event']=='topology':
                strategies.add(event.get('strategy','mst_astar'))
                if event.get('strategy')=='tree_aware' and event.get('mst_edges'):raise RuntimeError('Tree trace masquerades as MST')
    expected={n['net']:n['edges'] for n in data['nets']}
    if routes!=expected:raise RuntimeError('Trace replay differs from final routes')
    return {'complete':True,'events':events,'strategies':sorted(strategies)}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--experiment',required=True)
    p.add_argument('--case',choices=['toy','sample001','open_3d_case001'],action='append')
    p.add_argument('--router-budget',type=int,default=180)
    p.add_argument('--evaluation-budget',type=int,default=120)
    p.add_argument('--no-score',action='store_true')
    a=p.parse_args(argv);safe_label(a.experiment)
    if not 1<=a.router_budget<=900 or not 1<=a.evaluation_budget<=900:p.error('Budgets must be 1..900 seconds')
    candidate=a.candidate.resolve()
    if not candidate.is_file():p.error('Candidate binary not found')
    cases=a.case or ['toy','sample001','open_3d_case001']
    # Always finish small correctness runs before case001.
    cases=sorted(set(cases),key=['toy','sample001','open_3d_case001'].index)
    base=read(PROJECT/'configs/default.json')
    for _,_,config in VARIANTS[1:]:
        values=read(PROJECT/'configs'/f'{config}.json')
        if {k:v for k,v in values.items() if k not in MODES}!=base:raise RuntimeError('Shared parameters differ: '+config)
    report=OUTPUTS/'reports/v3_algorithm'/a.experiment/'comparisons'
    report.mkdir(parents=True,exist_ok=False)
    pinned={str(path):sha256(path) for path in [candidate,PROJECT/'build/router_v2',PROJECT/'configs/default.json',*[PROJECT/'configs'/f'{v[2]}.json' for v in VARIANTS[1:]]]}
    plan={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT).decode().strip(),
          'git_dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=PROJECT)),
          'cases':cases,'router_budget':a.router_budget,'evaluation_budget':a.evaluation_budget,'pinned':pinned,
          'candidate_status':'UNREGISTERED_CANDIDATE','score_enabled':not a.no_score,'records':[]}
    write_new(report/'plan.json',plan);rows=[];sequence=0
    for case in cases:
        for variant,binary,config in VARIANTS:
            if any(sha256(Path(path))!=expected for path,expected in pinned.items()):raise RuntimeError('Pinned executable/config changed; experiment stopped')
            sequence+=1;label=safe_label(f'v3_{a.experiment}_{variant}')
            directory=OUTPUTS/'runs'/case/label
            if directory.exists():raise RuntimeError('Historical Label already exists: '+label)
            executable=PROJECT/binary if binary else candidate;config_path=PROJECT/'configs'/f'{config}.json'
            cmd=[sys.executable,'-B',str(PROJECT/'scripts/run_case.py'),'--case',case,'--label',label,'--backend','wsl','--router',str(executable),'--config',str(config_path),'--time-limit',str(a.router_budget)]
            if case!='open_3d_case001':cmd+=['--trace']
            prefix=report/f'{sequence:02d}_{case}_{variant}'
            print(f'[{sequence}] {case} / {variant}',flush=True)
            with prefix.with_suffix('.run.log').open('xb') as log:
                run_call=subprocess.run(cmd,cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT,timeout=a.router_budget+90)
            run=read(directory/'run.json');row={'case':case,'variant':variant,'label':label,'run_directory':str(directory),'router_version':run.get('router_version'),
                'router_sha256':run.get('router_sha256'),'git_commit':plan['git_commit'],'worktree_dirty':plan['git_dirty'],
                'config_path':str(config_path),'config_sha256':sha256(config_path),'status':run.get('status'),
                'returncode':run.get('returncode'),'helper_returncode':run_call.returncode,'failure_category':run.get('failure_category'),
                'runtime_seconds':run.get('runtime_seconds'),'command_tree_peak_rss_bytes':run.get('peak_memory_bytes'),
                'peak_rss_bytes':(run.get('supervisor_exit') or {}).get('child_peak_rss_bytes') or run.get('router_peak_rss_bytes') or run.get('peak_memory_bytes'),
                'peak_rss_source':'Linux supervisor child getrusage' if (run.get('supervisor_exit') or {}).get('child_peak_rss_bytes') is not None else 'sampled Router or GNU time command tree; see run.json',
                'router_sampled_peak_rss_bytes':run.get('router_peak_rss_bytes'),'official_status':'NOT_EVALUATED',
                'astar_expansions':None,'astar_calls':None,'official_total_score':None}
            if run.get('status')=='ROUTED' and run_call.returncode==0 and run.get('returncode')==0:
                data=read(directory/'result.json');row.update(metrics=data['metrics'],cpp_runtime_seconds=data['metrics'].get('runtime_seconds'),net_count=len(data['nets']),layers=len(data['layers']),
                    guide_sha256=sha256(directory/'route.guide'),normalized_json_sha256=normalized(data))
                if case!='open_3d_case001':row['trace_replay']=replay(directory,data)
                if not a.no_score:
                    evaluation=[sys.executable,'-B',str(PROJECT/'scripts/evaluate.py'),'--case',case,'--label',label,'--backend','wsl','--engine','latest','--timeout',str(a.evaluation_budget)]
                    with prefix.with_suffix('.evaluation.log').open('xb') as log:
                        call=subprocess.run(evaluation,cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT,timeout=a.evaluation_budget+75)
                    inv=read(directory/'official_latest/invocation.json');official=inv.get('report',{})
                    row.update(official_status=inv.get('official_status',inv.get('status','NOT_EVALUATED')),evaluation_returncode=call.returncode,
                               evaluation_failure_category=inv.get('failure_category'),official_report=official,evaluator_sha256=inv.get('evaluator_sha256'))
                    # Reuse explicit-total semantics; never invent an official total.
                    sys.path.insert(0,str(PROJECT/'parameter_tuning'));from records import official_metrics
                    row.update(official_metrics(official));row['official_total_score']=row.pop('total_score',None)
            write_new(prefix.with_suffix('.record.json'),row);rows.append(row)
            if not a.no_score and case!='open_3d_case001' and row['status']=='ROUTED' and row['official_status']!='PASS':raise RuntimeError('Small official verification did not pass; larger experiments stopped')
            if variant=='legacy' and row.get('status')=='ROUTED':
                previous=next(r for r in rows if r['case']==case and r['variant']=='v2')
                if row.get('guide_sha256')!=previous.get('guide_sha256') or row.get('normalized_json_sha256')!=previous.get('normalized_json_sha256'):raise RuntimeError('Legacy equivalence failed; do not proceed')
            if row['status']!='ROUTED':
                if row.get('failure_category') in ['UNKNOWN','WSL_EXIT_ERROR','OOM_CONFIRMED','OOM_SUSPECTED']:raise RuntimeError('Infrastructure failure; see the preserved run/record')
                if case!='open_3d_case001':raise RuntimeError('Small correctness case failed; larger experiments stopped')
    write_new(report/'results.json',{'plan':plan,'rows':rows})
    fields=['case','variant','router_version','router_sha256','status','official_status','cpp_runtime_seconds','runtime_seconds','peak_rss_bytes','guide_sha256','wirelength_dbu','official_overflow','pearson','wirelength_score','congestion_score','thermal_score','official_total_score']
    with (report/'results.csv').open('x',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    print('Completed:',report,flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
