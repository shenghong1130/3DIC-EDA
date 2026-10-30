#!/usr/bin/env python3
"""Phase-one OAT experiments; existing Router/evaluator helpers remain unchanged."""
from pathlib import Path
import argparse
import ctypes
import json
import math
import os
import subprocess
import sys
import time
import uuid

from support import (HERE, PROJECT, TuningError, load, atomic, immutable, locked,
    variants, unique_label, commands, fingerprints, verify_files, object_hash,
    safe_label, sha256, utc, validate_state)
from records import (read_optional, run_errors, artifact_errors, artifacts,
    official_state, collect)
from common import resolve_router, resolve_config

TERMINAL = {'COMPLETED','ROUTER_FAILED','EVALUATION_FAILED','INTERRUPTED','BLOCKED'}
RETRYABLE = TERMINAL - {'COMPLETED'}

def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case', choices=['open_3d_case001','open_3d_case003','open_3d_case005'])
    p.add_argument('--experiment', default='phase1_case001')
    p.add_argument('--plan-config', type=Path, default=HERE/'experiment_config.json')
    p.add_argument('--suite', default=None)
    p.add_argument('--backend', choices=['wsl','native'])
    p.add_argument('--engine', choices=['latest','legacy'])
    p.add_argument('--router', type=Path, help='Default: Linux project/router for WSL; native platform default otherwise')
    p.add_argument('--router-timeout', type=float)
    p.add_argument('--evaluation-timeout', type=float)
    p.add_argument('--max-runs', type=int, help='Maximum NEW Router attempts in this invocation; pending evaluations do not consume this limit')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--report-only', action='store_true')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--retry-failed', action='store_true', help='Explicitly retry failed/interrupted candidates using fresh Labels; never overwrite')
    p.add_argument('--confirm-thermal-source', metavar='NOTE', help='case005 only: record how the mismatched Thermal source was verified')
    return p

def directory(experiment):
    safe_label(experiment)
    target = HERE/'reports'/experiment
    if not target.resolve().is_relative_to(HERE.resolve()):
        raise TuningError('Experiment directory redirects outside parameter_tuning')
    return target

