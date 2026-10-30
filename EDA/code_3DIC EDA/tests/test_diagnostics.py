"""Diagnostics tests use temporary processes/files, never official data or old runs."""
from pathlib import Path
import contextlib,csv,io,json,os,signal,subprocess,sys,tempfile,time,unittest
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'scripts'))
from diagnostics import classify,progress_tail,atomic_json,ResourceMonitor
from common import timed_process,catalog

class EvidenceTests(unittest.TestCase):
    def category(self,code=1,progress=None,supervisor=None,backend='wsl',status='FINISHED'):
        return classify({'process_status':status,'returncode':code},progress or {},supervisor or {},'test',backend,False)[0]
    def test_ambiguous_returns_are_not_oom(self):
        for code in [1,124,137]:self.assertEqual(self.category(code),'UNKNOWN')
    def test_router_error_timeout_allocation(self):
        for kind,expected in [('router_exception','ROUTER_ERROR'),('time_budget','TIMEOUT'),('allocation_failure','OOM_SUSPECTED')]:
            self.assertEqual(self.category(progress={'error':{'event':'error','error_kind':kind,'message':'test'}}),expected)
    def test_signal_and_shared_oom_attribution(self):
        record={'run_id':'test','router_signal':9,'router_returncode':-9}
        self.assertEqual(self.category(137,supervisor=record),'ROUTER_SIGNAL')
        record['resource_summary']={'cgroup_oom_kill_delta':1}
        self.assertEqual(self.category(137,supervisor=record),'OOM_SUSPECTED')
        record['oom_confirmed_for_pid']=True;record['kernel_oom_evidence']=['timestamped matching victim PID fixture']
        self.assertEqual(self.category(137,supervisor=record),'OOM_CONFIRMED')
        record['run_id']='another-run'
        self.assertEqual(self.category(137,supervisor=record),'UNKNOWN')
    def test_incomplete_jsonl_and_bounded_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'progress.jsonl'
            path.write_bytes((b'{"event":"old","pad":"'+b'x'*2000+b'"}\n')+b'{"event":"progress","net_index":1000}\n{"event":"error"')
            self.assertEqual(progress_tail(path,128)['last']['net_index'],1000)
    def test_mixed_raw_log_does_not_drive_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'router.log';raw='WSL localhost proxy warning\r\n'.encode('utf-16le')+b'ERROR: unrelated legacy raw message\n';p.write_bytes(raw)
            self.assertEqual(self.category(),'UNKNOWN');self.assertEqual(p.read_bytes(),raw)
    def test_host_timeout_and_no_progress(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            directory=Path(tmp)
            result=timed_process([sys.executable,'-c','import time;time.sleep(30)'],directory/'router.log',.3,monitor_dir=directory,monitor_interval=.03,stall_after=.01,native_resources=True)
            self.assertEqual(result['process_status'],'TIMEOUT');self.assertEqual(self.category(result['returncode'],status='TIMEOUT'),'TIMEOUT')
            records=[json.loads(line) for line in (directory/'host_monitor.jsonl').read_text().splitlines()]
            self.assertTrue(any(r['progress_status']=='NO_PROGRESS' for r in records))
            self.assertTrue(all(r['router_liveness'].startswith('UNKNOWN') for r in records))
    def test_launch_error_saved(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            d=Path(tmp);r=timed_process([str(d/'missing-executable')],d/'router.log',1,monitor_dir=d)
            self.assertEqual(r['process_status'],'LAUNCH_ERROR')
            self.assertTrue((d/'host_monitor.jsonl').is_file())
    def test_partial_label_cannot_be_reused(self):
        parent=PROJECT/'outputs/runs/custom';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='diagnostic_label_',dir=parent) as tmp:
            d=Path(tmp);marker=d/'progress.log';marker.write_text('keep me',encoding='utf8')
            for name in ['input.def','input.lef','thermal.json']:(d/name).write_text('temporary test input',encoding='utf8')
            args=[sys.executable,'-B',str(PROJECT/'scripts/run_case.py'),'--label',d.name,'--def',str(d/'input.def'),'--lef',str(d/'input.lef'),'--thermal-report',str(d/'thermal.json')]
            r=subprocess.run(args,capture_output=True,timeout=10)
            self.assertNotEqual(r.returncode,0);self.assertEqual(marker.read_text(),'keep me')

class RouterProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe=Path(os.environ.get('ROUTER_EXE',str(PROJECT/'outputs/router.exe' if os.name=='nt' else PROJECT/'router')))
        if not cls.exe.is_file():raise unittest.SkipTest('Build native router first or set ROUTER_EXE')
    def setUp(self):
        parent=PROJECT/'outputs/tests';parent.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='monitor_',dir=parent);self.d=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def invoke(self,*extra):
        c=catalog()['toy']
        args=[str(self.exe),'--def',c['def'],'--lef',c['lef'],'--thermal-report',c['thermal'],'--output',str(self.d/'route.guide'),'--progress-dir',str(self.d/'logs'),*extra]
        env=os.environ.copy();env.pop('GR_EXTERNAL_RESOURCE_MONITOR',None)
        run=subprocess.run(args,capture_output=True,timeout=30,env=env)
        events=[json.loads(line) for line in (self.d/'logs/progress.jsonl').read_text(encoding='utf8').splitlines()]
        return run,events
    def test_no_trace_stages_and_direct_resource_samples(self):
        run,events=self.invoke('--iterations','0')
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertFalse((self.d/'trace.jsonl').exists())
        for phase in ['RUN','ARGUMENTS','PARSE_LEF','PARSE_DEF','GRAPH_BUILD','THERMAL','FIXED_DEMAND','ROUTING','MST','A_STAR','TSV_SELECTION','WRITE_GUIDE']:
            for event in ['phase_started','phase_completed']:
                self.assertTrue(any(e['event']==event and e['phase']==phase for e in events),(phase,event))
        self.assertEqual(events[-1]['event'],'complete');self.assertEqual(events[-1]['progress_percent'],100)
        with (self.d/'logs/resource_monitor.csv').open(encoding='utf8') as f:rows=list(csv.DictReader(f))
        self.assertTrue(rows)
        if sys.platform.startswith('linux'):
            self.assertGreater(float(rows[0]['rss_bytes']),0);self.assertGreater(float(rows[0]['mem_available_bytes']),0)
        else:self.assertEqual(rows[0]['mem_available_bytes'],'N/A')
    def test_error_retains_inner_phase_and_net(self):
        config=self.d/'tiny.json';config.write_text('{"search_limit":1}',encoding='utf8')
        run,events=self.invoke('--config',str(config));self.assertEqual(run.returncode,1)
        error=events[-1];self.assertEqual(error['event'],'error');self.assertEqual(error['phase'],'A_STAR')
        self.assertGreater(error['net_index'],0);self.assertTrue(error['net']);self.assertEqual(error['error_kind'],'router_exception')
        self.assertFalse((self.d/'route.guide').exists())
    def test_router_budget_has_typed_evidence(self):
        run,events=self.invoke('--time-limit','0.000001');self.assertEqual(run.returncode,1)
        self.assertEqual(events[-1]['error_kind'],'time_budget')
    def test_rollback_progress_completes_last_net(self):
        config=self.d/'rollback.json';config.write_text('{"congestion":0,"history":0,"iterations":1}',encoding='utf8')
        run,events=self.invoke('--config',str(config));self.assertEqual(run.returncode,0,run.stderr)
        rollback=[e for e in events if e['phase']=='ROLLBACK' and e['event']=='phase_completed']
        self.assertTrue(rollback);self.assertEqual(rollback[-1]['progress_percent'],100)
        self.assertEqual(events[-1]['progress_percent'],100)

