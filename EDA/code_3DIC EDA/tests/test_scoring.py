"""Synthetic reports only. No Router, official evaluator or historical writes."""
import contextlib
import io
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))
import scoring
import score_case
from common import sha256


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='scoring_')
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.case, self.label = 'open_3d_case001', 'case001_v3_5'
        self.run = self.project / 'outputs/runs' / self.case / self.label
        self.run.mkdir(parents=True)
        self.guide = self.run / 'route.guide'
        self.guide.write_text('n\n(\n0 0 1 1 metal1\n)\n')
        inp = self.project / 'input.def'
        inp.write_text('synthetic immutable input')
        self.record = dict(case=self.case, label=self.label, status='ROUTED', returncode=0,
                           input_sha256={'def': sha256(inp)}, inputs={'def': str(inp)},
                           router_sha256='1' * 64, config_sha256='2' * 64, runtime_seconds=12, peak_memory_bytes=1048576)
        self.write(self.run / 'run.json', self.record)
        self.ident = dict(case=self.case, label=self.label, ruleset='REFERENCE_20261029', official=False,
                          guide_sha256=sha256(self.guide), input_sha256=self.record['input_sha256'],
                          router_sha256='1' * 64, config_sha256='2' * 64, reference_source_sha256='3' * 64)
        self.report = dict(ruleset='REFERENCE_20261029', official=False, static_legality='REFERENCE_VALID',
                           guide_sha256=self.ident['guide_sha256'], input_sha256=self.ident['input_sha256'],
                           reference_source_sha256='3' * 64, reference_scorer_sha256='4' * 64,
                           wirelength_dbu=1234, dbu_per_micron=1000, overflow=9,
                           normalized_overflow_squared=1.5, thermal_correlation=.12, runtime_seconds=2,
                           peak_rss_bytes=2048, error_counts={}, errors=[], by_layer={'1': {'overflow': 9}})

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf8')

    def reference(self, canonical=False):
        base = 'outputs/reports/scoring/reference_20261029' if canonical else 'outputs/reports/reference_20261029'
        path = self.project / base / self.case / self.label
        self.write(path / 'invocation.json', dict(identity=self.ident, status='COMPLETED', compiled_binary_sha256='4' * 64))
        self.write(path / 'reference_report.json', self.report)
        return path

    def official(self, status='PASS'):
        path = self.run / 'official_latest'
        inv = dict(case=self.case, label=self.label, status=status, official_status=status,
                   evaluator_sha256=scoring.ORIGINAL_SHA256, guide_sha256=sha256(self.guide),
                   evaluated_input_sha256=self.record['input_sha256'], evaluator_runtime_seconds=3,
                   peak_memory_bytes=4096)
        report = dict(status=status, grid={'dbu_per_micron':1000}, wirelength={'total_dbu':12000},
                      congestion={'total_overflow':4}, thermal_tsv={'correlation_above_ambient':.2},
                      score=dict(wirelength_score=80, congestion_score=70, thermal_score=60))
        self.write(path / 'invocation.json', inv)
        self.write(path / 'evaluation_report.json', report)
        return path

    def summary(self, scorer=scoring.REFERENCE, **kwargs):
        return scoring.summary(self.case, self.label, scorer, project=self.project, **kwargs)

    def test_historical_and_canonical_results(self):
        old = self.reference()
        before = {p.name: p.read_bytes() for p in old.iterdir()}
        result = self.summary()
        self.assertEqual(result['status'], 'REFERENCE_VALID')
        self.assertEqual(result['metrics']['wirelength_um'], 1.234)
        self.assertIsNone(result['metrics']['congestion_score'])
        self.assertFalse(result['official'])
        new = self.reference(True)
        self.assertEqual(self.summary()['report_path'], str(new / 'reference_report.json'))
        self.assertEqual(before, {p.name: p.read_bytes() for p in old.iterdir()})

    def test_duplicate_identity_and_result_conflicts_stop(self):
        self.reference(); new = self.reference(True)
        self.report['overflow'] += 1
        self.write(new / 'reference_report.json', self.report)
        with self.assertRaises(scoring.ScoringConflict): self.summary()

    def test_actual_guide_and_input_hash_mismatch_stop(self):
        self.reference()
        self.guide.write_text('changed')
        with self.assertRaisesRegex(scoring.ScoringConflict, 'Guide'): self.summary()
        self.guide.write_text('n\n(\n0 0 1 1 metal1\n)\n')
        Path(self.record['inputs']['def']).write_text('changed input')
        with self.assertRaisesRegex(scoring.ScoringConflict, 'Input'): self.summary()

    def test_report_invocation_hash_disagreement(self):
        path = self.reference()
        self.report['reference_source_sha256'] = '5' * 64
        self.write(path / 'reference_report.json', self.report)
        with self.assertRaises(scoring.ScoringConflict): self.summary()

    def test_partial_analysis_and_unknown_counts_do_not_claim_zero(self):
        path = self.reference()
        inv = scoring.read_json(path/'invocation.json');inv['status']='ERROR'
        self.write(path/'invocation.json',inv)
        result=self.summary();self.assertEqual(result['status'],'ANALYSIS_ERROR');self.assertEqual(result['metrics'],{})
        inv['status']='COMPLETED';self.write(path/'invocation.json',inv)
        self.report.pop('error_counts');self.write(path/'reference_report.json',self.report)
        self.assertIsNone(self.summary()['metrics']['tsv_conflicts'])

    def test_official_scores_and_auxiliary_not_final_score(self):
        self.official()
        result = self.summary(scoring.OFFICIAL)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['metrics']['wirelength_um'], 12)
        self.assertEqual(result['metrics']['overflow'], 4)
        self.assertEqual(result['metrics']['auxiliary_quality'], 72)
        self.assertIsNone(result['metrics']['final_total_score'])
        self.assertIsNone(result['metrics']['case_score'])

    def test_official_fail_and_environment_error_are_distinct(self):
        path = self.official('FAIL')
        self.assertEqual(self.summary(scoring.OFFICIAL)['status'], 'FAIL')
        inv = scoring.read_json(path / 'invocation.json')
        inv.update(status='ERROR', official_status='NOT_EVALUATED', failure_category='UNKNOWN')
        self.write(path / 'invocation.json', inv)
        self.assertEqual(self.summary(scoring.OFFICIAL)['status'], 'ANALYSIS_ERROR')

    def test_third_party_official_binary_rejected(self):
        path = self.official()
        inv = scoring.read_json(path / 'invocation.json'); inv['evaluator_sha256'] = '9' * 64
        self.write(path / 'invocation.json', inv)
        with self.assertRaises(scoring.ScoringConflict): self.summary(scoring.OFFICIAL)

    def test_missing_and_incomplete_router_and_reference_source(self):
        self.assertEqual(self.summary()['status'], 'NOT_EVALUATED')
        self.reference(); self.official()
        self.assertEqual(self.summary(source='reference')['status'], 'NOT_EVALUATED')
        self.assertEqual(self.summary(source='reference')['metrics'], {})
        self.record.update(status='RUNNING', returncode=None)
        self.write(self.run / 'run.json', self.record)
        self.guide.unlink()
        result = self.summary()
        self.assertFalse(result['router_complete'])
        self.assertEqual(result['router_status'], 'INCOMPLETE')
        self.assertNotEqual(result['status'], 'REFERENCE_VALID')

    def test_comparison_hash_input_and_invalid_guards(self):
        self.reference(); a = self.summary(); b = copy.deepcopy(a)
        self.assertTrue(scoring.comparison(a, b)['compatible'])
        b['status'] = 'REFERENCE_INVALID'
        self.assertTrue(scoring.comparison(a, b)['diagnostic_only'])
        b['scorer_source_sha256'] = 'different'
        self.assertFalse(scoring.comparison(a, b)['compatible'])
        b = copy.deepcopy(a); b['input_sha256'] = 'UNKNOWN'
        self.assertFalse(scoring.comparison(a, b)['compatible'])

    def test_small_summary_never_reads_large_geometry_or_csv(self):
        path = self.reference(); (path / 'resources.csv').write_text('not parsed')
        (self.run / 'result.json').write_text('not parsed')
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.name in ('resources.csv', 'result.json'):
                raise AssertionError('Full geometry read from scoring summary')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            self.assertLess(len(json.dumps(self.summary())), 12000)

    def test_immutable_index_and_cli_parameter_validation(self):
        self.reference(); record = self.summary()
        index = scoring.write_index(record, self.project)
        self.assertEqual(scoring.read_json(index)['scorer_id'], 'REFERENCE_20261029')
        record['status'] = 'REFERENCE_INVALID'
        with self.assertRaises(scoring.ScoringConflict): scoring.write_index(record, self.project)
        for extra in [['--attempt','bad'], ['--timeout','nan'], ['--memory-mb','2'], ['--scorer','official_original','--memory-mb','1024']]:
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                score_case.main(['--case', self.case, '--label', self.label, *extra])
            self.assertEqual(error.exception.code, 2)

    def test_dispatch_reuses_pass_and_routes_new_parameters_without_shell(self):
        self.reference(); record = self.summary()
        with patch.object(score_case, 'summary', return_value=record), patch.object(score_case, 'reference_directory', return_value=self.reference()), patch.object(score_case, 'write_index', return_value=self.project / 'index.json'), patch('evaluate_reference.source_identity', return_value=('3'*64, {})), patch('evaluate_reference.main', side_effect=AssertionError('Successful report must not re-evaluate')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(score_case.main(['--case',self.case,'--label',self.label]), 0)
        missing = copy.deepcopy(record); missing['status'] = 'NOT_EVALUATED'
        with patch.object(score_case, 'summary', side_effect=[missing, record]), patch.object(score_case, 'reference_directory', return_value=None), patch.object(score_case, 'PROJECT', self.project), patch.object(score_case, 'write_index', return_value=self.project / 'index.json'), patch('evaluate_reference.source_identity', return_value=('3'*64, {})), patch('evaluate_reference.main', return_value=0) as invoke, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(score_case.main(['--case',self.case,'--label',self.label,'--memory-mb','3072','--backend','wsl','--timeout','90']),0)
            self.assertIn('3072',invoke.call_args.args[0]); self.assertIn('90.0',invoke.call_args.args[0])

    def test_api_identity_errors_and_no_execution(self):
        spec = importlib.util.spec_from_file_location('scoring_ui_test_app', PROJECT / 'ui/app.py')
        app = importlib.util.module_from_spec(spec); spec.loader.exec_module(app)
        self.reference(); self.official()
        def get_summary(case, label, scorer, source, attempt, directory=None):
            return scoring.summary(case, label, scorer, source, attempt, project=self.project)
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(app, 'selected', return_value=self.run), patch.object(app, 'OUTPUTS', self.project / 'outputs'), patch.object(app, 'scoring_summary', side_effect=get_summary), patch.object(app.Handler, 'log_message'), patch('subprocess.Popen', side_effect=AssertionError('UI must not execute tools')):
            thread.start()
            try:
                url = 'http://127.0.0.1:' + str(server.server_port) + '/api/scoring?id=' + self.case + '/' + self.label
                with urlopen(url, timeout=5) as response:
                    data = json.load(response)
                self.assertEqual(data['a']['scorer_id'], 'REFERENCE_20261029')
                with urlopen(url + '&scorer=official_original&b=' + self.case + '/' + self.label, timeout=5) as response:
                    data = json.load(response)
                self.assertEqual(data['a']['scorer_id'], data['b']['scorer_id'])
                self.assertTrue(data['comparison']['compatible'])
                with urlopen(url.replace('/api/scoring?', '/api/scoring/file?') + '&file=report', timeout=5) as response:
                    self.assertEqual(json.load(response)['static_legality'], 'REFERENCE_VALID')
                self.guide.write_text('changed')
                with self.assertRaises(HTTPError) as error: urlopen(url, timeout=5)
                self.assertEqual(error.exception.code, 409)
            finally:
                server.shutdown(); thread.join(); server.server_close()

    def test_official_attempt_dispatch_is_explicit(self):
        self.official(); record = self.summary(scoring.OFFICIAL)
        missing = copy.deepcopy(record); missing['status'] = 'NOT_EVALUATED'
        with patch.object(score_case, 'summary', side_effect=[missing,record]), patch.object(score_case, 'file_hash', return_value=scoring.ORIGINAL_SHA256), patch.object(score_case, 'existing_run_dir', return_value=self.run), patch.object(score_case, 'write_index', return_value=self.project/'index.json'), patch('evaluate.main', return_value=0) as invoke, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(score_case.main(['--case',self.case,'--label',self.label,'--scorer',scoring.OFFICIAL,'--attempt','study01','--backend','wsl']),0)
            args = invoke.call_args.args[0]
            self.assertEqual(args[args.index('--engine')+1],'latest')
            self.assertEqual(args[args.index('--attempt')+1],'study01')

    def test_analysis_failure_exit_is_preserved_and_receipt_is_indexed(self):
        self.reference(); record=self.summary();missing=copy.deepcopy(record);missing['status']='NOT_EVALUATED'
        failed=copy.deepcopy(record);failed.update(status='ANALYSIS_ERROR',report_path=None,metrics={})
        with patch.object(score_case,'summary',side_effect=[missing,failed]), patch.object(score_case,'reference_directory',return_value=None), patch.object(score_case,'PROJECT',self.project), patch.object(score_case,'write_index',return_value=self.project/'index.json') as index, patch('evaluate_reference.source_identity',return_value=('3'*64,{})), patch('evaluate_reference.main',side_effect=SystemExit(2)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(score_case.main(['--case',self.case,'--label',self.label]),2)
            self.assertEqual(index.call_args.args[0]['status'],'ANALYSIS_ERROR')

    def test_reference_entry_allocates_only_new_canonical_directory(self):
        import evaluate_reference as entry
        def identify(*args): return dict(self.ident),{},self.guide
        def build(exe, backend): exe.write_bytes(b'mock reference');return ['mock-build']
        def execute(exe, inputs, guide, path, *args):
            report = dict(self.report, reference_scorer_sha256=sha256(exe))
            self.write(path/'reference_report.json',report)
            return report,['mock-execute']
        with patch.object(entry,'PROJECT',self.project), patch.object(entry,'identity',side_effect=identify), patch.object(entry,'source_identity',return_value=('3'*64,{})), patch.object(entry,'reference_directory',return_value=None), patch.object(entry,'build',side_effect=build), patch.object(entry,'execute',side_effect=execute), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(['--case',self.case,'--label',self.label]),0)
        new = self.project/'outputs/reports/scoring/reference_20261029'/self.case/self.label
        self.assertEqual(scoring.read_json(new/'invocation.json')['status'],'COMPLETED')
        self.assertEqual(self.summary()['status'],'REFERENCE_VALID')


if __name__ == '__main__':
    unittest.main()