def prepare(args, report):
    settings = load(args.plan_config)
    plan_file = report/'experiment_plan.json'; state_file = report/'experiment_state.json'
    if plan_file.exists():
        plan = load(plan_file)
        for key, value in [('case',args.case),('backend',args.backend),('engine',args.engine),
                ('suite',args.suite),('router_timeout',args.router_timeout),('evaluation_timeout',args.evaluation_timeout)]:
            if value is not None and value != plan[key]:
                raise TuningError('Frozen plan differs at '+key+'; use a new --experiment')
        if args.router is not None and resolve_router(args.router) != resolve_router(plan['router_path']):
            raise TuningError('Frozen Router path differs; use a new experiment')
        if str(args.plan_config.resolve()) != plan['plan_config_path']:
            raise TuningError('Different plan-config path; use the original or a new experiment')
        if not state_file.is_file():
            raise TuningError('Plan exists without state; initialization incomplete, use a new experiment')
        state = load(state_file)
        if state.get('plan_sha256') != object_hash(plan) or set(state['experiments']) != {r['id'] for r in plan['variants']}:
            raise TuningError('State/immutable plan mismatch')
        validate_state(plan,state)
        return plan, state
    if args.report_only:
        raise TuningError('No experiment to report; use --dry-run first')
    if state_file.exists() or any(p.name != '.manager.lock' for p in report.iterdir()):
        raise TuningError('Unknown/incomplete existing experiment directory; use a new name')
    case = args.case or settings['default_case']; suite = args.suite or 'oat'
    if case != 'open_3d_case001' and args.case is None:
        raise TuningError('Large Cases require an explicit --case selection')
    backend = args.backend or settings['backend']; engine = args.engine or settings['engine']
    if os.name == 'nt' and backend == 'native':
        raise TuningError('Windows native official evaluation is unsupported; use --backend wsl')
    baseline = load(resolve_config(PROJECT/'configs/default.json'))
    rows = variants(baseline, settings, suite)
    router = resolve_router(args.router or (PROJECT/'router' if backend == 'wsl' or os.name != 'nt' else PROJECT/'outputs/router.exe'))
    rt = args.router_timeout if args.router_timeout is not None else settings['router_timeout']
    et = args.evaluation_timeout if args.evaluation_timeout is not None else settings['evaluation_timeout']
    monitor = settings['monitoring']
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or x<=0 for x in [rt,et,*monitor.values()]):
        raise TuningError('Budgets and monitoring values must be finite and positive')
    if int(monitor['progress_every']) != monitor['progress_every']:
        raise TuningError('progress_every must be an integer')
    fp = fingerprints(case,backend,engine,router)
    fp['files'][str(args.plan_config.resolve())] = sha256(args.plan_config)
    batch = uuid.uuid4().hex
    config_dir = HERE/'configs'/args.experiment
    if not config_dir.resolve().is_relative_to(HERE.resolve()):
        raise TuningError('Config directory redirects outside parameter_tuning')
    for row in rows:
        path = config_dir/(row['id']+'.json'); immutable(path,row['config'])
        row.update(config_path=str(path.resolve()),config_sha256=sha256(path),
                   first_label=unique_label(args.experiment,row['id'],batch))
    plan = {'schema_version':1,'experiment':args.experiment,'batch_id':batch,'created_at':utc(),
        'case':case,'suite':suite,'backend':backend,'engine':engine,'router_path':str(router),
        'router_timeout':rt,'evaluation_timeout':et,'monitoring':monitor,
        'baseline':baseline,'default_config_path':str(PROJECT/'configs/default.json'),
        'plan_config_path':str(args.plan_config.resolve()),'fingerprints':fp,'variants':rows,
        'score_policy':'Only explicit official total fields; no weighted total is synthesized.',
        'tuning_source_sha256':{p.name:sha256(p) for p in sorted(HERE.glob('*.py'))}}
    state = {'schema_version':1,'plan_sha256':object_hash(plan),'batch_state':'PENDING','updated_at':utc(),
        'events':[],'experiments':{r['id']:{'state':'PENDING','attempts':[]} for r in rows}}
    immutable(plan_file,plan); immutable(state_file,state)
    return plan,state

def save_state(report,state):
    state['updated_at'] = utc(); atomic(report/'experiment_state.json',state)

def transition(report,state,row,status,reason=None):
    entry = state['experiments'][row['id']]; entry['state'] = status
    if entry['attempts']:
        entry['attempts'][-1].update(state=status,updated_at=utc())
        if reason:
            entry['attempts'][-1]['reason'] = reason
    state['events'].append({'at':utc(),'id':row['id'],'state':status,'reason':reason})
    save_state(report,state)

def pid_alive(pid):
    if not isinstance(pid,int) or pid <= 0:
        return False
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:
            return ctypes.get_last_error()==5  # Access denied is not proof of death.
        code=ctypes.c_uint32()
        try:
            return not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value==259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid,0);return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

