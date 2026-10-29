import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import common
import test_workflow


class ArchiveCompatibility(unittest.TestCase):
    def test_archive_resolution_is_narrow_and_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'configs/archive';archive.mkdir(parents=True)
            file=archive/'v3_legacy.json';file.write_bytes(b'{"wire":1}')
            with patch.object(common,'PROJECT',root):
                self.assertEqual(common.resolve_config(root/'configs/v3_legacy.json'),file)
                self.assertEqual(common.resolve_config(root/'other/v3_legacy.json'),root/'other/v3_legacy.json')
                self.assertEqual(common.resolve_config(root/'configs/v3_final.json'),root/'configs/v3_final.json')
            self.assertEqual(file.read_bytes(),b'{"wire":1}')


class FinalEntry(unittest.TestCase):
    def setUp(self):
        exe=os.environ.get('V3_FINAL_EXE')
        if not exe: self.skipTest('Set V3_FINAL_EXE to the temporary Final build')
        self.fixture=test_workflow.ParserIntegration(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.fixture.exe=Path(exe)

    def test_shared_config_selects_final_and_rejects_research_modes(self):
        f=self.fixture;config=f.dir/'final.json';config.write_text(json.dumps({'wire':1,'via':1,'thermal':3}),encoding='utf8')
        run=f.run_router('--config',str(config));self.assertEqual(run.returncode,0,run.stderr.decode(errors='replace'))
        data=json.loads((f.dir/'result.json').read_text(encoding='utf8'))
        self.assertEqual(data['v3_final']['strategy'],'v3_final_guide_guard')
        self.assertEqual(data['algorithm']['tree_route_mode'],0)
        self.assertEqual(data['algorithm']['net_order_mode'],0)
        self.assertEqual(data['congestion_strategy']['reroute_policy_mode'],0)
        self.assertEqual(data['v3_final']['optimization'],'v2_safe_reroute_then_occupied_graph_prune')
        config.write_text('{"tree_route_mode":1}',encoding='utf8')
        self.assertNotEqual(f.run_router('--config',str(config)).returncode,0)


class ReleaseGate(unittest.TestCase):
    def test_no_release_on_tradeoff_or_missing_official_report(self):
        from validate_v3_final import quality_gate
        rows=[]
        for case in ['toy','sample001','open_3d_case001']:
            for variant in ['v2','v3']:
                rows.append(dict(case=case,variant=variant,status='ROUTED',official_status='PASS',wirelength_dbu=9 if case=='open_3d_case001' and variant=='v3' else 10,official_overflow=2,wirelength_score=50,congestion_score=50,thermal_score=50))
        self.assertTrue(quality_gate(rows)[0])
        rows[-1]['wirelength_score']=49;self.assertFalse(quality_gate(rows)[0]);rows[-1]['wirelength_score']=50
        rows[-1]['official_overflow']=3;self.assertFalse(quality_gate(rows)[0]);rows[-1]['official_overflow']=2
        rows[-1]['official_status']='NOT_EVALUATED';self.assertFalse(quality_gate(rows)[0])
