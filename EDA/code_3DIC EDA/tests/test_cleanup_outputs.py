"""Destructive maintenance safety checks, isolated in TemporaryDirectory."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cleanup_outputs', Path(__file__).resolve().parents[1] / 'scripts/cleanup_outputs.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

class CleanupSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / 'outputs'
        self.root.mkdir()
        self.patch = patch.object(c, 'OUTPUTS', self.root)
        self.patch.start()
        self.profile = self.root / 'browser_profile'
        (self.profile / 'Default').mkdir(parents=True)
        (self.profile / 'Local State').write_text('{}')
        (self.profile / 'Default/Cache').write_text('generated test cache')
        (self.root / 'reports').mkdir()
        (self.root / 'reports/ui_browser_test.json').write_text('{"status":"PASS"}')
        self.age()

    def age(self):
        for p in sorted(self.root.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            os.utime(p, (time.time() - 600, time.time() - 600))

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def plan(self, tracked=None, rows=None):
        return c.make_plan(c.scan(self.root), tracked or set(), rows or [], [], self.root)

    def test_unknown_and_history_never_candidates(self):
        for rel in ['runs/case/failed/run.json', 'unknown/result.json', 'submissions/history.zip', 'ui_cache/cache.sqlite']:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('keep')
        self.assertEqual([p['path'] for p in self.plan()], ['browser_profile'])

    def test_zip_tracked_and_report_descendants_block_whole_group(self):
        for name in ['history.zip', 'invocation.json', 'progress.log', 'build.manifest.json']:
            p = self.profile / name
            p.write_text('keep')
            self.age()
            self.assertEqual(self.plan()[0]['action'], 'keep')
            p.unlink()
        tracked = {str((self.profile / 'Local State').absolute()).casefold()}
        self.assertEqual(self.plan(tracked)[0]['action'], 'keep')

    def test_windows_and_wsl_and_relative_active_paths_block(self):
        for command in [str(self.profile), '/mnt/d/project/outputs/browser_profile', '--user-data-dir=outputs/browser_profile']:
            rows = [{'pid': 42, 'name': 'browser', 'command': command}]
            self.assertEqual(self.plan(rows=rows)[0]['action'], 'keep')

    def test_recent_or_unfinished_profiles_stay(self):
        (self.profile / 'new').write_text('active')
        self.assertEqual(self.plan()[0]['action'], 'keep')
        self.age()
        (self.root / 'reports/ui_browser_test.json').write_text('{"status":"FAIL"}')
        self.assertEqual(self.plan()[0]['action'], 'keep')

    def test_escape_rejected(self):
        with self.assertRaises(ValueError):
            c.checked(self.root, self.root / '../elsewhere')
        with self.assertRaises(ValueError):
            c.checked(self.root, self.root)

    def test_safe_apply_removes_only_candidate(self):
        plan = self.plan()
        inventory = c.scan(self.root)
        audit = self.root / 'reports/test_apply'
        audit.mkdir()
        with patch.object(c, 'processes', return_value=[]):
            result = c.execute(plan, inventory, set(), [], audit)
        self.assertEqual(result[0]['status'], 'completed')
        self.assertFalse(self.profile.exists())
        self.assertTrue((self.root / 'reports/ui_browser_test.json').exists())

    def test_new_file_since_preview_blocks_delete(self):
        plan = self.plan()
        inventory = c.scan(self.root)
        (self.profile / 'new').write_text('keep')
        self.age()
        audit = self.root / 'reports/test_changed'
        audit.mkdir()
        with patch.object(c, 'processes', return_value=[]):
            result = c.execute(plan, inventory, set(), [], audit)
        self.assertEqual(result[0]['status'], 'skipped_or_partial')
        self.assertTrue((self.profile / 'new').exists())

    def test_move_never_overwrites(self):
        source = self.root / 'time_probe.txt'
        source.write_text('Command being timed: "./router --help"')
        dest = self.root / 'development/probes/time_probe.txt'
        dest.parent.mkdir(parents=True)
        dest.write_text('existing')
        self.age()
        entry = next(e for e in self.plan() if e['path'] == 'time_probe.txt')
        self.assertEqual(entry['action'], 'keep')
        self.assertEqual(dest.read_text(), 'existing')

    def test_default_is_read_only_and_inspection_failure_blocks_apply(self):
        before = c.scan(self.root)
        with patch.object(c, 'tracked_files', return_value=set()), patch.object(c, 'source_references', return_value=[]), \
                patch.object(c, 'processes', return_value=[]), patch('sys.argv', ['cleanup_outputs.py']), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(c.main(), 0)
        self.assertEqual(c.scan(self.root), before)
        with patch.object(c, 'tracked_files', return_value=set()), patch.object(c, 'source_references', return_value=[]), \
                patch.object(c, 'processes', side_effect=OSError('unavailable')), patch('sys.argv', ['cleanup_outputs.py', '--apply']), \
                contextlib.redirect_stdout(io.StringIO()):
            c.main()
        self.assertTrue(self.profile.exists())

if __name__ == '__main__':
    unittest.main()