def possible_active(plan,attempt,directory):
    if pid_alive(attempt.get('process_pid')):
        return 'Previous helper PID still exists (or was reused); do not start another attempt.'
    run = read_optional(directory/'run.json')
    if not directory.exists() and attempt.get('process_started_epoch') and not attempt.get('launch_failed'):
        # A crash between Popen and PID persistence must not permit a duplicate.
        budget=plan['evaluation_timeout'] if attempt.get('process_role')=='evaluation' else plan['router_timeout']
        if time.time() < attempt['process_started_epoch']+budget+60:
            return 'Previous launch has no finalized directory; wait for its recorded budget before retry.'
    if run.get('status') == 'RUNNING' or (run and not (directory/'router_exit.json').exists()
            and at_resource_mode(run)=='linux_supervisor' and run.get('status')!='ROUTED'):
        heartbeat = directory/'host_heartbeat.json'
        grace = max(60,plan['monitoring']['stall_after']*2,plan['monitoring']['monitor_interval']*3)+15
        latest = heartbeat.stat().st_mtime if heartbeat.exists() else directory.stat().st_mtime
        if time.time()-latest < grace:
            return 'Router may still be supervised; wait for host lease/cancellation to finish.'
    if attempt.get('process_role') == 'evaluation' and not (directory/f"official_{plan['engine']}/invocation.json").exists():
        if time.time() < attempt.get('process_started_epoch',0)+plan['evaluation_timeout']+45:
            return 'Interrupted evaluator may still run under its Linux timeout; wait for its budget to expire.'
    return None

def at_resource_mode(run):
    return run.get('monitoring',{}).get('resource_mode')

def seal(plan,attempt,directory):
    attempt['artifacts']=artifacts(directory,plan['engine'])

def check_other_runs(plan,state):
    """Do not start an experiment alongside an external unfinished Router run."""
    own={(plan['case'],attempt['label']) for entry in state['experiments'].values() for attempt in entry['attempts']}
    for file in (PROJECT/'outputs/runs').glob('*/*/run.json'):
        if (file.parent.parent.name,file.parent.name) in own:
            continue
        run=read_optional(file)
        if run.get('status')=='RUNNING':
            raise TuningError('Another Router run is RUNNING or not finalized: '+str(file.parent)+
                '. Wait/verify its diagnostics; this manager will not stop it or rewrite its history.')

def finish_evaluation(plan,row,state,report,directory):
    entry=state['experiments'][row['id']];attempt=entry['attempts'][-1]
    inv=read_optional(directory/f"official_{plan['engine']}/invocation.json")
    official=read_optional(directory/f"official_{plan['engine']}/evaluation_report.json")
    seal(plan,attempt,directory)
    if official_state(inv,official)=='PASS' and attempt.get('evaluation_helper_returncode') in [None,0]:
        transition(report,state,row,'COMPLETED')
        observed=collect(plan,row,entry,report)
        if not observed['eligible']:
            transition(report,state,row,'BLOCKED','; '.join(observed['integrity_errors']) or 'Incomplete PASS evidence')
            raise TuningError('PASS evidence did not match the frozen batch')
    else:
        transition(report,state,row,'EVALUATION_FAILED',inv.get('failure_reason') or 'Official '+official_state(inv,official))
    return inv

