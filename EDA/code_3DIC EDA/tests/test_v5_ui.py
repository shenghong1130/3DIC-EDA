"""V5 Guide UI regression; temporary fixtures only, no Router/scorer execution."""
import hashlib,json,shutil,sys,tempfile,threading,time,unittest
from pathlib import Path
from urllib.request import urlopen
from urllib.error import HTTPError
from unittest.mock import patch

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'ui'))
import app,v5_store

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

class V5UITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='v5_ui_');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.project=self.root/'project';self.run=self.project/'run';self.run.mkdir(parents=True)
        self.cache=self.root/'cache'
        # Real small public fixture, copied so corruption tests never touch history.
        original=PROJECT/'outputs/runs/toy/toy_v5'
        if not original.exists():self.skipTest('Existing toy_v5 fixture unavailable; no run is generated')
        r=json.loads((original/'run.json').read_text(encoding='utf8'))
        for key,path in r['inputs'].items():
            target=self.root/(key+Path(path).suffix);shutil.copyfile(path,target);r['inputs'][key]=str(target)
        for name in ('route.guide','route.run.json'):shutil.copyfile(original/name,self.run/name)
        (self.run/'run.json').write_text(json.dumps(r),encoding='utf8')
        for name in ('engine/design.py','engine/guide_rules.py','original/source_manifest.json'):
            target=self.project/'v5'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(PROJECT/'v5'/name,target)
        self.patch=patch.object(v5_store,'PROJECT',self.project);self.patch.start();self.addCleanup(self.patch.stop)
        self.before={p:digest(p) for p in self.root.rglob('*') if p.is_file()}
    def store(self):return v5_store.V5Store(self.run,self.cache)
    def wait(self,s):
        end=time.monotonic()+30
        while True:
            state=s.status()
            if state['state']!='indexing':return state
            if time.monotonic()>end:self.fail('Index timeout: '+str(state))
            time.sleep(.02)
    def ready(self):
        s=self.store();self.assertEqual(self.wait(s)['state'],'ready');return s
    def test_true_geometry_and_no_invented_internal_data(self):
        s=self.ready();summary=s.summary();self.assertEqual(summary['metrics']['nets'],203)
        guides=dict(v5_store.guide_nets(self.run/'route.guide'));count=0
        for name,rects in guides.items():
            n=s.net(name);self.assertEqual(n['guides'],rects);self.assertEqual(n['edges'],[])
            self.assertEqual(n['nodes'],[]);self.assertIsNone(n['via_count'])
            self.assertTrue(all(p['node'] is None for p in n['pins']))
            count+=len(n['tsv_markers'])
        self.assertEqual(count,3);self.assertEqual(summary['metrics']['guide_tsv_count'],3)
        self.assertIsNone(summary['metrics']['overflow_local']);self.assertIsNone(summary['metrics']['tsv_count'])
        self.assertEqual(s.cell(0,0)['resources'],[]);self.assertEqual(s.tiles({'view':'thermal'})['tiles'],[])
        self.assertEqual(sum(t['value'] for t in s.tiles({'view':'density'})['tiles']),3)
        self.assertTrue(all(not n['edges'] for n in s.geometry({})['nets']))
        self.assertGreater(s.pins({'tier':'top'})['total'],0)
        self.assertGreater(s.pins({'layer':'1'})['total'],0)
        self.assertEqual({p:digest(p) for p in self.before},self.before)
        self.assertFalse((self.run/'result.json').exists())
    def test_reusable_cache_and_invalidation(self):
        s=self.ready()
        with patch.object(v5_store,'parser_module',side_effect=AssertionError('unnecessary reparse')):
            other=self.store();self.assertEqual(other.status()['state'],'ready');other.net(other.search({})['items'][0]['net'])
        self.run.joinpath('route.guide').write_bytes(self.run.joinpath('route.guide').read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'changed'):s.summary()
        changed=self.store();self.assertNotEqual(s.token,changed.token)
        self.assertIn('SHA256 mismatch',self.wait(changed)['error']);self.assertFalse(changed.snapshot.exists())
    def test_missing_incomplete_and_corrupt_receipts(self):
        p=self.run/'run.json';r=json.loads(p.read_text());r['status']='INCOMPLETE';p.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,'incomplete'):self.store()
        r['status']='ROUTED';p.write_text(json.dumps(r));self.run.joinpath('route.run.json').write_text('{}')
        s=self.store();self.assertEqual(self.wait(s)['state'],'error');self.assertFalse(s.snapshot.exists())
        self.run.joinpath('route.guide').unlink()
        with self.assertRaises(FileNotFoundError):self.store()
    def test_truncated_guide_is_not_published(self):
        p=self.run/'route.guide';p.write_bytes(p.read_bytes().rstrip()[:-1]);r=json.loads(self.run.joinpath('run.json').read_text());r['guide_sha256']=digest(p);self.run.joinpath('run.json').write_text(json.dumps(r))
        s=self.store();self.assertIn('Truncated Guide',self.wait(s)['error']);self.assertFalse(s.snapshot.exists())
    def test_ambiguous_tsv_is_na_not_fabricated(self):
        original=v5_store.parser_module
        def parser(name):
            m=original(name)
            if name=='guide_rules.py':m.select_tsv=lambda *a: (_ for _ in ()).throw(ValueError('ambiguous test'))
            return m
        with patch.object(v5_store,'parser_module',side_effect=parser):s=self.ready()
        self.assertEqual(s.summary()['guide_tsv_ambiguous_nets'],3)
        for item in s.search({'cross':'1'})['items']:
            n=s.net(item['net']);self.assertIsNone(n['tsv_count']);self.assertEqual(n['tsv_markers'],[])
    def test_http_load_switch_and_error_recovery(self):
        s=self.ready()
        class Quiet(app.Handler):
            def log_message(self,*args):pass
        def selected(key):
            if key!='toy/toy_v5':raise ValueError('Unknown run')
            return self.run
        with patch.object(app,'selected',side_effect=selected),patch.object(app,'CACHE',self.cache),patch.object(app,'catalog',return_value={}):
            server=app.ThreadingHTTPServer(('127.0.0.1',0),Quiet);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def get(path):
                try:
                    with urlopen('http://127.0.0.1:'+str(server.server_port)+path) as response:return response.status,json.load(response)
                except HTTPError as e:return e.code,json.load(e)
            try:
                status,data=get('/api/run?id=toy/toy_v5');self.assertEqual(status,200);self.assertTrue(data['indexed'])
                self.assertEqual(get('/api/summary?id=wrong/run')[0],400)
                self.assertEqual(get('/api/summary?id=toy/toy_v5&version=stale')[0],409)
                status,data=get('/api/summary?id=toy/toy_v5');self.assertEqual(status,200);self.assertEqual(data['metrics']['nets'],203)
                self.assertEqual(get('/api/net?id=toy/toy_v5&net=missing')[0],400)
                self.assertEqual(get('/api/trace?id=toy/toy_v5')[1]['events'],[])
                self.assertEqual(get('/api/nets?id=toy/toy_v5&limit=1')[1]['total'],203)
            finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
