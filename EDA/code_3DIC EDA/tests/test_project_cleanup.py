"""Cleanup safety checks use temporary fixtures only; never launch a Router."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import project_cleanup as cleanup


def row(path,root):
    s=path.stat()
    return dict(path=path.relative_to(root).as_posix(),bytes=s.st_size,mtime_ns=s.st_mtime_ns,ctime_ns=s.st_ctime_ns,inode=s.st_ino,creation_ns=getattr(s,'st_birthtime_ns',s.st_ctime_ns),atime_ns=s.st_atime_ns,mode=s.st_mode,sha256=cleanup.sha(path))


class CleanupSafety(unittest.TestCase):
    def test_root_and_escape_never_allowed(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/'root';root.mkdir()
            for p in (root,root.parent/'elsewhere'):
                with self.assertRaises(ValueError):cleanup.checked(root,p)

    def test_tracked_protected_changed_and_missing_source_retained(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'tests/sample.py';source.parent.mkdir();source.write_text('pass')
            cached=source.parent/'__pycache__/sample.cpython-313.pyc';cached.parent.mkdir();cached.write_bytes(b'temporary bytecode')
            os.utime(cached,(time.time()-600,time.time()-600));r=row(cached,root)
            with patch.object(cleanup,'PROJECT',root):
                with self.assertRaises(ValueError):cleanup.candidate_guard(r,{r['path']},{})
                self.assertEqual(cleanup.candidate_guard(r,set(),{}),cached)
                source.unlink()
                with self.assertRaises(ValueError):cleanup.candidate_guard(r,set(),{})
                self.assertTrue(cached.exists())
                cached.write_bytes(b'user change')
                with self.assertRaises(ValueError):cleanup.candidate_guard(r,set(),{})
                protected=dict(r,path='v5_trace/engine/maze_router.py')
                with self.assertRaises(ValueError):cleanup.candidate_guard(protected,set(),{})

    def test_only_old_cache_with_surviving_source_is_disposable(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'result.json';source.write_text('{"nets":[]}')
            cache=root/('a'*24+'.sqlite')
            def make(version):
                with sqlite3.connect(cache) as db:
                    db.execute('CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT)')
                    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('signature',json.dumps([version,cleanup.fingerprint(source),None])))
                db.close()
            with patch.object(cleanup,'PROJECT',root),patch.object(cleanup,'exclusive_probe'):
                make(4);self.assertFalse(cleanup.sqlite_cache(cache,4)['eligible'])
                make(3);self.assertTrue(cleanup.sqlite_cache(cache,4)['eligible'])
                source.unlink();self.assertFalse(cleanup.sqlite_cache(cache,4)['eligible'])

    def test_bad_archive_fails_before_any_real_deletion(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t);workspace=base/'workspace';root=workspace/'project';report=root/'outputs/reports/project_cleanup';report.mkdir(parents=True)
            doc=root/'build/README.md';doc.parent.mkdir();doc.write_text('original readme')
            source=root/'tests/sample.py';source.parent.mkdir();source.write_text('pass')
            cached=source.parent/'__pycache__/sample.cpython-313.pyc';cached.parent.mkdir();cached.write_bytes(b'cache')
            os.utime(cached,(time.time()-600,time.time()-600))
            r=row(cached,root);d=row(doc,root)
            plan={'project':str(root),'archive_directory':str(base/'archives/job'),'candidate_files':[r],'ui_store_version':4}
            cleanup.dump(report/'audit_plan.json',plan);cleanup.dump(report/'dependency_analysis.json',{'by_basename':{}})
            with (report/'inventory_before.jsonl').open('w') as f:
                for item in (r,d):f.write(json.dumps(item)+'\n')
            (report/'cleanup_plan.md').write_text('fixture plan')
            with patch.object(cleanup,'PROJECT',root),patch.object(cleanup,'WORKSPACE',workspace),patch.object(cleanup,'REPORT',report),patch.object(cleanup,'verify_sources'),patch.object(cleanup,'processes',return_value={'relevant':[]}),patch.object(cleanup,'git_state',return_value={'tracked':[]}),patch.object(cleanup,'verify_archive',side_effect=ValueError('Corrupt backup')):
                with self.assertRaisesRegex(ValueError,'Corrupt backup'):cleanup.apply()
            self.assertTrue(cached.exists());self.assertEqual(doc.read_text(),'original readme')

    def test_restore_refuses_overwrite_and_path_escape(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);archive=root/'recovery.zip';payload=b'original bytes'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('files/cache.pyc',payload)
            manifest={'id':'fixture','project':str(root/'restored'),'archive_file':str(archive),'archive_sha256':cleanup.sha(archive),'entries':[{'path':'cache.pyc','archive_entry':'files/cache.pyc','sha256':hashlib.sha256(payload).hexdigest(),'action':'DELETE_SAFE','mode':0o644,'mtime_ns':time.time_ns(),'atime_ns':time.time_ns()}]}
            cleanup.dump(root/'restore_manifest.json',manifest);script=root/'restore_cleanup.py';script.write_text(cleanup.RESTORE_SCRIPT)
            command=[sys.executable,'-B',str(script),'--apply']
            subprocess.run(command,check=True,capture_output=True)
            target=root/'restored/cache.pyc';self.assertEqual(target.read_bytes(),payload)
            target.write_bytes(b'user data');subprocess.run(command,check=True,capture_output=True);self.assertEqual(target.read_bytes(),b'user data')
            manifest['entries'][0]['path']='../escape.pyc';cleanup.dump(root/'restore_manifest.json',manifest)
            result=subprocess.run(command,capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertFalse((root/'escape.pyc').exists())


if __name__=='__main__':unittest.main()
