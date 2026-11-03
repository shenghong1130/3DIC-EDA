"""Resource/identity/conflict guards use temporary files and Mock subprocesses."""
import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import validate_v4 as v
import research_v4 as research
import report_v4 as report
from common import sha256

class V4SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='v4 workflow space ');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.reports=self.root/'outputs/reports/v4';self.reports.mkdir(parents=True)
        for d in ['build','configs','inputs']:(self.root/d).mkdir()
        (self.root/'build/router_v4').write_bytes(b'mock router');(self.root/'configs/v4.json').write_text('{}')
        self.inputs={k:self.root/'inputs'/k for k in ['def','lef','thermal']}
        for k,p in self.inputs.items():p.write_text('mock '+k)
        self.case='open_3d_case005';self.label='case005_v4'
        old=self.root/'outputs/runs'/self.case/'case005_v3_6';old.mkdir(parents=True)
        (old/'run.json').write_text(json.dumps({'input_sha256':{k:sha256(p) for k,p in self.inputs.items()}}))
        self.build={'sha256':sha256(self.root/'build/router_v4'),'config_sha256':sha256(self.root/'configs/v4.json')};self.save()
    def save(self):
        (self.reports/'build.json').write_text(json.dumps(self.build))
    def formal(self,commit=5*2**30):
        with patch.object(v,'PROJECT',self.root),patch.object(v,'ROOT',self.reports),patch.object(v,'catalog',return_value={self.case:self.inputs}),patch.object(v,'windows_memory',return_value={'commit_available_bytes':commit}),patch.object(v.subprocess,'run',side_effect=AssertionError('No WSL process may start in this guard test')),patch.object(v,'launch',side_effect=AssertionError('No Router/scorer may start')):
            v.formal(self.case)
    def test_low_commit_records_not_started_and_preserves_label(self):
        with self.assertRaisesRegex(ValueError,'Commit headroom'):self.formal()
        self.assertFalse((self.root/'outputs/runs'/self.case/self.label).exists())
        receipt=json.loads((self.reports/'case005_execution/resource_gate.json').read_text())
        self.assertEqual(receipt['status'],'NOT_STARTED_RESOURCE_GATE')
    def test_changed_binary_refused_before_wsl(self):
        (self.root/'build/router_v4').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'identity mismatch'):self.formal(20*2**30)
    def test_changed_config_refused_before_wsl(self):
        (self.root/'configs/v4.json').write_text('{"wire":2}')
        with self.assertRaisesRegex(ValueError,'identity mismatch'):self.formal(20*2**30)
    def test_changed_input_refused_before_wsl(self):
        self.inputs['thermal'].write_text('changed')
        with self.assertRaisesRegex(ValueError,'Inputs differ'):self.formal(20*2**30)
    def test_existing_binary_is_never_recompiled_over(self):
        with self.assertRaisesRegex(ValueError,'Binary exists'):
            v.compile_binary(self.root/'build/router_v4',[],self.reports/'should_not_exist.log')
        self.assertEqual((self.root/'build/router_v4').read_bytes(),b'mock router')
    def test_failed_quality_gate_cannot_package(self):
        with patch.object(report,'PROJECT',self.root),self.assertRaisesRegex(ValueError,'Quality gate'):
            report.package({'local_quality_gate_passed':False})
        self.assertFalse((self.root/'outputs/submissions').exists())
    def test_streaming_nets_skip_large_unrelated_unicode_container(self):
        path=self.root/'result.json';nets=[{'net':'a','nodes':[1,2]},{'net':'b','pins':[]}]
        path.write_text(json.dumps({'unrelated':['\u5c42'*700000],'nets':nets},ensure_ascii=False),encoding='utf8')
        self.assertEqual(list(research.members(path)),nets)
    def test_truncated_net_does_not_yield_fabricated_record(self):
        path=self.root/'truncated.json';path.write_text('{"nets":[{"net":"a"')
        with self.assertRaises(ValueError):list(research.members(path))

if __name__=='__main__':unittest.main()
