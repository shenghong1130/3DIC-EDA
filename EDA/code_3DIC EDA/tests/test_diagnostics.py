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
    def supervised(self,code,timeout=False,lease=60):
        with tempfile.TemporaryDirectory(prefix='router_monitor_') as tmp:
            d=Path(tmp);atomic_json(d/'host_heartbeat.json',{'alive':True})
            args=[sys.executable,str(PROJECT/'scripts/linux_supervisor.py'),'--directory',str(d),'--run-id','test','--interval','.05','--lease-seconds',str(lease),'--',sys.executable,'-c',code]
            if timeout:args=['timeout','--signal=TERM','--kill-after=5s','.4s',*args]
            run=subprocess.run(args,capture_output=True,timeout=10)
            deadline=time.monotonic()+3
            while not (d/'router_exit.json').exists() and time.monotonic()<deadline:time.sleep(.05)
            record=json.loads((d/'router_exit.json').read_text())
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
if __name__=='__main__':unittest.main()
