"""Adapter-only tests. Never launch or alter the original algorithm."""
import io
import json
import sys
import subprocess
import time
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import import_v5
import run_v5_case
from v5_resource_guard import host_pressure, linux_pressure, HostGuard, MIB
from types import SimpleNamespace


class V5Integration(unittest.TestCase):
    def test_79_original_files(self):
        self.assertEqual(run_v5_case.verify()['file_count'],79)

    def test_archive_rejection(self):
        for name,mode in [('../x',0),('/x',0),('C:/x',0),('x\\y',0),('x',0o120777),('NUL.txt',0),('dir/x.',0)]:
            with self.subTest(name=name):
                b=io.BytesIO()
                with zipfile.ZipFile(b,'w') as z:
                    item=zipfile.ZipInfo(name);item.filename=name;item.external_attr=mode<<16;z.writestr(item,b'x')
                with zipfile.ZipFile(io.BytesIO(b.getvalue())) as z:
                    with self.assertRaises(ValueError):import_v5.checked_members(z)

    def test_existing_run_protected_on_block(self):
        with tempfile.TemporaryDirectory(prefix='v5_adapter_test_') as temp:
            root=Path(temp).resolve();old=root/'outputs/runs/toy/toy_v5';old.mkdir(parents=True)
            (old/'route.guide').write_bytes(b'original evidence')
            inputs={}
            for name in ['def','lef','thermal']:
                p=root/name;p.write_bytes(name.encode());inputs[name]=str(p)
            with patch.object(run_v5_case,'PROJECT',root),patch.object(run_v5_case,'REPORTS',root/'reports'),patch.object(run_v5_case,'catalog',return_value={'toy':inputs}),patch.object(run_v5_case,'resource_check',return_value={'allowed':False}):
                record=run_v5_case.execute('toy','toy_v5')
            self.assertEqual(record['status'],'BLOCKED_RESOURCE')
            self.assertEqual(record['label'],'toy_v5_run2')
            self.assertEqual((old/'route.guide').read_bytes(),b'original evidence')
            self.assertFalse((old.parent/'toy_v5_run2').exists())

    def test_force_only_bypasses_reserve(self):
        linux={'mem_available_bytes':3*2**30,'swap_free_bytes':6*2**30,'busy':[]}
        def gate(disk=30*2**30,busy=False,force=True):
            row={**linux,'busy':['router'] if busy else []}
            response=SimpleNamespace(returncode=0,stdout=json.dumps(row).encode(),stderr=b'')
            # Exercise platform-independent Linux gate without starting WSL.
            with patch.object(run_v5_case.os,'name','posix'),patch.object(run_v5_case,'backend_command',return_value=['probe']),patch.object(run_v5_case.subprocess,'run',return_value=response),patch.object(run_v5_case.shutil,'disk_usage',return_value=SimpleNamespace(free=disk)):
                return run_v5_case.resource_check('open_3d_case003','native',force)['allowed']
        self.assertFalse(gate(force=False));self.assertTrue(gate())
        self.assertFalse(gate(disk=2**30));self.assertFalse(gate(busy=True))
        linux.update(mem_available_bytes=128*MIB,swap_free_bytes=128*MIB)
        self.assertFalse(gate())

    def test_memory_pressure_never_ignored(self):
        self.assertIsNone(host_pressure({'commit_available_bytes':4*2**30,'physical_available_bytes':2*2**30}))
        self.assertEqual(host_pressure({'commit_available_bytes':256*MIB,'physical_available_bytes':2**30}),'CRITICAL_WINDOWS_COMMIT')
        self.assertEqual(host_pressure({}),'HOST_MEMORY_UNAVAILABLE')
        self.assertEqual(linux_pressure({'mem_available_bytes':128*MIB,'swap_free_bytes':128*MIB}),'CRITICAL_LINUX_RAM_SWAP')
        self.assertIsNone(linux_pressure({'mem_available_bytes':128*MIB,'swap_free_bytes':2**30}))
        self.assertEqual(linux_pressure({'mem_available_bytes':2**30,'swap_free_bytes':2**30,'cgroup_limit_bytes':2**30,'cgroup_current_bytes':1000*MIB}),'CRITICAL_CGROUP_MEMORY')

    def test_host_guard_emits_scoped_cancel(self):
        with tempfile.TemporaryDirectory(prefix='v5_guard_host_') as temp:
            d=Path(temp)
            with patch('v5_resource_guard.windows_memory',return_value={'commit_available_bytes':256*MIB,'physical_available_bytes':2**30}):
                HostGuard(d).watch()
            self.assertEqual(json.loads((d/'host_cancel.json').read_text())['reason'],'CRITICAL_WINDOWS_COMMIT')
            self.assertTrue((d/'v5_safety_stop.json').exists())

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux supervisor integration')
    def test_supervisor_exit_and_nested_session_cleanup(self):
        supervisor=run_v5_case.PROJECT/'scripts/v5_resource_guard.py'
        with tempfile.TemporaryDirectory(prefix='v5_guard_test_') as temp:
            root=Path(temp)
            for expected in (0,7):
                d=root/str(expected);d.mkdir();(d/'host_heartbeat.json').write_text('{}')
                result=subprocess.run([sys.executable,'-B',str(supervisor),'--directory',str(d),'--run-id','test','--',sys.executable,'-c',f'raise SystemExit({expected})'],timeout=10)
                self.assertEqual(result.returncode,expected)
                receipt=json.loads((d/'router_exit.json').read_text())
                self.assertEqual(receipt['router_returncode'],expected)
                self.assertIsNone(receipt['termination_reason'])
            d=root/'cancel';d.mkdir();(d/'host_heartbeat.json').write_text('{}')
            child_code="import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)"
            proc=subprocess.Popen([sys.executable,'-B',str(supervisor),'--directory',str(d),'--run-id','test','--',sys.executable,'-c',child_code,str(d/'nested.pid')])
            try:
                deadline=time.monotonic()+5
                while not (d/'nested.pid').exists() and time.monotonic()<deadline:time.sleep(.05)
                self.assertTrue((d/'nested.pid').exists())
                pid=int((d/'nested.pid').read_text())
                (d/'host_cancel.json').write_text('{"reason":"TEST_MEMORY_STOP"}')
                self.assertEqual(proc.wait(timeout=10),2)
                self.assertFalse(Path('/proc',str(pid)).exists())
                receipt=json.loads((d/'router_exit.json').read_text())
                self.assertEqual(receipt['termination_reason'],'TEST_MEMORY_STOP')
                self.assertGreater(receipt['child_peak_rss_bytes'],0)
            finally:
                if proc.poll() is None:
                    proc.terminate();proc.wait(timeout=10)

    def test_scorer_budget_still_enforced(self):
        from score_v5_case import scoring_resources_available
        check={'allowed':True,'linux':{'mem_available_bytes':8*2**30}}
        self.assertTrue(scoring_resources_available(check,{'commit_available_bytes':5*2**30},3072))
        self.assertFalse(scoring_resources_available(check,{'commit_available_bytes':3*2**30},3072))
        check['allowed']=False
        self.assertFalse(scoring_resources_available(check,{'commit_available_bytes':5*2**30},3072))
        check={'allowed':True,'linux':{'mem_available_bytes':2*2**30}}
        self.assertFalse(scoring_resources_available(check,{'commit_available_bytes':5*2**30},3072))

    def test_no_config_or_budget_cli(self):
        source=(run_v5_case.PROJECT/'scripts/run_v5_case.py').read_text(encoding='utf8')
        self.assertNotIn("add_argument('--config'",source)
        self.assertNotIn("add_argument('--time-limit'",source)
        self.assertNotIn('--development-small-budget',source)


if __name__=='__main__':unittest.main()