def reconcile(plan,state,report):
    """Only own labels; complete files + identity/hashes, never directory presence alone."""
    for row in plan['variants']:
        entry=state['experiments'][row['id']]
        if not entry['attempts']:
            continue
        attempt=entry['attempts'][-1];directory=PROJECT/'outputs/runs'/plan['case']/attempt['label']
        if entry['state']=='COMPLETED':
            errors=artifact_errors(directory,attempt.get('artifacts',{}))
            observed=collect(plan,row,entry,report)
            if errors or not observed['eligible']:
                transition(report,state,row,'BLOCKED','Completed artifacts failed revalidation: '+str(errors+observed['integrity_errors']))
                raise TuningError('Completed experiment evidence changed; batch paused')
            continue
        errors=artifact_errors(directory,attempt.get('artifacts',{}))
        if errors:
            transition(report,state,row,'BLOCKED','; '.join(errors))
            raise TuningError('Sealed history changed; cannot re-seal it on another resume')
        active=possible_active(plan,attempt,directory)
        if active:
            transition(report,state,row,'BLOCKED',active);raise TuningError(active)
        if not directory.exists():
            transition(report,state,row,'INTERRUPTED','Reserved attempt has no run directory; a retry requires a new Label.')
            continue
        if entry['state'] in {'ROUTER_FAILED','EVALUATION_FAILED'}:
            errors=artifact_errors(directory,attempt.get('artifacts',{}))
            if errors:
                transition(report,state,row,'BLOCKED','; '.join(errors));raise TuningError('Failure history changed')
            continue
        run=read_optional(directory/'run.json')
        errors=run_errors(plan,row,attempt['label'],run) if run else ['No finalized run manifest']
        errors+=artifact_errors(directory,attempt.get('route_artifacts',{}))
        if errors:
            transition(report,state,row,'BLOCKED','; '.join(errors));continue
        routed=run.get('status')=='ROUTED' and run.get('returncode')==0 and run.get('process_status')=='FINISHED'
        if routed and all((directory/n).is_file() for n in ['result.json','route.guide']):
            if not attempt.get('route_artifacts'):
                attempt['route_artifacts']={n:sha256(directory/n) for n in ['result.json','route.guide']}
                attempt['route_seal_note']='First seal on recovery of a finalized, identity-verified Router run'
                save_state(report,state)
            score_dir=directory/f"official_{plan['engine']}"
            inv=read_optional(score_dir/'invocation.json')
            if inv:
                finish_evaluation(plan,row,state,report,directory)
            elif score_dir.exists():
                transition(report,state,row,'INTERRUPTED','Partial official directory retained; retry requires new Router Label.')
            else:
                transition(report,state,row,'ROUTED','Verified complete Router output; official evaluation has not started.')
        elif run.get('status')=='FAIL':
            seal(plan,attempt,directory);transition(report,state,row,'ROUTER_FAILED',run.get('failure_reason'))
        else:
            transition(report,state,row,'INTERRUPTED','Run was not finalized; never promote residual files to success.')

def launch(command,role,plan,row,state,report):
    entry=state['experiments'][row['id']];attempt=entry['attempts'][-1]
    log_dir=report/'attempts'/attempt['label'];log_dir.mkdir(parents=True,exist_ok=True)
    log_path=log_dir/(role+'_helper.log')
    if log_path.exists():
        raise TuningError('Helper log already exists; refusing duplicate execution')
    attempt.update(process_pid=None,process_role=role,process_started_epoch=time.time(),launch_failed=False,
        manager_source_sha256={p.name:sha256(p) for p in sorted(HERE.glob('*.py'))})
    save_state(report,state)
    with log_path.open('xb') as log:
        try:
            process=subprocess.Popen(command,cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT,shell=False)
        except OSError:
            attempt['launch_failed']=True;save_state(report,state);raise
        attempt.update(process_pid=process.pid)
        save_state(report,state)
        try:
            code=process.wait()
        except KeyboardInterrupt:
            # Console Ctrl+C also reaches helpers. Linux Router cancellation is
            # additionally file-based; never kill all WSL or touch another run.
            if role=='router':
                cancel=PROJECT/'outputs/runs'/plan['case']/attempt['label']/'host_cancel.json'
                if cancel.parent.is_dir() and not cancel.exists():
                    immutable(cancel,{'reason':'PARAMETER_MANAGER_INTERRUPTED','timestamp_utc':utc()})
            try:
                process.wait(timeout=12)
            except (subprocess.TimeoutExpired,KeyboardInterrupt):
                pass  # Retain PID; resume blocks while helper/lease/budget may be alive.
            transition(report,state,row,'INTERRUPTED','Ctrl+C; own helper may still be finalizing. Check retained logs before retry.')
            raise
    attempt[role+'_helper_returncode']=code;attempt['process_pid']=None;save_state(report,state)
    return code

