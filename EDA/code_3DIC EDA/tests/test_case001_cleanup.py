"""Pruning invariants using temporary files only; no Router or evaluator."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import cleanup_case001 as c
import cleanup_outputs as safe
from case001_history import summary_rows


class Case001Prune(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name).resolve()/'workspace/EDA/core'
        self.root=self.project/'outputs';self.run=self.root/'runs/open_3d_case001/old'
        self.run.mkdir(parents=True);(self.project/'docs').mkdir();(self.root/'reports').mkdir()
        (self.run/'run.json').write_text('{}');(self.run/'route.guide').write_text('real fixture')
        self.row=dict(identity='historical_research_fixture',experiment_type='HISTORICAL_RESEARCH',
            retention='DELETE',verified_from_original=True,guide_sha256=safe.digest(self.run/'route.guide'),
            files=c.snapshot(self.run,self.root),evaluations=[],official_reports={})
        self.data=dict(schema_version=1,case='open_3d_case001',records={'old':self.row},representatives={})
        self.policy=dict(summary_sha256='',delete={'old':{'guide_sha256':self.row['guide_sha256']}},keep={})
        self.write_policy()
        self.patches=[patch.object(safe,'processes',return_value=[]),patch.object(safe,'tracked_files',return_value=set())]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def write_policy(self):
        file=self.root/c.SUMMARY;file.write_text(json.dumps(self.data))
        self.policy['summary_sha256']=safe.digest(file)
        (self.project/c.POLICY).write_text(json.dumps(self.policy))

    def preview(self):return c.preview(self.project,self.root)[0]

    def test_preview_is_readonly_and_apply_only_exact_files(self):
        other=self.root/'runs/open_3d_case003/failed/run.json';other.parent.mkdir(parents=True);other.write_text('keep')
        unknown=self.run.parent/'unknown';unknown.mkdir();(unknown/'keep').write_text('keep')
        tune=self.run.parent/'tune_fixture';tune.mkdir();(tune/'keep').write_text('keep')
        before=safe.scan(self.root);self.assertEqual(self.preview()['delete_directories'],1)
        self.assertEqual(before,safe.scan(self.root));self.assertFalse((self.root/c.AUDIT).exists())
        result=c.execute(self.project,self.root,apply=True)
        self.assertEqual(result['status'],'COMPLETED');self.assertFalse(self.run.exists())
        self.assertTrue(other.exists());self.assertTrue(unknown.exists());self.assertTrue(tune.exists())
        with self.assertRaisesRegex(ValueError,'already exists'):c.execute(self.project,self.root,apply=True)

    def test_same_size_mtime_modified_bytes_are_rejected(self):
        f=self.run/'run.json';s=f.stat();f.write_text('[]');os.utime(f,ns=(s.st_atime_ns,s.st_mtime_ns))
        self.assertEqual(self.preview()['delete_directories'],0);self.assertTrue(f.exists())

    def test_new_member_is_rejected(self):
        (self.run/'new.log').write_text('new');self.assertEqual(self.preview()['delete_directories'],0)

    def test_tuning_name_cannot_be_allowlisted(self):
        renamed=self.run.with_name('tune_fixture');self.run.rename(renamed)
        self.data['records']={'tune_fixture':self.row};self.policy['delete']={'tune_fixture':self.policy['delete']['old']};self.write_policy()
        self.assertEqual(self.preview()['delete_directories'],0)

    def test_kept_representative_cannot_be_allowlisted(self):
        self.policy['keep']['old']='Representative';self.write_policy();self.assertEqual(self.preview()['delete_directories'],0)

    def test_active_process_and_unavailable_inspection_block(self):
        with patch.object(safe,'processes',return_value=[dict(pid=42,name='python.exe',command='python scripts/evaluate.py')]):
            self.assertEqual(self.preview()['delete_directories'],0)
        with patch.object(safe,'processes',side_effect=OSError('unavailable')):
            with self.assertRaises(OSError):self.preview()

    def test_dependencies_and_summary_tampering_block(self):
        (self.project/'docs/active.json').write_text(json.dumps({'path':'outputs/runs/open_3d_case001/old/result.json'}))
        self.assertEqual(self.preview()['delete_directories'],0)
        (self.root/c.SUMMARY).write_text('{}')
        with self.assertRaisesRegex(ValueError,'Summary differs'):self.preview()

    def test_archived_uri_is_not_a_file_dependency_or_fake_score(self):
        (self.project/'docs/old.json').write_text(json.dumps({'provenance':'history://case001/old/result.json'}))
        self.assertEqual(self.preview()['delete_directories'],1)
        row=summary_rows(self.data)[0]
        self.assertEqual(row['official_status'],'NOT_EVALUATED');self.assertIsNone(row['wirelength_dbu'])
        self.assertFalse(row['raw_run_retained'])

    def test_symlink_escape_rejected(self):
        target=self.root/'outside';target.write_text('keep')
        try:(self.run/'redirect').symlink_to(target)
        except OSError:self.skipTest('Symlink privilege unavailable')
        with self.assertRaises(ValueError):c.snapshot(self.run,self.root)

    def test_report_times_use_exact_prior_measurement_not_replacement_run(self):
        import report_router_benchmark as report
        row=dict(case='open_3d_case001',label='preexisting_absent',router_sha256='a',guide_sha256='b',run_directory='history://case001/preexisting_absent')
        prior=self.root/'reports/performance_comparison.json'
        prior.write_text(json.dumps({'official_cases':[row|{'router_elapsed_seconds':12.5}]}))
        with patch.object(report,'ROOT',prior.parent),patch('case001_history.archived_row',side_effect=KeyError('not in summary')):
            self.assertEqual(report.measurement_times(row)['router_elapsed_seconds'],12.5)
            self.assertIsNone(report.measurement_times(row|{'guide_sha256':'different'})['router_elapsed_seconds'])


if __name__=='__main__':unittest.main()
