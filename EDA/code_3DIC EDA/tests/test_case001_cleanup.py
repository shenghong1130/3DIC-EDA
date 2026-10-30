"""Migration and retired-cleanup safety; temporary fixtures, no real tools."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import common
import cleanup_case001 as cleanup
import cleanup_outputs as safe
import evaluate

class Case001Migration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/'workspace/EDA/core'
        self.outputs=self.root/'outputs';self.run=self.outputs/'runs/open_3d_case001/case001_v2'
        self.run.mkdir(parents=True);(self.root/'docs').mkdir()
        self.old=self.outputs/'runs/open_3d_case001/old_control'
        self.inputs={}
        for key in ['def','lef','thermal']:
            f=self.root/(key+'.input');f.write_text('fixture');self.inputs[key]=str(f)
        self.raw=dict(case='open_3d_case001',label='old_control',status='ROUTED',returncode=0,
            guide=str(self.old/'route.guide'),trace=None,inputs=self.inputs,
            input_sha256={k:common.sha256(Path(v)) for k,v in self.inputs.items()},
            command=['router','--output',str(self.old/'route.guide')])
        self.manifest=self.run/'run.json';self.manifest.write_text(json.dumps(self.raw))
        (self.run/'route.guide').write_text('real fixture');(self.run/'result.json').write_text('{}')
        self.registry=self.root/'docs/router_versions.json'
        self.data=dict(schema_version=1,versions={},run_migrations={'open_3d_case001/old_control':dict(
            directory='outputs/runs/open_3d_case001/case001_v2',original_directory='outputs/runs/open_3d_case001/old_control',
            label='case001_v2',original_label='old_control',original_run_sha256=common.sha256(self.manifest),
            guide_sha256=common.sha256(self.run/'route.guide'))})
        self.save()
        for patcher in [patch.object(common,'PROJECT',self.root),patch.object(common,'OUTPUTS',self.outputs)]:
            patcher.start();self.addCleanup(patcher.stop)
    def save(self):self.registry.write_text(json.dumps(self.data))
    def test_raw_bytes_immutable_and_logical_identity_updated(self):
        before=self.manifest.read_bytes();r=common.load_run(self.run)
        self.assertEqual(r['label'],'case001_v2');self.assertEqual(r['guide'],str(self.run/'route.guide'))
        self.assertEqual(r['command'],self.raw['command']);self.assertEqual(r['migration']['original_label'],'old_control')
        self.assertEqual(before,self.manifest.read_bytes())
        self.assertEqual(common.existing_run_dir('open_3d_case001','old_control'),self.run)
        self.assertEqual(common.existing_run_dir('open_3d_case001','case001_v2'),self.run)
    def test_original_manifest_change_rejected(self):
        self.manifest.write_text('{}')
        with self.assertRaisesRegex(ValueError,'identity changed'):common.load_run(self.run)
    def test_registry_path_escape_rejected(self):
        self.data['run_migrations']['open_3d_case001/old_control']['directory']='../outside';self.save()
        with self.assertRaisesRegex(ValueError,'escaped outputs'):common.existing_run_dir('open_3d_case001','old_control')
    def test_official_report_paths_overlay_preserves_score_and_bytes(self):
        p=self.run/'evaluation_report.json';raw={'inputs':{'guide':str(self.old/'route.guide')},'score':{'congestion_score':78.03719827292642},'status':'PASS'}
        p.write_text(json.dumps(raw));before=p.read_bytes();value=common.load_record(p)
        self.assertEqual(value['inputs']['guide'],str(self.run/'route.guide'));self.assertEqual(value['score'],raw['score']);self.assertEqual(p.read_bytes(),before)
    def test_missing_registry_leaves_unmigrated_workflow_unchanged(self):
        self.registry.unlink();self.assertEqual(common.load_run(self.run),self.raw)
    def test_score_preflight_uses_existing_guide_new_label_no_router(self):
        # execute is replaced before invoking CLI; no official tool is started.
        observed=[]
        def fake(args,run,directory):observed.append((run,directory));return {'status':'PASS'}
        with patch.object(evaluate,'execute',side_effect=fake),contextlib.redirect_stdout(io.StringIO()):
            result=evaluate.main(['--case','open_3d_case001','--label','case001_v2','--backend','wsl','--attempt','fixture'])
        self.assertEqual(result,0);self.assertEqual(observed[0][0]['label'],'case001_v2');self.assertTrue(Path(observed[0][0]['guide']).exists())
        self.assertEqual(observed[0][1],self.run/'official_latest_attempts/fixture')
    def test_existing_score_directory_still_refuses_overwrite(self):
        (self.run/'official_latest').mkdir()
        with patch.object(evaluate,'execute') as tool,contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            evaluate.main(['--case','open_3d_case001','--label','case001_v2','--backend','wsl'])
        tool.assert_not_called()
    def test_retired_pruning_never_reads_policy_or_deletes(self):
        before=safe.scan(self.outputs)
        p=cleanup.execute(self.root,self.outputs);self.assertEqual(p['status'],'RETIRED');self.assertEqual(p['delete_directories'],0)
        with self.assertRaisesRegex(ValueError,'retired'):cleanup.execute(self.root,self.outputs,apply=True)
        self.assertEqual(before,safe.scan(self.outputs))
    def test_snapshot_detects_same_size_changed_bytes(self):
        before=cleanup.snapshot(self.run,self.outputs);p=self.run/'result.json';s=p.stat();p.write_text('[]');os.utime(p,ns=(s.st_atime_ns,s.st_mtime_ns))
        self.assertNotEqual(before,cleanup.snapshot(self.run,self.outputs))
    def test_previous_numeric_label_and_paths_resolve_directly_to_latest(self):
        row=self.data['run_migrations']['open_3d_case001/old_control']
        row.update(previous_labels=['v2'],previous_directories=['outputs/runs/open_3d_case001/v2'])
        self.save()
        self.assertEqual(common.existing_run_dir('open_3d_case001','v2'),self.run)
        value=common.remap_record_paths(str(self.outputs/'runs/open_3d_case001/v2/route.guide'))
        self.assertEqual(value,str(self.run/'route.guide'))
    def test_case_version_label_uses_actual_case_id(self):
        for case,expected in [('open_3d_case001','case001_v3_2'),('open_3d_case003','case003_v3_2'),
                ('open_3d_case005','case005_v3_2'),('toy','toy_v3_2'),('sample001','sample001_v3_2'),
                ('sample001_legacy','sample001_legacy_v3_2')]:
            self.assertEqual(common.formal_run_label(case,'v3_2'),expected)
    def test_actual_directory_outranks_a_reused_historical_alias(self):
        direct=self.outputs/'runs/open_3d_case001/old_control';direct.mkdir()
        self.assertEqual(common.existing_run_dir('open_3d_case001','old_control'),direct)
    def test_snapshot_rejects_symlink(self):
        try:(self.run/'link').symlink_to(self.registry)
        except OSError:self.skipTest('Symlink privilege unavailable')
        with self.assertRaises(ValueError):cleanup.snapshot(self.run,self.outputs)

if __name__=='__main__':unittest.main()
