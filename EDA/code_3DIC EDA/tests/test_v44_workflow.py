"""Low-memory V44 diagnostics and launch-safety tests; no Router/scorer execution."""
import csv,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import analyze_v44 as audit
import validate_v4_4 as workflow

class DiagnosticsTests(unittest.TestCase):
    def test_decomposition_and_streaming(self):
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);csvpath=folder/'resources.csv';result=folder/'result.json'
            with csvpath.open('w',newline='') as f:
                w=csv.writer(f);w.writerow(['node','col','row','layer','capacity','fixed_demand','routing_demand','demand','overflow'])
                w.writerows([[4,0,0,1,2,3,1,4,2],[5,1,0,1,2,0,4,4,2],[8,0,0,2,0,1,0,1,1]])
            result.write_text(json.dumps({'x_lines':[0,1,2],'y_lines':[0,1,2],'nets':[{'net':'test','pins':[{'node':4}],'nodes':[4,5],'edges':[[4,5]],'cross_die':False,'tsv_node':None}]}))
            r=audit.audit(csvpath,result)
            self.assertEqual((r['overflow'],r['fixed_baseline'],r['routing_increment']),(5,2,3))
            self.assertEqual(r['by_layer'][2]['zero_capacity_demand'],1)
            self.assertEqual(r['net_count'],1);self.assertEqual(r['tsv_footprint_overflow'],'UNKNOWN')
            self.assertEqual(r['top_fraction']['0.01']['overflow'],2)
            self.assertLess(r['maximum_reader_chars'],2**20)

    def test_conflicts_are_exclusive(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'record.json';audit.save(p,{'protected':True})
            with self.assertRaises(FileExistsError):audit.save(p,{})
            self.assertTrue(json.loads(p.read_text())['protected'])

    def test_build_resource_block_never_launches(self):
        with tempfile.TemporaryDirectory() as t,patch.object(workflow,'ROOT',Path(t)),patch.object(workflow,'gate',return_value={'allowed':False}),patch.object(workflow,'launch') as launch:
            workflow.build();launch.assert_not_called()
            self.assertFalse(json.loads((Path(t)/'build_attempts/initial/resource_gate.json').read_text())['allowed'])

    def test_invalid_budget_or_variant_never_launches(self):
        with patch.object(workflow,'launch') as launch:
            for budget in [-1,.06,float('nan')]:
                with self.assertRaises(ValueError):workflow.experiment('toy','joint',budget)
            with self.assertRaises(ValueError):workflow.experiment('toy','not_real',.01)
            launch.assert_not_called()

    def test_missing_verified_build_blocks_experiment(self):
        with tempfile.TemporaryDirectory() as t,patch.object(workflow,'ROOT',Path(t)),patch.object(workflow,'catalog',return_value={'toy':{}}),patch.object(workflow,'timed_process') as launch:
            with self.assertRaises(FileNotFoundError):workflow.experiment('toy','joint',.01)
            launch.assert_not_called()

    def test_no_wsl_probe_with_low_commit(self):
        with patch.object(workflow,'windows_memory',return_value={'commit_available_bytes':2**30}),patch('validate_v4_3.gate') as probe:
            self.assertFalse(workflow.gate()['allowed']);probe.assert_not_called()

    def test_cold_wsl_startup_commit_is_rechecked(self):
        with patch.object(workflow,'windows_memory',side_effect=[{'commit_available_bytes':3*2**30},{'commit_available_bytes':2**30}]),patch('validate_v4_3.gate',return_value={'allowed':True}):
            self.assertFalse(workflow.gate()['allowed'])

if __name__=='__main__':unittest.main()
