"""Post-cleanup integrity gates must distinguish sources from removed caches."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import project_cleanup as cleanup


class FinalizationSafety(unittest.TestCase):
    def test_final_reports_verify_retained_files_without_more_removals(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);report=root/'outputs/reports/project_cleanup';report.mkdir(parents=True)
            source=root/'source.py';source.write_text('pass')
            rows=[{'path':'source.py','kind':'file','sha256':cleanup.sha(source)}]
            ui={'available':True,'run_count':1}
            cleanup.dump(report/'ui_before.json',ui)
            manifest={'archive_file':str(root/'archive.zip'),'entries':[{'path':'cache.pyc','status':'REMOVED','bytes':12}]}
            with patch.object(cleanup,'PROJECT',root),patch.object(cleanup,'REPORT',report),patch.object(cleanup,'__file__',str(root/'scripts/project_cleanup.py')),patch.object(cleanup,'verify_sources'),patch.object(cleanup,'processes',return_value={'relevant':[]}),patch.object(cleanup,'git_state',return_value={}),patch.object(cleanup,'ui_snapshot',return_value=ui),patch.object(cleanup,'report_results') as reports:
                cleanup.finalize({},rows,manifest)
                reports.assert_called_once()
            result=cleanup.load(report/'verification.json')
            self.assertEqual(result['unexpected_changes'],[])
            self.assertEqual(result['retained_files_hashed'],1)
            self.assertTrue(source.exists())

    def test_removed_bytecode_does_not_hide_missing_or_changed_source(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);report=root/'outputs/reports/project_cleanup';report.mkdir(parents=True)
            registry=root/'docs/router_versions.json';registry.parent.mkdir();registry.write_text('{}')
            source=root/'tests/sample.py';source.parent.mkdir();source.write_text('pass')
            rows=[{'path':'tests/sample.py','kind':'file','classification':'KEEP','sha256':cleanup.sha(source)},
                  {'path':'tests/__pycache__/sample.cpython-313.pyc','kind':'file','classification':'DELETE_SAFE','sha256':'removed'}]
            (report/'inventory_before.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            plan={'v5_original_files':{},'trace_files':{},'registry_sha256':cleanup.sha(registry)}
            with patch.object(cleanup,'PROJECT',root),patch.object(cleanup,'REPORT',report),patch.object(cleanup,'__file__',str(root/'scripts/project_cleanup.py')):
                cleanup.verify_sources(plan)
                source.write_text('user change')
                with self.assertRaisesRegex(ValueError,'Dependency/source changed'):cleanup.verify_sources(plan)
                source.unlink()
                with self.assertRaises(FileNotFoundError):cleanup.verify_sources(plan)


if __name__=='__main__':unittest.main()
