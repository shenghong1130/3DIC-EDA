"""Adapter-only tests. Never launch or alter the original algorithm."""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import import_v5
import run_v5_case


class V5Integration(unittest.TestCase):
    def test_79_original_files(self):
        self.assertEqual(run_v5_case.verify()['file_count'],79)

    def test_archive_rejection(self):
        for name,mode in [('../x',0),('/x',0),('C:/x',0),('x\\y',0),('x',0o120777),('NUL.txt',0),('dir/x.',0)]:
            with self.subTest(name=name):
                b=io.BytesIO()
                with zipfile.ZipFile(b,'w') as z:
                    item=zipfile.ZipInfo(name);item.filename=name;item.external_attr=mode<<16;z.writestr(item,b'x')
                with zipfile.ZipFile(io.BytesIO(b.getvalue())) as z:
                    with self.assertRaises(ValueError):import_v5.checked_members(z)

    def test_existing_run_protected_on_block(self):
        with tempfile.TemporaryDirectory(prefix='v5_adapter_test_') as temp:
            root=Path(temp).resolve();old=root/'outputs/runs/toy/toy_v5';old.mkdir(parents=True)
            (old/'route.guide').write_bytes(b'original evidence')
            inputs={}
            for name in ['def','lef','thermal']:
                p=root/name;p.write_bytes(name.encode());inputs[name]=str(p)
            with patch.object(run_v5_case,'PROJECT',root),patch.object(run_v5_case,'REPORTS',root/'reports'),patch.object(run_v5_case,'catalog',return_value={'toy':inputs}),patch.object(run_v5_case,'resource_check',return_value={'allowed':False}):
                record=run_v5_case.execute('toy','toy_v5')
            self.assertEqual(record['status'],'BLOCKED_RESOURCE')
            self.assertEqual(record['label'],'toy_v5_run2')
            self.assertEqual((old/'route.guide').read_bytes(),b'original evidence')
            self.assertFalse((old.parent/'toy_v5_run2').exists())

    def test_no_config_or_budget_cli(self):
        source=(run_v5_case.PROJECT/'scripts/run_v5_case.py').read_text(encoding='utf8')
        self.assertNotIn("add_argument('--config'",source)
        self.assertNotIn("add_argument('--time-limit'",source)
        self.assertNotIn('--development-small-budget',source)


if __name__=='__main__':unittest.main()
