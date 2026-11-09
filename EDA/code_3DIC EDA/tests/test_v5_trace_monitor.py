"""Monitor regression tests never launch an official Case or touch old runs."""
import contextlib
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from common import timed_process,sha256,catalog
from diagnostics import ResourceMonitor,utc
from v5_trace_monitor import TraceObserver,resource_tail,TraceVerificationHeartbeat
import run_v5_trace_case as runner


class MonitorTests(unittest.TestCase):
    def test_trace_verification_continues_heartbeat_after_router_exit(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            d=Path(temp);observer=TraceObserver(d,True)
            (d/'host_monitor.jsonl').write_text('{"previous":true}\n')
            monitor=TraceVerificationHeartbeat(d,observer,time.monotonic()-30,interval=.03)
            time.sleep(.12);errors=monitor.close()
            records=[json.loads(s) for s in (d/'host_monitor.jsonl').read_text(encoding='utf8').splitlines()]
            self.assertFalse(errors)
            self.assertTrue(records[0]['previous'])
            self.assertGreater(len(records),3)
            self.assertTrue(all(r['stage']=='trace_verify' and r['subprocess_status']=='EXITED' for r in records[1:]))
            self.assertTrue(all(r['elapsed_seconds']>=30 for r in records[1:]))

    def test_silent_process_remains_unknown_and_heartbeat_continues(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()) as console:
            d=Path(temp);observer=TraceObserver(d,True)
            result=timed_process([sys.executable,'-B','-c','import time;time.sleep(.65)'],d/'router.stdout.log',float('inf'),monitor_dir=d,monitor_interval=.1,stall_after=.01,monitor_observer=observer,native_resources=True,process_metrics_enabled=True)
            observer.finish()
            rows=[json.loads(line) for line in (d/'host_monitor.jsonl').read_text().splitlines()]
            self.assertEqual(result['returncode'],0)
            self.assertGreaterEqual(len(rows),4)
            self.assertTrue(all(r['progress_status']=='PROGRESS_UNKNOWN' for r in rows))
            self.assertTrue(all(r['stage']=='PROGRESS_UNKNOWN' for r in rows))
            self.assertNotIn('NO_PROGRESS',console.getvalue())
            self.assertFalse((d/'host_cancel.json').exists())
            self.assertTrue(any(r['rss_bytes'] and r['rss_bytes']>0 for r in rows))

    def test_actual_stage_logs_errors_and_all_bytes_survive_bounded_copy(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()) as console:
            d=Path(temp);observer=TraceObserver(d,True)
            self.assertEqual(observer.snapshot('RUNNING',1)['stage'],'PROGRESS_UNKNOWN')
            stage=d/'.final-router-fixture';stage.mkdir()
            raw=b'routed 200 seconds 1\n'+b'x'*150000+b'\nValueError: actual failure\n'+b'{"version":"fixture","validation":{"status":"LOCAL_PASS"},"seconds":1}\n'
            (stage/'baseline.log').write_bytes(raw)
            row=observer.snapshot('RUNNING',1)
            self.assertEqual(row['stage'],'baseline')
            self.assertEqual(row['progress_status'],'PROGRESS_UNKNOWN')
            (stage/'landing.log').write_bytes(b'INITIAL {}\n')
            (d/'router.stdout.log').write_bytes(b'STAGE landing accepted False\n')
            self.assertEqual(observer.snapshot('RUNNING',1)['stage'],'landing')
            result=observer.finish()
            self.assertFalse(result['errors'])
            self.assertEqual(result['streams'][str(stage/'baseline.log')]['copied_bytes'],len(raw))
            # Source-tagged chunks preserve every byte; originals remain exact.
            merged=(d/'router.log').read_bytes()
            self.assertIn(b'ValueError: actual failure',merged)
            self.assertIn(b'STAGE landing accepted False',merged)
            self.assertEqual((stage/'baseline.log').read_bytes(),raw)
            chunks=[json.loads(s) for s in (d/'router.log.index.jsonl').read_text(encoding='utf8').splitlines()]
            restored=b''.join(merged[r['aggregate_offset']:r['aggregate_offset']+r['length']] for r in chunks if r['source']==str(stage/'baseline.log'))
            self.assertEqual(restored,raw)
            self.assertIn('actual failure',console.getvalue())
            self.assertIn('accepted False',console.getvalue())
            self.assertIn('version=fixture validation=LOCAL_PASS',console.getvalue())

    def test_live_descendant_memory_is_included_not_only_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);marker=d/'child.pid'
            child="import os,time,sys;from pathlib import Path;data=bytearray(8*2**20);Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(.8)"
            parent="import subprocess,sys;subprocess.run([sys.executable,'-B','-c',sys.argv[1],sys.argv[2]],check=True)"
            proc=subprocess.Popen([sys.executable,'-B','-c',parent,child,str(marker)])
            try:
                deadline=time.monotonic()+5
                while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue(marker.exists())
                monitor=ResourceMonitor(d/'descendants.csv',proc.pid,process_metrics_enabled=True)
                sampled=monitor.sample();monitor.finish()
                self.assertIn(marker.read_text(),sampled['tree_pids'].split(';'))
                self.assertGreater(sampled['tree_rss_bytes'],sampled['rss_bytes']+4*2**20)
            finally:proc.wait(timeout=8)

    def test_cpu_and_memory_are_real_samples_and_old_schema_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp)
            monitor=ResourceMonitor(d/'metrics.csv',os.getpid(),process_metrics_enabled=True)
            monitor.sample()
            until=time.monotonic()+.15
            while time.monotonic()<until:sum(range(10000))
            sampled=monitor.sample();monitor.finish()
            self.assertGreater(sampled['cpu_percent'],0)
            self.assertGreater(sampled['tree_rss_bytes'],0)
            self.assertIn('one core=100%',sampled['cpu_scope'])
            old=ResourceMonitor(d/'old.csv',os.getpid());old.sample();old.finish()
            self.assertNotIn('cpu_percent',(d/'old.csv').read_text().splitlines()[0])
            with (d/'metrics.csv').open('ab') as f:f.write(b'partial,unfinished')
            self.assertEqual(resource_tail(d/'metrics.csv')['timestamp_utc'],sampled['timestamp_utc'])

    def test_observation_failure_does_not_change_child_exit(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            d=Path(temp);observer=TraceObserver(d,False)
            with patch.object(observer,'snapshot',side_effect=RuntimeError('injected observer failure')):
                result=timed_process([sys.executable,'-B','-c','print("complete")'],d/'router.stdout.log',float('inf'),monitor_dir=d,monitor_observer=observer)
            observer.finish()
            self.assertEqual(result['returncode'],0)
            self.assertTrue(result['monitor_errors'])
            self.assertIn(b'complete',(d/'router.log').read_bytes())

    def test_partial_trace_is_not_claimed_fully_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);(d/'route_trace.jsonl').write_bytes(b'{}\n');(d/'route_trace.index.sqlite').touch()
            receipt={'guide_sha256':'guide','trace_sha256':sha256(d/'route_trace.jsonl'),'counts':{'FINAL_ACCEPTED_VERIFIED':2,'PATH_PROVENANCE_LOST':1}}
            result=runner.trace_verification(receipt,d,'guide',3)
            self.assertEqual(result['status'],'VERIFIED_PARTIAL')
            self.assertEqual(runner.trace_verification(receipt,d,'another',3)['status'],'FAILED')

    def test_all_official_cases_use_same_supervised_path_without_starting_them(self):
        cases=catalog()
        for name in ('open_3d_case001','open_3d_case003','open_3d_case005'):
            self.assertIn(name,cases)
        # Exercise each case's launch preparation, but replace the process call.
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            root=Path(temp)
            (root/'v5_trace/engine').mkdir(parents=True);(root/'v5_trace/engine/final_router.py').write_text('fixture')
            for kind in ('def','lef','thermal'):(root/kind).write_text('fixture')
            entries={name:{k:str(root/k) for k in ('def','lef','thermal')} for name in cases if name in ('open_3d_case001','open_3d_case003','open_3d_case005')}
            for name in entries:
                def fake(*args,**kwargs):
                    self.assertEqual(args[2],float('inf'))
                    self.assertEqual(kwargs['monitor_interval'],5)
                    self.assertIsInstance(kwargs['monitor_observer'],TraceObserver)
                    command=' '.join(args[0])
                    self.assertIn('v5_resource_guard.py',command)
                    self.assertIn('--process-metrics',command)
                    self.assertNotIn('--development-small-budget',command)
                    return {'returncode':1,'runtime_seconds':0,'process_status':'FINISHED'}
                with patch.object(runner,'PROJECT',root),patch.object(runner,'source_identity',return_value={}),patch.object(runner,'catalog',return_value=entries),patch.object(runner,'startup_check',return_value={'allowed':True}),patch.object(runner,'HostGuard') as guard,patch.object(runner,'timed_process',side_effect=fake):
                    row=runner.execute(name,backend='wsl')
                    self.assertEqual(row['status'],'INCOMPLETE')
                    self.assertTrue(guard.return_value.start.called)


if __name__=='__main__':unittest.main()