def run_batch(plan,state,report,args):
    if any(e['attempts'] for e in state['experiments'].values()) and not args.resume:
        raise TuningError('Experiment already started; use --resume (failures need explicit --retry-failed)')
    errors=verify_files(plan,check_environment=True)
    if errors:
        raise TuningError('Frozen inputs/tools/environment changed: '+'; '.join(errors))
    if plan['case']=='open_3d_case005' and not (report/'thermal_source_attestation.json').exists():
        if not args.confirm_thermal_source or not args.confirm_thermal_source.strip():
            raise TuningError('case005 Thermal filename mismatch: supply --confirm-thermal-source "verified source and evidence" after verification')
        immutable(report/'thermal_source_attestation.json',{'note':args.confirm_thermal_source.strip(),
            'thermal_sha256':plan['fingerprints']['input_sha256']['thermal'],'created_at':utc()})
    reconcile(plan,state,report)
    state['batch_state']='RUNNING';save_state(report,state);started=0
    for row in plan['variants']:
        entry=state['experiments'][row['id']]
        if entry['state']=='COMPLETED':
            continue
        retry=entry['state'] in RETRYABLE and args.retry_failed
        if entry['state']=='PENDING' or retry:
            if args.max_runs is not None and started>=args.max_runs:
                continue
            check_other_runs(plan,state)
            errors=verify_files(plan,check_environment=True)
            if errors:
                raise TuningError('Versions changed before Router: '+'; '.join(errors))
            attempt_index=len(entry['attempts'])+1
            label=row['first_label'] if not entry['attempts'] else unique_label(plan['experiment'],row['id'],plan['batch_id'],attempt_index)
            while (PROJECT/'outputs/runs'/plan['case']/label).exists():
                label=unique_label(plan['experiment'],row['id'],plan['batch_id'],attempt_index)
            run_cmd,eval_cmd=commands(plan,row,label)
            attempt={'attempt':attempt_index,'label':label,'state':'PENDING','created_at':utc(),
                'router_command':run_cmd,'evaluation_command':eval_cmd,'artifacts':{}}
            entry['attempts'].append(attempt);transition(report,state,row,'ROUTING')
            print(f"[ROUTER {started+1}] {row['id']} -> {label}",flush=True);started+=1
            code=launch(run_cmd,'router',plan,row,state,report)
            directory=PROJECT/'outputs/runs'/plan['case']/label;run=read_optional(directory/'run.json')
            errors=verify_files(plan,check_environment=True)
            errors+=run_errors(plan,row,label,run) if run else ['Router helper did not finalize run.json']
            if errors:
                transition(report,state,row,'BLOCKED','; '.join(errors));raise TuningError('Router/version integrity failure')
            if code!=0 or run.get('status')!='ROUTED' or run.get('returncode')!=0 or run.get('process_status')!='FINISHED' or not all((directory/n).is_file() for n in ['result.json','route.guide']):
                seal(plan,attempt,directory);transition(report,state,row,'ROUTER_FAILED',run.get('failure_reason') or 'Router incomplete')
                if run.get('process_status')=='INTERRUPTED':
                    state['batch_state']='INTERRUPTED';save_state(report,state);return started
                if run.get('failure_category') in {'LAUNCH_ERROR','WSL_EXIT_ERROR','UNKNOWN','OOM_SUSPECTED','OOM_CONFIRMED','ROUTER_SIGNAL'}:
                    raise TuningError('Infrastructure/unknown failure; batch paused: '+str(run.get('failure_category')))
                continue
            attempt['route_artifacts']={n:sha256(directory/n) for n in ['result.json','route.guide']}
            transition(report,state,row,'ROUTED')
        if entry['state']=='ROUTED':
            check_other_runs(plan,state)
            attempt=entry['attempts'][-1];directory=PROJECT/'outputs/runs'/plan['case']/attempt['label']
            errors=verify_files(plan,check_environment=True)+artifact_errors(directory,attempt.get('route_artifacts',{}))
            if errors:
                transition(report,state,row,'BLOCKED','; '.join(errors));raise TuningError('Changed inputs/Guide before evaluation')
            if (directory/f"official_{plan['engine']}").exists():
                transition(report,state,row,'INTERRUPTED','Official directory already exists; refusing overwrite');continue
            transition(report,state,row,'EVALUATING');print('[EVALUATOR] '+row['id'],flush=True)
            code=launch(attempt['evaluation_command'],'evaluation',plan,row,state,report)
            errors=verify_files(plan,check_environment=True)+artifact_errors(directory,attempt.get('route_artifacts',{}))
            if errors:
                transition(report,state,row,'BLOCKED','; '.join(errors));raise TuningError('Versions/Guide changed during evaluation')
            inv=finish_evaluation(plan,row,state,report,directory)
            if inv.get('process_status')=='INTERRUPTED':
                state['batch_state']='INTERRUPTED';save_state(report,state);return started
            rc=inv.get('returncode')
            if not inv or inv.get('process_status')=='LAUNCH_ERROR' or isinstance(rc,int) and (rc & 0xffffffff)>=0x80000000:
                raise TuningError('Evaluator infrastructure failure; batch paused (see helper/evaluator logs)')
    state['batch_state']='FINISHED' if all(e['state'] in TERMINAL for e in state['experiments'].values()) else 'PARTIAL'
    save_state(report,state);return started

