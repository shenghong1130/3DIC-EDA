"""Input-only final entry, with validated fallback after each optional stage.

The frozen v0.12 baseline is generated from supplied inputs. No saved GUIDE,
case-name table, official result or network access is used during execution.
"""
import route_trace as _trace
import argparse,hashlib,json,os,subprocess,sys,tempfile,time
from pathlib import Path
from design import Design
from validate import validate
from final_policy import accept_buffered,shared_maze_budget
from runtime_budget import run_bounded


def choose_stage(kind,report):
    if kind=='buffered':
        return accept_buffered(report)
    return True


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--def',dest='def_file',type=Path,required=True)
    for k in ('lef','thermal-report','output'):p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--development-small-budget',action='store_true',help='Reduced uniform budgets for integration checks, not the final default')
    a=p.parse_args();a.output=a.output.resolve()
    if a.output.exists():p.error('Output exists')
    inputs={'def':a.def_file.resolve(),'lef':a.lef.resolve(),'thermal':a.thermal_report.resolve()}
    hashes={k:hashlib.sha256(v.read_bytes()).hexdigest() for k,v in inputs.items()}
    here=Path(__file__).resolve().parent;baseline=here/'baseline_v12';assert (baseline/'portable.py').is_file()
    a.output.parent.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix='.final-router-',dir=a.output.parent))
    env=dict(os.environ,PYTHONPATH='',PYTHONNOUSERSITE='1');start=time.monotonic();reports=[]
    current=stage/'baseline.guide';cmd=[sys.executable,str(baseline/'portable.py'),'--def',str(inputs['def']),'--lef',str(inputs['lef']),'--thermal-report',str(inputs['thermal']),'--output',str(current)]
    if a.development_small_budget:cmd+=['--fast-seed','--wire-budget','16','--maze-budget','1','--no-scale-search','--joint-tsv-budget','4','--congestion-budget','16','--escape-budget','128']
    deadline=start+11*3600;baseline_status=None
    try:
        with (stage/'baseline.log').open('w',encoding='utf8') as log:baseline_status=run_bounded(cmd,log,env,min(9*3600,max(1,deadline-time.monotonic()-900)))
    except subprocess.TimeoutExpired:baseline_status='TIMEOUT'
    d=Design(inputs['def'],inputs['lef'])
    if baseline_status!=0 or not current.is_file():
        # Every candidate belongs to this invocation's private staging folder.
        # Child processes have stopped before inspecting complete checkpoints.
        recovered=None
        for candidate in sorted(stage.rglob('*.guide'),key=lambda q:q.stat().st_mtime,reverse=True)[:12]:
            try:validate(d,candidate);recovered=candidate;break
            except (ValueError,OSError):continue
        if recovered is None:raise RuntimeError('Baseline failed without a complete valid checkpoint')
        current=recovered
    validate(d,current)
    small=a.development_small_budget
    stages=[('landing','landing_repair.py',None,True),('buffered','buffered_escape.py',128 if small else 100000,False),('wire','guarded_wire.py',8 if small else 2000,False),('tree','tree_reattach.py',8 if small else 4096,True),('pin','pin_escape.py',8 if small else 256,False),('maze','guarded_maze.py',8 if small else shared_maze_budget(len(d.nets)),False),('compact','compact_guide.py',None,False)]
    for kind,module,budget,thermal in stages:
        output=stage/(kind+'.guide');cmd=[sys.executable,str(here/module),'--def',str(inputs['def']),'--lef',str(inputs['lef']),'--seed-guide',str(current),'--output',str(output)]
        if budget is not None:cmd+=['--max-reroutes',str(budget)]
        if thermal:cmd+=['--thermal-report',str(inputs['thermal'])]
        record={'stage':kind,'seed':current.name,'accepted':False}
        remaining=deadline-time.monotonic()-300
        if remaining<=0:
            record['reason']='Global runtime budget reached; retained validated result';reports.append(record);break
        try:
            with (stage/(kind+'.log')).open('w',encoding='utf8') as log:code=run_bounded(cmd,log,env,min(21600,remaining))
            record['returncode']=code
            if code:record['reason']='Stage failed; retained previously validated route'
            else:
                validation=validate(d,output);report=json.loads(output.with_suffix('.run.json').read_text())
                record['validation']=validation;record['metrics']={k:report.get(k) for k in ('initial','final','stats')}
                if choose_stage(kind,report):current=output;record['accepted']=True
                else:record['reason']='Input-derived quality gate rejected candidate'
        except (subprocess.TimeoutExpired,ValueError,OSError,KeyError) as exc:record['reason']=type(exc).__name__+'; retained previously validated route'
        reports.append(record);print('STAGE',kind,'accepted',record['accepted'],flush=True)
        _trace.stage_decision(record, current, output)
    validation=validate(d,current)
    assert hashes=={k:hashlib.sha256(v.read_bytes()).hexdigest() for k,v in inputs.items()},'Input modified'
    report={'version':'0.16-final-input-only','validation':validation,'stages':reports,'input_sha256':hashes,'input_files_unchanged':True,'seconds':time.monotonic()-start,'development_small_budget':small,'runtime_budget_seconds':39600,'baseline_status':baseline_status,'official_source_score':None,'hidden_quality':'UNVERIFIED','logs_directory':str(stage)}
    temporary=a.output.with_suffix('.tmp.guide');temporary.write_bytes(current.read_bytes());temporary.replace(a.output)
    _trace.alias_guide(temporary, a.output)
    _trace.alias_guide(current, a.output)
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({'version':report['version'],'seconds':report['seconds'],'validation':validation['status']}),flush=True)


if __name__=='__main__':main()
