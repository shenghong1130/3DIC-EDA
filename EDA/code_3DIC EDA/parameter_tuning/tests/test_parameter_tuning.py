"""All Router/official invocations are mocked; no real runs or historical writes."""
from pathlib import Path
import contextlib
import copy
import io
import json
import os
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import support as s
import records as rec
import tune_parameters as t
import analysis as a

BASE=s.load(ROOT.parent/'configs/default.json')
SETTINGS=s.load(ROOT/'experiment_config.json')

class Experiments(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='调参 路径 ')
        self.project=Path(self.temp.name)/'大学 space'/'code_3DIC EDA'
        self.here=self.project/'parameter_tuning';self.here.mkdir(parents=True)
        self.report=self.here/'reports/phase1_case001'
        (self.project/'configs').mkdir();s.immutable(self.project/'configs/default.json',BASE)
        self.settings=self.here/'experiment_config.json';s.immutable(self.settings,SETTINGS)
        (self.project/'router').write_bytes(b'\x7fELFMOCK_NOT_EXECUTABLE')
        self.inputs={}
        for key in ['def','lef','thermal']:
            p=self.project/(key+'.input');p.write_text('mock '+key);self.inputs[key]=str(p)
        self.environment={'test_fixture':True}
        self.stack=contextlib.ExitStack()
        for module,name,value in [(s,'HERE',self.here),(s,'PROJECT',self.project),(t,'HERE',self.here),
                (t,'PROJECT',self.project),(rec,'PROJECT',self.project),(a,'HERE',self.here)]:
            self.stack.enter_context(patch.object(module,name,value))
        self.stack.enter_context(patch.object(s,'environment',return_value=self.environment))
        self.stack.enter_context(patch.object(t,'fingerprints',side_effect=self.fingerprints))
        self.stack.enter_context(patch.object(a,'make_figures',return_value=[]))
        self.calls=[];self.fail_router=set();self.fail_score=set();self.timeouts=set();self.totals=False

    def tearDown(self):
        self.stack.close();self.temp.cleanup()

    def fingerprints(self,case,backend,engine,router,probe=True):
        files={str(Path(p).resolve()):s.sha256(Path(p)) for p in [*self.inputs.values(),router,self.project/'configs/default.json']}
        return {'files':files,'input_sha256':{k:s.sha256(Path(p)) for k,p in self.inputs.items()},
            'router_sha256':s.sha256(router),'evaluator_sha256':'mock_eval_sha', 'inputs':self.inputs,
            'environment':self.environment,'warning':'Mock case005 Thermal mismatch' if case.endswith('005') else None}

    def cli(self,*extra):
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            return t.main(['--plan-config',str(self.settings),*extra])

    def initialize(self):
        self.assertEqual(self.cli('--dry-run'),0)
        return s.load(self.report/'experiment_plan.json'),s.load(self.report/'experiment_state.json')

    def fake_launch(self,cmd,role,plan,row,state,report):
        self.calls.append((row['id'],role,list(cmd)))
        attempt=state['experiments'][row['id']]['attempts'][-1];label=attempt['label']
        target=self.project/'outputs/runs'/plan['case']/label
        if role=='router':
            target.mkdir(parents=True,exist_ok=False)
            failed=row['id'] in self.fail_router
            budget=plan['router_timeout']
            run={'case':plan['case'],'label':label,'config':row['config'],'inputs':self.inputs,
                'backend':plan['backend'],'router_sha256':plan['fingerprints']['router_sha256'],
                'input_sha256':plan['fingerprints']['input_sha256'],'status':'FAIL' if failed else 'ROUTED',
                'returncode':1 if failed else 0,'process_status':'FINISHED','runtime_seconds':10 if row['id']=='baseline' else 11,
                'router_peak_rss_bytes':1024*1024,'failure_category':'ROUTER_ERROR' if failed else 'SUCCESS',
                'failure_reason':'Mock A* expansion limit' if failed else None,
                'monitoring':{'enabled':True,'sample_interval_seconds':plan['monitoring']['monitor_interval'],
                    'heartbeat_seconds':plan['monitoring']['heartbeat_interval'],'progress_every':plan['monitoring']['progress_every'],
                    'stall_after_seconds':plan['monitoring']['stall_after']},
                'command':['wsl','--',shlex.join([str(self.project/'router'),'--time-limit',str(budget)])]}
            s.atomic(target/'run.json',run)
            if not failed:
                (target/'route.guide').write_text('mock fixture Guide\n')
                result={'schema_version':1,'nets':[{'net':'test_n','cross_die':True,'tsv_node':10,'pins':[],'edges':[]}],
                    'metrics':{'routed_nets':1,'nets':1,'runtime_seconds':9,'tsv_count':1},
                    'iterations':[{'iteration':0},{'iteration':1,'accepted':False}],'tsv_density':[1,0],
                    'resources':[[1,2,3]],'heat':[25,26]}
                s.atomic(target/'result.json',result)
            attempt['router_helper_returncode']=1 if failed else 0
            return attempt['router_helper_returncode']
        score=target/f"official_{plan['engine']}";score.mkdir(exist_ok=False)
        failed=row['id'] in self.fail_score or row['id'] in self.timeouts
        report={'status':'FAIL' if failed else 'PASS','score':{'wirelength_score':70,'congestion_score':80,
            'thermal_score':50,'weights':{'wirelength':.45,'congestion':.3,'thermal_tsv':.25}},
            'wirelength':{'total_dbu':100,'total_um':.1,'tsv_count':1},
            'congestion':{'total_overflow':2,'max_overflow':1},'thermal_tsv':{'correlation_above_ambient':0}}
        if self.totals:
            report['score']['total_score']=65 if row['id']=='baseline' else 66
        inv={'status':report['status'],'report':report,'returncode':124 if row['id'] in self.timeouts else 1 if failed else 0,
            'process_status':'TIMEOUT' if row['id'] in self.timeouts else 'FINISHED','engine':plan['engine'],
            'evaluator_sha256':plan['fingerprints']['evaluator_sha256'],'evaluated_input_sha256':plan['fingerprints']['input_sha256'],
            'runtime_seconds':2,'peak_memory_bytes':3000000}
        s.atomic(score/'evaluation_report.json',report);s.atomic(score/'invocation.json',inv)
        attempt['evaluation_helper_returncode']=1 if failed else 0
        return attempt['evaluation_helper_returncode']

    def execute(self,*extra):
        with patch.object(t,'launch',side_effect=self.fake_launch):
            return self.cli(*extra)

    def test_twelve_baseline_and_only_declared_difference(self):
        plan,state=self.initialize()
        self.assertEqual(len(plan['variants']),12)
        for row in plan['variants']:
            self.assertEqual(s.load(row['config_path']),dict(BASE,**row['changes']))
            self.assertEqual({k:v for k,v in row['config'].items() if BASE[k]!=v},row['changes'])
            self.assertEqual(row['config_sha256'],s.sha256(Path(row['config_path'])))
        self.assertTrue(all(e['state']=='PENDING' for e in state['experiments'].values()))
        self.assertFalse((self.report/'best_config.json').exists())

    def test_invalid_change_fixed_parameter_and_fractional_integer(self):
        for config in [dict(BASE,iterations=1.5),dict(BASE,via=0),dict(BASE,margin=float('nan'))]:
            with self.assertRaises(s.TuningError):s.validate_config(config)
        settings=copy.deepcopy(SETTINGS);settings['suites']['oat']['variants'][1]['changes']['history']=2
        with self.assertRaises(s.TuningError):s.variants(BASE,settings,'oat')
        with self.assertRaises(s.TuningError):s.variants(dict(BASE,wire=2),SETTINGS,'oat')

    def test_labels_unique_and_chinese_space_command_arrays(self):
        plan,_=self.initialize();labels={s.unique_label('a'*80,'b'*80,'batch') for _ in range(100)}
        self.assertEqual(len(labels),100);self.assertTrue(all(len(x)<=80 for x in labels))
        run,evaluate=s.commands(plan,plan['variants'][0],'label_01')
        self.assertEqual(run[run.index('--config')+1],plan['variants'][0]['config_path'])
        self.assertIn('大学 space',run[run.index('--config')+1])
        self.assertEqual(run[run.index('--case')+1],'open_3d_case001')
        self.assertEqual(evaluate[evaluate.index('--timeout')+1],'600')
        self.assertIn('--progress-every',run);self.assertNotIn('--trace',run)

    def test_dry_run_never_launches_and_does_not_replace_plan_config_state(self):
        with patch.object(t,'launch',side_effect=AssertionError('Real execution forbidden')) as launch:
            self.initialize();before={p:s.sha256(p) for p in [self.report/'experiment_plan.json',self.report/'experiment_state.json',*list((self.here/'configs/phase1_case001').glob('*.json'))]}
            self.assertEqual(self.cli('--dry-run'),0);launch.assert_not_called()
            self.assertTrue(all(s.sha256(p)==v for p,v in before.items()))
            self.assertFalse((self.project/'outputs').exists())

    def test_max_two_resume_two_skips_complete_and_preserves_history(self):
        self.initialize();self.assertEqual(self.execute('--max-runs','2'),0)
        self.assertEqual([(a,b) for a,b,_ in self.calls],[('baseline','router'),('baseline','evaluation'),('via_low','router'),('via_low','evaluation')])
        old={str(p):s.sha256(p) for p in (self.project/'outputs').rglob('*') if p.is_file()}
        self.calls.clear();self.assertEqual(self.execute('--resume','--max-runs','2'),0)
        self.assertEqual([i for i,role,_ in self.calls if role=='router'],['via_high','congestion_low'])
        self.assertTrue(all(s.sha256(Path(p))==h for p,h in old.items()))
        self.assertFalse((self.report/'best_config.json').exists())  # weights are not a total.

    def test_report_only_never_launches_or_rewrites_state(self):
        self.initialize();self.execute('--max-runs','1');old=s.sha256(self.report/'experiment_state.json')
        with patch.object(t,'launch',side_effect=AssertionError('Not allowed')) as launch:
            self.assertEqual(self.cli('--report-only'),0);launch.assert_not_called()
        self.assertEqual(s.sha256(self.report/'experiment_state.json'),old)

    def test_router_failure_prohibits_score_and_continues(self):
        self.initialize();self.fail_router={'baseline'}
        self.assertEqual(self.execute('--max-runs','2'),0)
        self.assertNotIn(('baseline','evaluation'),[(i,role) for i,role,_ in self.calls])
        result=s.load(self.report/'results.json');base=result['rows'][0]
        self.assertEqual(base['router_status'],'FAIL');self.assertFalse(base['eligible'])
        self.assertIsNone(result['rows'][1]['delta_wirelength_score'])

    def test_official_failure_timeout_and_missing_metrics(self):
        self.initialize();self.fail_score={'baseline'};self.timeouts={'via_low'}
        self.assertEqual(self.execute('--max-runs','2'),0)
        rows=s.load(self.report/'results.json')['rows']
        self.assertEqual(rows[0]['official_status'],'FAIL');self.assertEqual(rows[1]['official_status'],'TIMEOUT')
        self.assertFalse(any(r['eligible'] for r in rows));self.assertIsNone(rec.official_metrics({})['wirelength_score'])
        self.assertIsNone(rec.official_metrics({'score':{'weights':{'wirelength':.45}}})['total_score'])
        self.assertEqual(rec.official_metrics({'score':0})['total_score'],0)
        self.assertIsNone(rec.official_metrics({'total_score':True})['total_score'])

    def test_real_total_field_only_writes_best_with_baseline_and_two_pass(self):
        self.initialize();self.totals=True
        self.assertEqual(self.execute('--max-runs','2'),0)
        self.assertEqual(s.load(self.report/'best_config.json')['via'],.25)
        payload=s.load(self.report/'results.json')
        self.assertEqual(payload['leaders']['total_score']['ids'],['via_low'])
        self.assertEqual(payload['rows'][1]['delta_total_score'],1)

    def test_changed_tools_block_ranking_and_further_router(self):
        self.initialize();self.totals=True;self.execute('--max-runs','2')
        (self.project/'router').write_bytes(b'changed mock tool')
        self.calls.clear();self.assertEqual(self.execute('--resume'),2);self.assertEqual(self.calls,[])
        self.assertFalse(s.load(self.report/'results.json')['comparable'])
        self.assertFalse((self.report/'best_config.json').exists())
        self.assertTrue(list((self.report/'superseded').glob('*/best_config.json')))

    def test_tampered_completed_guide_blocks_resume_without_overwrite(self):
        plan,_=self.initialize();self.execute('--max-runs','1')
        state=s.load(self.report/'experiment_state.json');label=state['experiments']['baseline']['attempts'][0]['label']
        guide=self.project/'outputs/runs'/plan['case']/label/'route.guide';guide.write_text('tampered')
        self.calls.clear();self.assertEqual(self.execute('--resume'),2);self.assertEqual(self.calls,[])
        self.assertEqual(guide.read_text(),'tampered')

    def test_explicit_retry_uses_new_label_preserving_failure(self):
        self.initialize();self.fail_router={'baseline'};self.execute('--max-runs','1')
        state=s.load(self.report/'experiment_state.json');old=state['experiments']['baseline']['attempts'][0]['label']
        old_manifest=self.project/'outputs/runs/open_3d_case001'/old/'run.json';old_sha=s.sha256(old_manifest)
        self.fail_router=set();self.calls.clear();self.assertEqual(self.execute('--resume','--retry-failed','--max-runs','1'),0)
        attempts=s.load(self.report/'experiment_state.json')['experiments']['baseline']['attempts']
        self.assertEqual(len(attempts),2);self.assertNotEqual(attempts[-1]['label'],old)
        self.assertEqual(s.sha256(old_manifest),old_sha)

    def test_recover_routed_without_scoring_does_not_repeat_router(self):
        plan,state=self.initialize();row=plan['variants'][0]
        run,evaluate=s.commands(plan,row,row['first_label'])
        state['experiments']['baseline']={'state':'ROUTING','attempts':[{'label':row['first_label'],'state':'ROUTING','router_command':run,'evaluation_command':evaluate,'artifacts':{}}]}
        self.fake_launch(run,'router',plan,row,state,self.report);s.atomic(self.report/'experiment_state.json',state)
        self.calls.clear();self.assertEqual(self.execute('--resume','--max-runs','1'),0)
        self.assertEqual([(i,r) for i,r,_ in self.calls][:2],[('baseline','evaluation'),('via_low','router')])

    def test_partial_official_directory_is_never_overwritten(self):
        plan,state=self.initialize();row=plan['variants'][0];run,evaluate=s.commands(plan,row,row['first_label'])
        state['experiments']['baseline']={'state':'EVALUATING','attempts':[{'label':row['first_label'],'state':'EVALUATING','router_command':run,'evaluation_command':evaluate,'artifacts':{}}]}
        self.fake_launch(run,'router',plan,row,state,self.report)
        score=self.project/'outputs/runs'/plan['case']/row['first_label']/'official_latest';score.mkdir();(score/'partial.log').write_text('preserve')
        s.atomic(self.report/'experiment_state.json',state);self.calls.clear()
        self.assertEqual(self.execute('--resume','--max-runs','1'),0)
        self.assertNotIn(('baseline','evaluation'),[(i,role) for i,role,_ in self.calls])
        self.assertEqual((score/'partial.log').read_text(),'preserve')

    def test_output_label_collision_allocates_fresh_without_overwrite(self):
        plan,_=self.initialize();reserved=self.project/'outputs/runs'/plan['case']/plan['variants'][0]['first_label']
        reserved.mkdir(parents=True);(reserved/'marker').write_text('history')
        self.assertEqual(self.execute('--max-runs','1'),0)
        self.assertEqual((reserved/'marker').read_text(),'history')
        label=s.load(self.report/'experiment_state.json')['experiments']['baseline']['attempts'][0]['label']
        self.assertNotEqual(label,reserved.name)

    def test_cli_budget_is_distinct_from_config_budget_and_frozen(self):
        self.assertEqual(self.cli('--router-timeout','120','--dry-run'),0)
        plan=s.load(self.report/'experiment_plan.json')
        self.assertEqual(plan['baseline']['time_limit'],600);self.assertEqual(plan['router_timeout'],120)
        self.assertEqual(self.cli('--router-timeout','121','--dry-run'),2)

    def test_case005_cannot_run_before_source_attestation(self):
        self.assertEqual(self.cli('--case','open_3d_case005','--dry-run'),0)
        self.calls.clear();self.assertEqual(self.execute('--case','open_3d_case005','--max-runs','1'),2)
        self.assertEqual(self.calls,[])

    def test_process_lock_atomic_failure_and_immutable_history(self):
        target=self.here/'atomic.json';s.atomic(target,{'old':True})
        with patch.object(s.os,'replace',side_effect=OSError('injected failure')):
            with self.assertRaises(OSError):s.atomic(target,{'new':True})
        self.assertEqual(s.load(target),{'old':True});self.assertFalse(list(self.here.glob('*.tmp')))
        with self.assertRaises(s.TuningError):s.immutable(target,{'new':True})
        with s.locked(self.here/'test.lock'):
            with self.assertRaises(s.TuningError):
                with s.locked(self.here/'test.lock'):pass

    def test_figures_empty_and_observed_are_png_not_zero_filled(self):
        self.initialize();payload=s.load(self.report/'results.json')
        import plotting
        original_blas={key:os.environ.get(key) for key in ['OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']}
        images=plotting.make_figures(payload['rows'],self.report)
        self.assertEqual(original_blas,{key:os.environ.get(key) for key in original_blas})
        self.assertEqual(len(images),6)
        for name in images:
            self.assertEqual((self.report/'figures'/name).read_bytes()[:8],b'\x89PNG\r\n\x1a\n')
        self.totals=True;self.execute('--max-runs','2');payload=s.load(self.report/'results.json')
        self.assertEqual(len(plotting.make_figures(payload['rows'],self.report)),6)

    def test_baseline_zero_percent_and_pareto_conflicting_objectives(self):
        rows=[{'id':'baseline','eligible':True,'changes':{},'router_status':'ROUTED','total_score':0},
              {'id':'via_low','eligible':True,'changes':{'via':.25},'router_status':'ROUTED','total_score':1}]
        a.compare(rows);self.assertEqual(rows[1]['delta_total_score'],1);self.assertIsNone(rows[1]['percent_total_score'])
        one={'id':'one','wirelength_score':80,'congestion_score':60};two={'id':'two','wirelength_score':60,'congestion_score':80}
        candidates,dimensions=a.pareto([one,two]);self.assertEqual(set(candidates),{'one','two'});self.assertIn('wirelength_score',dimensions)

    def test_ctrl_c_persists_interrupted_without_starting_evaluator(self):
        self.initialize()
        process=Mock(pid=99999999);process.wait.side_effect=[KeyboardInterrupt(),0]
        with patch.object(t.subprocess,'Popen',return_value=process) as popen:
            self.assertEqual(self.cli('--max-runs','2'),130)
        self.assertEqual(popen.call_count,1)
        self.assertIs(popen.call_args.kwargs['shell'],False)
        state=s.load(self.report/'experiment_state.json')
        self.assertEqual(state['batch_state'],'INTERRUPTED')
        self.assertEqual(state['experiments']['baseline']['state'],'INTERRUPTED')
        self.assertEqual(state['experiments']['via_low']['state'],'PENDING')
        self.assertFalse((self.project/'outputs').exists())

    def test_live_previous_helper_blocks_resume(self):
        plan,state=self.initialize();row=plan['variants'][0]
        run,evaluate=s.commands(plan,row,row['first_label'])
        entry={'state':'ROUTING','attempts':[{'label':row['first_label'],'state':'ROUTING',
            'router_command':run,'evaluation_command':evaluate,'process_pid':os.getpid(),'artifacts':{}}]}
        state['experiments']['baseline']=entry
        self.fake_launch(run,'router',plan,row,state,self.report);s.atomic(self.report/'experiment_state.json',state)
        self.calls.clear();self.assertEqual(self.execute('--resume','--max-runs','2'),2)
        self.assertEqual(self.calls,[])
        self.assertEqual(s.load(self.report/'experiment_state.json')['experiments']['baseline']['state'],'BLOCKED')

    def test_ordinary_timeout_continues_but_unknown_infrastructure_pauses(self):
        self.initialize();self.fail_router={'baseline'}
        def fail_as(category):
            def dispatch(cmd,role,plan,row,state,report):
                rc=self.fake_launch(cmd,role,plan,row,state,report)
                if role=='router' and row['id']=='baseline':
                    label=state['experiments']['baseline']['attempts'][-1]['label']
                    file=self.project/'outputs/runs'/plan['case']/label/'run.json'
                    run=s.load(file);run['failure_category']=category;s.atomic(file,run)
                return rc
            return dispatch
        with patch.object(t,'launch',side_effect=fail_as('UNKNOWN')):
            self.assertEqual(self.cli('--max-runs','2'),2)
        self.assertEqual([(i,r) for i,r,_ in self.calls],[('baseline','router')])
        self.calls.clear()
        with patch.object(t,'launch',side_effect=fail_as('TIMEOUT')):
            self.assertEqual(self.cli('--resume','--retry-failed','--max-runs','2'),0)
        self.assertIn(('via_low','evaluation'),[(i,r) for i,r,_ in self.calls])
        self.assertEqual(s.load(self.report/'results.json')['rows'][0]['router_status'],'TIMEOUT')

    def test_foreign_history_label_and_changed_commands_are_rejected(self):
        self.initialize();self.execute('--max-runs','1')
        state=s.load(self.report/'experiment_state.json');old=copy.deepcopy(state)
        state['experiments']['baseline']['attempts'][0]['label']='v3'
        s.atomic(self.report/'experiment_state.json',state);self.calls.clear()
        self.assertEqual(self.execute('--resume'),2);self.assertEqual(self.calls,[])
        old['experiments']['baseline']['attempts'][0]['evaluation_command']+=['--unexpected']
        s.atomic(self.report/'experiment_state.json',old)
        self.assertEqual(self.execute('--resume'),2);self.assertEqual(self.calls,[])

    def test_current_report_schema_from_real_history_read_only(self):
        file=ROOT.parent/'outputs/runs/open_3d_case001/v3/official_latest/evaluation_report.json'
        if not file.is_file():
            self.skipTest('Optional local historical report is not present')
        before=s.sha256(file);raw=s.load(file);values=rec.official_metrics(raw)
        self.assertEqual(values['wirelength_score'],raw['score']['wirelength_score'])
        self.assertEqual(values['official_overflow'],raw['congestion']['total_overflow'])
        self.assertEqual(values['pearson'],raw['thermal_tsv']['correlation_above_ambient'])
        self.assertIsNone(values['total_score']);self.assertEqual(s.sha256(file),before)
        self.assertIsNone(rec.official_metrics({'total':12345})['total_score'])

    def test_external_running_router_is_retained_and_blocks_new_trials(self):
        self.initialize()
        old=self.project/'outputs/runs/open_3d_case005/ongoing/run.json'
        s.atomic(old,{'status':'RUNNING','case':'open_3d_case005','label':'ongoing'})
        checksum=s.sha256(old)
        self.assertEqual(self.execute('--max-runs','2'),2)
        self.assertEqual(self.calls,[]);self.assertEqual(s.sha256(old),checksum)

    def test_modified_completed_report_cannot_be_resealed_on_second_resume(self):
        self.initialize();self.execute('--max-runs','1')
        state=s.load(self.report/'experiment_state.json');label=state['experiments']['baseline']['attempts'][0]['label']
        score=self.project/'outputs/runs/open_3d_case001'/label/'official_latest'
        report=s.load(score/'evaluation_report.json');report['score']['wirelength_score']=99
        inv=s.load(score/'invocation.json');inv['report']=report
        s.atomic(score/'invocation.json',inv);s.atomic(score/'evaluation_report.json',report)
        self.calls.clear()
        self.assertEqual(self.execute('--resume'),2)
        self.assertEqual(self.execute('--resume'),2);self.assertEqual(self.calls,[])

    def test_live_launch_without_directory_does_not_allow_retry(self):
        plan,state=self.initialize();row=plan['variants'][0];label=row['first_label']
        run,evaluate=s.commands(plan,row,label)
        state['experiments']['baseline']={'state':'ROUTING','attempts':[{'label':label,'state':'ROUTING',
            'router_command':run,'evaluation_command':evaluate,'process_pid':os.getpid(),'artifacts':{}}]}
        s.atomic(self.report/'experiment_state.json',state)
        self.assertEqual(self.execute('--resume','--retry-failed','--max-runs','1'),2)
        self.assertEqual(self.calls,[])

if __name__=='__main__':
    unittest.main()
