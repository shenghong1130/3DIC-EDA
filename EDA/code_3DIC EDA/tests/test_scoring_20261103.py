"""Version isolation and safe default dispatch, synthetic immutable receipts."""
import copy,contextlib,io,json,unittest
from unittest.mock import patch
import test_scoring as legacy
import scoring,score_case,evaluate_reference,evaluate_reference_20261103

class NewReferenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture=legacy.ScoringTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture
    def latest(self):
        f=self.f;identity=copy.deepcopy(f.ident);report=copy.deepcopy(f.report)
        identity.update(ruleset='REFERENCE_20261103',pin_assignment_policy='nearest_in_cell')
        report.update(ruleset='REFERENCE_20261103',pin_assignment_policy='nearest_in_cell')
        path=f.project/'outputs/reports/scoring/reference_20261103'/f.case/f.label
        f.write(path/'invocation.json',{'identity':identity,'status':'COMPLETED','compiled_binary_sha256':'4'*64})
        f.write(path/'reference_report.json',report);return path,report
    def test_new_and_old_reports_are_separate_and_immutable(self):
        f=self.f;old=f.reference();before={p.name:p.read_bytes() for p in old.iterdir()};self.latest()
        a=f.summary(scoring.REFERENCE);b=f.summary(scoring.REFERENCE_LATEST)
        self.assertEqual(a['scorer_id'],'REFERENCE_20261029');self.assertEqual(b['scorer_id'],'REFERENCE_20261103')
        self.assertEqual(b['status'],'REFERENCE_VALID');self.assertIsNone(b['metrics']['final_total_score'])
        self.assertFalse(scoring.comparison(a,b)['compatible']);self.assertEqual(before,{p.name:p.read_bytes() for p in old.iterdir()})
    def test_old_report_does_not_satisfy_new_model(self):
        self.f.reference();self.assertEqual(self.f.summary(scoring.REFERENCE_LATEST)['status'],'NOT_EVALUATED')
    def test_mislabeled_new_report_is_rejected(self):
        path,report=self.latest();report['ruleset']='REFERENCE_20261029';self.f.write(path/'reference_report.json',report)
        with self.assertRaisesRegex(scoring.ScoringConflict,'ruleset'):self.f.summary(scoring.REFERENCE_LATEST)
    def test_input_change_is_rejected(self):
        self.latest();(self.f.project/'input.def').write_text('changed')
        with self.assertRaises(scoring.ScoringConflict):self.f.summary(scoring.REFERENCE_LATEST)
    def test_assignment_models_cannot_be_directly_compared(self):
        self.latest();a=self.f.summary(scoring.REFERENCE_LATEST);b=copy.deepcopy(a)
        b['model_parameters']['pin_assignment_policy']='upper_in_cell'
        self.assertFalse(scoring.comparison(a,b)['compatible'])
    def test_cli_default_is_new_and_reuses_without_execution(self):
        self.latest();record=self.f.summary(scoring.REFERENCE_LATEST)
        with patch.object(score_case,'summary',return_value=record) as summary,patch.object(score_case,'write_index',return_value=None),patch.object(evaluate_reference_20261103,'source_identity',return_value=('3'*64,{})),patch.object(evaluate_reference_20261103,'main',side_effect=AssertionError('No scorer should launch')),patch.object(evaluate_reference,'main',side_effect=AssertionError('Old scorer must not launch')),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(score_case.main(['--case',self.f.case,'--label',self.f.label]),0)
            self.assertEqual(summary.call_args.args[2],scoring.REFERENCE_LATEST)
    def test_reference_sources_have_distinct_identity(self):
        self.assertNotEqual(evaluate_reference.source_identity()[0],evaluate_reference_20261103.source_identity()[0])
        self.assertEqual(evaluate_reference.source_identity()[0],'b0c867aeaa98f2ff01144ca1d5d0d6c3fa2b144950024c7dcb107c9bd2d466f9')

if __name__=='__main__':unittest.main()
