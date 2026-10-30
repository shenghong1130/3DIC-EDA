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
            (root/'docs').mkdir()
            (root/'docs/router_versions.json').write_text(json.dumps({'config_aliases':{'configs/v3_legacy.json':{
                'path':'configs/archive/v3_legacy.json','sha256':common.sha256(file)}}}))
            with patch.object(common,'PROJECT',root):
                self.assertEqual(common.resolve_config(root/'configs/v3_legacy.json'),file)
                self.assertEqual(common.resolve_config(root/'other/v3_legacy.json'),root/'other/v3_legacy.json')
                self.assertEqual(common.resolve_config(root/'configs/v3_final.json'),root/'configs/v3_final.json')
            self.assertEqual(file.read_bytes(),b'{"wire":1}')

    def test_archived_config_hash_mismatch_is_not_silently_substituted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'configs/archive').mkdir(parents=True);(root/'docs').mkdir()
            file=root/'configs/archive/default.json';file.write_text('{"wire":2}')
            (root/'docs/router_versions.json').write_text(json.dumps({'config_aliases':{'configs/default.json':{
                'path':'configs/archive/default.json','sha256':'0'*64}}}))
            with patch.object(common,'PROJECT',root):
                with self.assertRaisesRegex(ValueError,'identity mismatch'):
                    common.resolve_config(root/'configs/default.json')

    def test_router_redirect_checks_hash_and_workspace_boundary(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'docs').mkdir();(root/'history').mkdir();file=root/'history/old';file.write_bytes(b'original')
            registry=root/'docs/router_versions.json';registry.write_text(json.dumps({'router_aliases':{'build/old':{'path':'history/old','sha256':hashlib.sha256(b'original').hexdigest()}}}))
            with patch.object(common,'PROJECT',root):
                self.assertEqual(common.resolve_router(root/'build/old'),file)
                file.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'identity mismatch'):common.resolve_router(root/'build/old')
                self.assertEqual(common.resolve_router(root/'other/old'),root/'other/old')


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
        self.assertEqual(data['v3_final']['strategy'],'v3_steiner_negotiated_global')
        self.assertEqual(data['v3_algorithm']['stage'],3)
        self.assertEqual(data['v3_algorithm']['generation'],'steiner_negotiated_tsv_research')
        self.assertGreater(data['v3_algorithm']['counters'].get('steiner_route_attempts',0),0)
        self.assertEqual(data['algorithm']['tree_route_mode'],0)
        self.assertEqual(data['algorithm']['net_order_mode'],0)
        self.assertEqual(data['congestion_strategy']['reroute_policy_mode'],0)
        self.assertEqual(data['v3_final']['optimization'],'bounded_hanan_exact_target_price_tsv')
        config.write_text('{"tree_route_mode":1}',encoding='utf8')
        self.assertNotEqual(f.run_router('--config',str(config)).returncode,0)


class NumericHotspotEntry(unittest.TestCase):
    def test_production_build_enters_hotspot_architecture(self):
        exe=os.environ.get('V3_2_EXE')
        if not exe:self.skipTest('Set V3_2_EXE to the temporary numeric build')
        fixture=test_workflow.ParserIntegration(methodName='runTest');fixture.setUp();self.addCleanup(fixture.tearDown);fixture.exe=Path(exe)
        config=fixture.dir/'v3_2.json';config.write_text(json.dumps({'wire':1,'via':1,'thermal':3}),encoding='utf8')
        run=fixture.run_router('--config',str(config));self.assertEqual(run.returncode,0,run.stderr.decode(errors='replace'))
        data=json.loads((fixture.dir/'result.json').read_text(encoding='utf8'))
        self.assertEqual(data['v3_final']['strategy'],'v3_2_hotspot_multi_net')
        self.assertEqual(data['v3_algorithm']['generation'],'v3_2_hotspot_multi_net')
        self.assertIn('hotspot_batches',data['v3_algorithm'])

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

    def test_existing_final_is_a_required_control_when_present(self):
        from validate_v3_final import quality_gate
        rows=[]
        for case in ['toy','sample001','open_3d_case001']:
            for variant in ['v2','old_final','v3']:
                rows.append(dict(case=case,variant=variant,status='ROUTED',official_status='PASS',wirelength_dbu=10 if variant=='v2' else 9,official_overflow=2,wirelength_score=50,congestion_score=50,thermal_score=50))
        self.assertTrue(quality_gate(rows)[0])
        rows[-2]['wirelength_dbu']=8
        self.assertFalse(quality_gate(rows)[0])
