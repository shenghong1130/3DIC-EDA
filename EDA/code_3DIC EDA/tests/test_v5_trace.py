"""Observer integrity, real path equivalence, rejection and disk-reader checks."""
import ast
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from v5_trace.finalize import route_record
from v5_trace.route_trace import geometry


class StripObserver(ast.NodeTransformer):
    def visit_Import(self,node):
        if any(a.name=='route_trace' and a.asname=='_trace' for a in node.names):return None
        return node
    def visit_FunctionDef(self,node):
        node.decorator_list=[d for d in node.decorator_list if not isinstance(d,ast.Attribute) or not isinstance(d.value,ast.Name) or d.value.id!='_trace']
        return self.generic_visit(node)
    def visit_Expr(self,node):
        value=node.value
        if isinstance(value,ast.Call) and isinstance(value.func,ast.Attribute) and isinstance(value.func.value,ast.Name) and value.func.value.id=='_trace':return None
        return self.generic_visit(node)


class TraceTests(unittest.TestCase):
    def probe(self,root,mode,extra=None):
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',**(extra or {}))
        env.pop('V5_TRACE_DIR',None)
        process=subprocess.run([sys.executable,'-B',str(ROOT/'tests/v5_trace_probe.py'),mode,str(root)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env,timeout=120)
        self.assertEqual(process.returncode,0,process.stdout.decode('utf8','replace'))
        return json.loads((root/'probe.json').read_text(encoding='utf8'))

    def test_original_79_hashes_and_observer_only_ast(self):
        manifest=json.loads((ROOT/'v5_trace/original_sha256.json').read_text(encoding='utf8'))
        original=ROOT/'v5/engine';files={p.relative_to(original).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in original.rglob('*') if p.is_file()}
        self.assertEqual(len(files),79);self.assertEqual(files,manifest['files'])
        for name in files:
            if not name.endswith('.py'):continue
            with self.subTest(name=name):
                before=ast.parse((original/name).read_bytes());after=StripObserver().visit(ast.parse((ROOT/'v5_trace/engine'/name).read_bytes()))
                self.assertEqual(ast.dump(before),ast.dump(after))

    def test_real_astar_mst_steiner_and_disabled_decisions(self):
        with tempfile.TemporaryDirectory(prefix='v5_trace_test_') as temp:
            root=Path(temp);runs={}
            for mode in ('original','on','off'):
                command=[sys.executable,'-B',str(ROOT/'tests/v5_trace_probe.py'),mode,str(root/mode)]
                env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
                env.pop('V5_TRACE_DIR',None)
                process=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env,timeout=120)
                self.assertEqual(process.returncode,0,process.stdout.decode('utf8','replace'))
                runs[mode]=json.loads((root/mode/'probe.json').read_text(encoding='utf8'))
            for kind in ('astar','manhattan_mst','steiner'):
                control=runs['original']['results'][kind]
                for mode in ('on','off'):
                    actual=runs[mode]['results'][kind]
                    for key in ('guide_sha256','validation','cross_die','decisions_sha256','search_operations','wire_proxy'):
                        self.assertEqual(actual[key],control[key],(mode,kind,key))
                record=json.loads((root/'on'/(kind+'_published')/'route_trace.jsonl').read_text(encoding='utf8'))
                self.assertEqual(record['provenance'],'FINAL_ACCEPTED_VERIFIED',record)
                self.assertTrue(record['edges'])
                if kind=='astar':self.assertIn('REAL_ASTAR',record['origins'])
                if kind=='steiner':self.assertIn('STEINER',record['algorithms'])
            self.assertFalse((root/'off/trace').exists())
            self.assertFalse((root/'original/trace').exists())

    def test_rejected_or_geometry_changed_cannot_be_final(self):
        layers={1:{'direction':'HORIZONTAL'}};rects=[[0,0,10,10,1]]
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE guide_nets(hash TEXT,net TEXT,geom TEXT,candidate TEXT)')
        rejected=route_record(db,Path('.'),'net',rects,layers,'guide')
        self.assertEqual(rejected['provenance'],'PATH_PROVENANCE_LOST');self.assertFalse(rejected['edges'])
        db.execute('INSERT INTO guide_nets VALUES(?,?,?,?)',('guide','net','DIFFERENT','rejected'))
        changed=route_record(db,Path('.'),'net',rects,layers,'guide')
        self.assertFalse(changed['accepted']);self.assertFalse(changed['edges'])
        self.assertEqual(geometry([[0,0,5,10,1],[5,0,10,10,1]],layers),geometry(rects,layers))

    def test_io_failure_does_not_change_routes(self):
        with tempfile.TemporaryDirectory(prefix='v5_trace_io_') as temp:
            root=Path(temp);control=self.probe(root/'control','original');failed=self.probe(root/'failed','on',{'V5_TRACE_FAIL_IO':'1'})
            self.assertEqual(control['results'],failed['results'])

    def test_net_filter_and_size_limit_remain_honest(self):
        with tempfile.TemporaryDirectory(prefix='v5_trace_cap_') as temp:
            root=Path(temp)
            excluded=self.probe(root/'filtered','on',{'V5_TRACE_NETS':'["not_this_net"]'})
            capped=self.probe(root/'capped','on',{'V5_TRACE_MAX_BYTES':'16384'})
            for kind in ('astar','manhattan_mst','steiner'):
                n=json.loads((root/'filtered'/(kind+'_published')/'route_trace.jsonl').read_text(encoding='utf8'))
                self.assertEqual(n['provenance'],'PATH_PROVENANCE_LOST');self.assertFalse(n['edges'])
                self.assertEqual(excluded['results'][kind]['guide_sha256'],capped['results'][kind]['guide_sha256'])
            self.assertTrue(all(p.stat().st_size<=16384 for p in (root/'capped/trace').glob('capture-*.jsonl')))

    def test_corrupt_trace_falls_back_to_guide(self):
        sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'ui'))
        from v5_trace_store import V5TraceStore
        def corrupt(name):raise ValueError('Trace record SHA256 mismatch')
        fake=SimpleNamespace(record=corrupt)
        net={'net':'n','guides':[[0,0,10,10,1]],'edges':[['bad','edge']]}
        result=V5TraceStore.attach(fake,net)
        self.assertEqual(result['path_provenance'],'PATH_PROVENANCE_LOST')
        self.assertEqual(result['edges'],[]);self.assertTrue(result['guides'])

    @unittest.skipUnless(os.name=='nt','Windows launch inventory')
    def test_launch_gate_does_not_overlap_existing_router(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        import run_v5_trace_case
        with patch.object(run_v5_trace_case.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'[{"ProcessId":123}]')):
            self.assertFalse(run_v5_trace_case.startup_check('toy','native')['allowed'])


if __name__=='__main__':unittest.main()