@unittest.skipUnless(sys.platform.startswith('linux'),'requires actual Linux waitpid/proc/timeout')
class LinuxSupervisionTests(unittest.TestCase):
    def test_sampling_exception_still_records_reaped_router_exit(self):
        # Exercise the supervisor's cleanup path without real OOM/WSL failure.
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            script="""import sys
from pathlib import Path
import linux_supervisor as s
class BrokenMonitor:
    def __init__(self,*args):pass
    def sample(self):raise RuntimeError('injected sampler failure')
    def finish(self):return {}
s.ResourceMonitor=BrokenMonitor
raise SystemExit(s.main())
"""
            env=os.environ.copy();env['PYTHONPATH']=str(PROJECT/'scripts')
            run=subprocess.run([sys.executable,'-B','-c',script,'--directory',str(d),'--run-id','test','--',sys.executable,'-c','import time;time.sleep(30)'],env=env,capture_output=True,timeout=10)
            record=json.loads((d/'router_exit.json').read_text())
            self.assertIn('supervisor_error',record)
            self.assertEqual(record['router_signal'],15);self.assertEqual(record['router_returncode'],-15)
            self.assertEqual(run.returncode,143)
            self.assertFalse(Path('/proc',str(record['router_pid'])).exists())
    def supervised(self,code,timeout=False,lease=60,kind='router'):
        with tempfile.TemporaryDirectory(prefix='router_monitor_') as tmp:
            d=Path(tmp);atomic_json(d/'host_heartbeat.json',{'alive':True})
            args=[sys.executable,str(PROJECT/'scripts/linux_supervisor.py'),'--directory',str(d),'--run-id','test','--interval','.05','--lease-seconds',str(lease),'--process-kind',kind,'--',sys.executable,'-c',code]
            if timeout:args=['timeout','--signal=TERM','--kill-after=5s','.4s',*args]
            run=subprocess.run(args,capture_output=True,timeout=10)
            deadline=time.monotonic()+3
            while not (d/(kind+'_exit.json')).exists() and time.monotonic()<deadline:time.sleep(.05)
            record=json.loads((d/(kind+'_exit.json')).read_text())
            self.assertTrue((d/'resource_monitor.csv').is_file())
            return run,record
    def test_real_sigkill_is_not_automatically_oom(self):
        run,record=self.supervised('import os,signal;os.kill(os.getpid(),signal.SIGKILL)')
        self.assertEqual(record['router_signal'],9);self.assertEqual(record['router_returncode'],-9)
        self.assertFalse(record.get('oom_confirmed_for_pid',False))
        self.assertEqual(run.returncode,137)
    def test_gnu_timeout_has_signal_provenance(self):
        run,record=self.supervised('import time;time.sleep(30)',timeout=True)
        self.assertEqual(run.returncode,124);self.assertEqual(record['termination_reason'],'wrapper_signal')
        category,_=classify({'process_status':'FINISHED','returncode':124},{},record,'test','native',False)
        self.assertEqual(category,'TIMEOUT')
    def test_evaluator_gnu_timeout_signal_provenance(self):
        from evaluator_diagnostics import classify_evaluator
        run,record=self.supervised('import time;time.sleep(30)',timeout=True,kind='evaluator')
        self.assertEqual(record['evaluator_signal'],15)
        self.assertEqual(classify_evaluator({'process_status':'FINISHED','returncode':run.returncode},record,'test',{},'MISSING','native')[0],'TIMEOUT')
    def test_proc_metrics_and_cgroup_unknowns(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor=ResourceMonitor(Path(tmp)/'resource.csv',os.getpid());row=monitor.sample();monitor.finish()
            self.assertGreater(row['rss_bytes'],0);self.assertIsNotNone(row['mem_available_bytes']);self.assertIsNotNone(row['swap_free_bytes'])
    def test_unresponsive_child_reaped_before_outer_kill(self):
        run,record=self.supervised('import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)',timeout=True)
        self.assertEqual(run.returncode,124)
        self.assertEqual(record['router_signal'],9)
        self.assertEqual(record['termination_reason'],'wrapper_signal')
        self.assertFalse(Path('/proc',str(record['router_pid'])).exists())
    def test_expired_host_lease_cleans_router_without_claiming_wsl_failure(self):
        run,record=self.supervised('import time;time.sleep(30)',lease=.3)
        self.assertEqual(record['termination_reason'],'host_lease_expired')
        self.assertFalse(Path('/proc',str(record['router_pid'])).exists())
        category,_=classify({'process_status':'FINISHED','returncode':run.returncode},{},record,'test','wsl',False)
        self.assertEqual(category,'UNKNOWN')
class EvaluatorEvidenceTests(unittest.TestCase):
    def test_descendant_rss_is_not_the_two_megabyte_launcher(self):
        from unittest import mock
        import diagnostics as d
        def numbers(path):return {'VmRSS':2048 if Path(path).parent.name=='1' else 9000000}
        def children(path,*args,**kwargs):return '2' if path.parent.name=='1' else ''
        with mock.patch.object(d,'_numbers',numbers),mock.patch.object(Path,'read_text',children):
            result=d.process_tree_memory(1)
        self.assertEqual(result['tree_rss_bytes'],9002048)
        self.assertEqual(result['tree_pids'],'1;2')
        self.assertFalse(result['tree_truncated'])

    def test_host_commit_snapshot_is_optional_and_nonnegative(self):
        from diagnostics import windows_memory
        data=windows_memory()
        self.assertTrue(all(isinstance(x,int) and x>=0 for x in data.values()))
        if data:self.assertLessEqual(data['commit_total_bytes'],data['commit_limit_bytes'])

    def test_evaluator_categories_require_process_evidence(self):
        from evaluator_diagnostics import classify_evaluator
        base={'run_id':'test','evaluator_returncode':0}
        host={'process_status':'FINISHED','returncode':0}
        def category(record=None,report=None,state='MISSING',raw=None,after=None):
            return classify_evaluator(raw or host,record or {},'test',report or {},state,'wsl',after)[0]
        for rc in (1,124,137):
            self.assertEqual(category(raw={**host,'returncode':rc}),'UNKNOWN')
        self.assertEqual(category(base),'REPORT_MISSING')
        self.assertEqual(category(base,state='INVALID'),'REPORT_INVALID')
        self.assertEqual(category(base,{'status':'PASS'},'PRESENT'),'SUCCESS')
        self.assertEqual(category({**base,'evaluator_returncode':1}),'EVALUATOR_EXIT_ERROR')
        self.assertEqual(category({**base,'evaluator_returncode':1},{'status':'FAIL'},'PRESENT'),'OFFICIAL_FAIL')
        for sig in (9,15,11):
            r={**base,'evaluator_returncode':-sig,'evaluator_signal':sig}
            self.assertEqual(category(r),'EVALUATOR_SIGNAL')
        self.assertEqual(category({**base,'evaluator_returncode':-9,'evaluator_signal':9,'resource_summary':{'cgroup_oom_kill_delta':1}}),'OOM_SUSPECTED')
        self.assertEqual(category({**base,'oom_confirmed_for_pid':True}),'OOM_CONFIRMED')
        self.assertEqual(category({**base,'run_id':'old'}),'UNKNOWN')
        self.assertEqual(category({**base,'evaluator_returncode':None,'boot_id':'before'},after={'boot_id':'after'}),'WSL_RESTART_OBSERVED')
        self.assertEqual(category(base,raw={**host,'returncode':1}),'WSL_EXIT_ERROR')
        self.assertEqual(category(raw={**host,'process_status':'TIMEOUT'}),'TIMEOUT')
        self.assertEqual(category(raw={**host,'process_status':'INTERRUPTED'}),'INTERRUPTED')
        self.assertEqual(category(raw={**host,'process_status':'LAUNCH_ERROR'}),'LAUNCH_ERROR')
        r={**base,'evaluator_returncode':-15,'evaluator_signal':15,'termination_reason':'wrapper_signal','termination_requested_signal':15}
        self.assertEqual(category(r,raw={**host,'returncode':124}),'TIMEOUT')

    def test_bounded_mixed_log_and_report_parse(self):
        from evaluator_diagnostics import log_tail,read_report
        with tempfile.TemporaryDirectory(dir=PROJECT) as tmp:
            d=Path(tmp);p=d/'mixed.log'
            raw='wsl: \u8b66\u544a proxy\r\n'.encode('utf-16le')+'\u8bc4\u5206 congestion\n'.encode()
            p.write_bytes(raw)
            text=log_tail(p)
            self.assertIn('wsl: \u8b66\u544a proxy',text);self.assertIn('\u8bc4\u5206 congestion',text)
            self.assertNotIn('\x00',text);self.assertEqual(p.read_bytes(),raw)
            p.write_bytes(b'x'*100000+b'tail');self.assertEqual(log_tail(p,16),'x'*12+'tail')
            report=d/'report.json';self.assertEqual(read_report(report)[1],'MISSING')
            for data in ('{','[]','{}'):
                report.write_text(data);self.assertTrue(read_report(report)[1].startswith('INVALID'))

    def test_vm_loss_recovers_only_flushed_resource_samples(self):
        from evaluator_diagnostics import resource_tail
        with tempfile.TemporaryDirectory(dir=PROJECT) as tmp:
            p=Path(tmp)/'resource.csv'
            p.write_bytes(b'pid,process_peak_rss_bytes,source\n'+b'42,123,proc_evaluator_pid\n'*10000+b'42,456,proc_evaluator_pid\n42,999999,proc_eval')
            data=resource_tail(p,1024)
            self.assertEqual(data['observed_peak_rss_bytes'],456)
            self.assertEqual(data['last_sample']['pid'],'42')


class EvaluatorWorkflowTests(unittest.TestCase):
    """Synthetic evaluator only. Every fixture/attempt belongs to TemporaryDirectory."""
    def setUp(self):
        from unittest import mock
        import evaluate
        self.e=evaluate;self.mock=mock
        self.tmp=tempfile.TemporaryDirectory(prefix='evaluator_verify_',dir=PROJECT)
        self.addCleanup(self.tmp.cleanup)
        self.d=Path(self.tmp.name)/'\u6d4b\u8bd5 path';self.d.mkdir()
        self.target=self.d/'run';self.target.mkdir()
        self.guide=self.target/'route.guide';self.guide.write_text('synthetic guide; not an official result')
        (self.target/'result.json').write_text('{"fixture":true}')
        inputs={}
        for key in ('def','lef','thermal'):
            path=self.d/('input.'+key);path.write_text('synthetic '+key);inputs[key]=str(path)
        from common import sha256
        self.run={'status':'ROUTED','returncode':0,'guide':str(self.guide),'inputs':inputs,
                  'input_sha256':{k:sha256(v) for k,v in inputs.items()}}
        atomic_json(self.target/'run.json',self.run)
        self.old=self.target/'official_latest';self.old.mkdir();(self.old/'marker').write_text('historical failure: keep')
        self.protected={p:p.read_bytes() for p in self.target.rglob('*') if p.is_file()}
        tool=self.d/'\u8bc4\u4f30\u7a0b\u5e8f';tool.mkdir()
        for name in ('score','POST9.dat','POWV9.dat'):(tool/name).write_text('synthetic evaluator')
        for name,value in [('EDA',self.d),('run_dir',lambda *args:self.target),('existing_run_dir',lambda *args:self.target),('wsl_snapshot',lambda:{}),('windows_events',lambda *args:{})]:
            patch=mock.patch.object(evaluate,name,value);patch.start();self.addCleanup(patch.stop)
        self.output=io.StringIO()

    def tearDown(self):
        for path,data in self.protected.items():self.assertEqual(path.read_bytes(),data,str(path))

    def arguments(self,attempt='diagnostic',backend='wsl'):
        return ['--case','toy','--label','existing','--backend',backend,'--timeout','3','--attempt',attempt]

    def invoke_mock(self,report=None,code=0,score_code=0,exit_record=True):
        def process(cmd,log,timeout,**kwargs):
            directory=Path(log).parent;run_id=json.loads((directory/'invocation.json').read_text(encoding='utf8'))['run_id']
            Path(log).write_bytes(b'mock evaluator, not an official score\n')
            (directory/'resource_monitor.csv').write_text('pid,process_peak_rss_bytes,source\n42,789,proc_evaluator_pid\n',encoding='utf8')
            if report is not None:atomic_json(directory/'evaluation_report.json',report)
            if exit_record:atomic_json(directory/'evaluator_exit.json',{'run_id':run_id,'evaluator_returncode':score_code,'child_peak_rss_bytes':12345})
            self.assertIn('--process-kind evaluator',cmd[-1])
            self.assertIn("'",cmd[-1]);self.assertEqual(kwargs['process_role'],'evaluator')
            return {'process_status':'FINISHED','returncode':code}
        with self.mock.patch.object(self.e,'timed_process',process),contextlib.redirect_stdout(self.output):
            rc=self.e.main(self.arguments())
        return rc,json.loads((self.target/'official_latest_attempts/diagnostic/invocation.json').read_text(encoding='utf8'))

    def test_mock_pass_and_immutable_history(self):
        rc,inv=self.invoke_mock({'status':'PASS'})
        self.assertEqual(rc,0);self.assertEqual(inv['status'],'PASS');self.assertEqual(inv['peak_memory_bytes'],12345)
        self.assertEqual(inv['evaluator_returncode'],0);self.assertEqual(inv['returncode_layer'],'wsl.exe')

    def test_mock_official_fail_requires_report(self):
        rc,inv=self.invoke_mock({'status':'FAIL'},1,1)
        self.assertEqual(rc,1);self.assertEqual(inv['official_status'],'FAIL');self.assertEqual(inv['failure_category'],'OFFICIAL_FAIL')

    def test_mock_transport_loss_is_not_official_fail(self):
        rc,inv=self.invoke_mock(code=1,exit_record=False)
        self.assertEqual(rc,1);self.assertEqual(inv['status'],'ERROR');self.assertEqual(inv['official_status'],'NOT_EVALUATED')
        self.assertEqual(inv['failure_category'],'UNKNOWN');self.assertEqual(inv['report'],{})
        self.assertEqual(inv['peak_memory_bytes'],789);self.assertIn('lower bound',inv['peak_memory_source'])

    def test_raw_wsl_exit_is_flushed_before_diagnostic_probe(self):
        checked=[]
        def probe():
            row=json.loads((self.target/'official_latest_attempts/diagnostic/invocation.json').read_bytes())
            self.assertEqual(row['returncode'],1);self.assertTrue(row['diagnostics_pending']);checked.append(True)
            return {}
        with self.mock.patch.object(self.e,'wsl_snapshot',probe):self.invoke_mock(code=1,exit_record=False)
        self.assertTrue(checked)

    def test_missing_report_after_clean_exit(self):
        rc,inv=self.invoke_mock()
        self.assertEqual(inv['failure_category'],'REPORT_MISSING');self.assertEqual(inv['status'],'ERROR')

    def test_existing_attempt_and_default_directory_refused(self):
        self.invoke_mock({'status':'PASS'})
        before=(self.target/'official_latest_attempts/diagnostic/invocation.json').read_bytes()
        for args in (self.arguments(),self.arguments()[:-2]):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:self.e.main(args)
            self.assertEqual(error.exception.code,2)
        self.assertEqual((self.target/'official_latest_attempts/diagnostic/invocation.json').read_bytes(),before)

    def test_changed_input_refused_before_attempt_created(self):
        Path(self.run['inputs']['def']).write_text('changed')
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):self.e.main(self.arguments())
        self.assertFalse((self.target/'official_latest_attempts').exists())

    def test_ui_reads_verdict_without_mutating_manifest(self):
        import importlib.util
        sys.path.insert(0,str(PROJECT/'ui'))
        spec=importlib.util.spec_from_file_location('evaluator_ui_compat',PROJECT/'ui/app.py')
        ui=importlib.util.module_from_spec(spec);spec.loader.exec_module(ui)
        root=self.d/'ui_outputs';d=root/'runs/toy/existing';d.mkdir(parents=True)
        manifest=d/'run.json'
        atomic_json(manifest,{**self.run,'case':'toy','label':'existing','official_status':'NOT_EVALUATED'})
        before=manifest.read_bytes();(d/'official_latest').mkdir()
        atomic_json(d/'official_latest/invocation.json',{'status':'PASS','report':{'status':'PASS'}})
        with self.mock.patch.object(ui,'OUTPUTS',root):
            self.assertEqual(ui.runs()['toy/existing']['official_status'],'PASS')
            self.assertEqual(ui.attach_reports({},d)['run']['official_status'],'PASS')
        self.assertEqual(manifest.read_bytes(),before)

    @unittest.skipUnless(os.name=='nt' and os.environ.get('RUN_WSL_EVALUATOR_TESTS')=='1','opt-in Windows -> WSL mock integration')
    def test_windows_to_wsl_unicode_paths_and_real_supervision(self):
        tool=self.d/'\u8bc4\u4f30\u7a0b\u5e8f/score'
        tool.write_bytes(b'#!/usr/bin/python3\nimport sys,time\nfrom pathlib import Path\n'
                         b'out=sys.argv[sys.argv.index("--output-dir")+1]\n'
                         b'print("synthetic evaluator only",flush=True)\ntime.sleep(.4)\n'
                         b'Path(out).joinpath("evaluation_report.json").write_text(\'{"status":"PASS"}\')\n')
        with contextlib.redirect_stdout(self.output):rc=self.e.main(self.arguments('wsl_real'))
        directory=self.target/'official_latest_attempts/wsl_real'
        inv=json.loads((directory/'invocation.json').read_text(encoding='utf8'))
        self.assertEqual(rc,0,inv);self.assertEqual(inv['evaluator_returncode'],0)
        self.assertEqual(inv['failure_category'],'SUCCESS');self.assertGreater(inv['peak_memory_bytes'],0)
        self.assertTrue(inv['supervisor']['boot_id']);self.assertGreater((directory/'resource_monitor.csv').stat().st_size,0)
        records=[json.loads(line) for line in (directory/'host_monitor.jsonl').read_text(encoding='utf8').splitlines()]
        self.assertTrue(all(row['process_role']=='evaluator' for row in records))

    @unittest.skipUnless(sys.platform.startswith('linux'),'actual Linux evaluator subprocess chain')
    def test_real_linux_mock_signals_timeout_and_resource_collection(self):
        tool=self.d/'\u8bc4\u4f30\u7a0b\u5e8f/score'
        scenarios=[('pass',"Path(out).joinpath('evaluation_report.json').write_text('{\"status\":\"PASS\"}')",'SUCCESS',0),
                   ('fail',"Path(out).joinpath('evaluation_report.json').write_text('{\"status\":\"FAIL\"}');sys.exit(1)",'OFFICIAL_FAIL',1),
                   ('nonzero','sys.exit(23)','EVALUATOR_EXIT_ERROR',23),
                   ('term','os.kill(os.getpid(),signal.SIGTERM)','EVALUATOR_SIGNAL',-15),
                   ('kill','os.kill(os.getpid(),signal.SIGKILL)','EVALUATOR_SIGNAL',-9),
                   ('missing','pass','REPORT_MISSING',0),
                   ('timeout','time.sleep(30)','TIMEOUT',-15),
                   ('forced','signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)','TIMEOUT',-9)]
        for name,body,category,score_code in scenarios:
            with self.subTest(name=name):
                tool.write_text('#!'+sys.executable+'\nimport os,signal,sys,time\nfrom pathlib import Path\nout=sys.argv[sys.argv.index("--output-dir")+1]\n'+body+'\n')
                tool.chmod(0o700)
                args=self.arguments(name,'native')
                args[args.index('--timeout')+1]='.8' if name in ('timeout','forced') else '5'
                with contextlib.redirect_stdout(self.output):rc=self.e.main(args)
                directory=self.target/'official_latest_attempts'/name
                inv=json.loads((directory/'invocation.json').read_text(encoding='utf8'))
                self.assertEqual(inv['failure_category'],category,inv)
                self.assertEqual(inv['evaluator_returncode'],score_code,inv)
                self.assertEqual(rc,0 if name=='pass' else 1)
                if category=='TIMEOUT':self.assertEqual(inv['process_status'],'TIMEOUT')
                self.assertGreater(inv['peak_memory_bytes'],0)
                self.assertTrue(inv['supervisor']['boot_id'])
                self.assertFalse(Path('/proc',str(inv['supervisor']['evaluator_pid'])).exists())
                self.assertGreater((directory/'resource_monitor.csv').stat().st_size,0)
                self.assertGreater((directory/'host_monitor.jsonl').stat().st_size,0)
                self.assertGreater((directory/'resource_usage.txt').stat().st_size,0)

if __name__=='__main__':unittest.main()