def preview(plan,state,report):
    print(f"Experiment {plan['experiment']} | {plan['case']} | {len(plan['variants'])} candidates | {plan['backend']}/{plan['engine']}")
    print(f"JSON time_limit={plan['baseline']['time_limit']}; effective Router CLI={plan['router_timeout']}s; evaluator={plan['evaluation_timeout']}s")
    print('Reports: '+str(report)+'\nRuns: '+str(PROJECT/'outputs/runs'/plan['case']))
    if plan['fingerprints']['warning']:
        print('INPUT WARNING: '+plan['fingerprints']['warning'])
    for row in plan['variants']:
        entry=state['experiments'][row['id']];label=entry['attempts'][-1]['label'] if entry['attempts'] else row['first_label']
        run,evaluate=commands(plan,row,label)
        print(f"\n{row['id']}: {json.dumps(row['changes'])} [{entry['state']}]")
        # Human display only. Actual execution always passes the original argv array.
        import shlex
        display=subprocess.list2cmdline if os.name=='nt' else shlex.join
        print('  '+display(run));print('  '+display(evaluate))

def main(argv=None):
    args=parser().parse_args(argv)
    if args.max_runs is not None and args.max_runs<=0:
        parser().error('--max-runs must be positive')
    for budget in [args.router_timeout,args.evaluation_timeout]:
        if budget is not None and (not math.isfinite(budget) or budget<=0):
            parser().error('Timeout budgets must be finite and positive')
    report=None;state=None;plan=None
    try:
        report=directory(args.experiment)
        with locked(report/'.manager.lock'):
            plan,state=prepare(args,report)
            from analysis import generate
            if args.report_only:
                generate(plan,state,report);print('Report regenerated: '+str(report/'analysis.md'));return 0
            if args.dry_run:
                errors=verify_files(plan)
                if errors:
                    raise TuningError('; '.join(errors))
                preview(plan,state,report);generate(plan,state,report);return 0
            with locked(HERE/'reports/.execution.lock'):
                try:
                    count=run_batch(plan,state,report,args)
                except KeyboardInterrupt:
                    state['batch_state']='INTERRUPTED';save_state(report,state);generate(plan,state,report)
                    print('Interrupted; history retained. Resume after helpers finish.',file=sys.stderr);return 130
                except (TuningError,OSError,ValueError,subprocess.SubprocessError) as e:
                    state.update(batch_state='BLOCKED',blocked_reason=str(e));save_state(report,state)
                    generate(plan,state,report);raise
            generate(plan,state,report);print(f'New Router attempts: {count}; reports: {report}')
            return 0 if state['batch_state']!='INTERRUPTED' else 130
    except (TuningError,OSError,ValueError,KeyError,subprocess.SubprocessError) as e:
        print('EXPERIMENT BLOCKED: '+str(e),file=sys.stderr);return 2

if __name__=='__main__':
    raise SystemExit(main())
