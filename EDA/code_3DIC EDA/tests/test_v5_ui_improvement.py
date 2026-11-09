"""Visualization-only regressions. Never invoke routing or scoring."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'ui'))
sys.path.insert(0,str(PROJECT/'scripts'))
from v5_geometry import estimate_paths,read_design
from v5_store import GuideIndex,parser_module
from cache_policy import trim_cache


class ImprovementTests(unittest.TestCase):
    def test_cache_quota_preserves_sources_and_current_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'route.guide';source.write_bytes(b'protected')
            old=root/('a'*24+'.sqlite');old.write_bytes(b'old')
            current=root/('v5_guide_'+'b'*24+'.json');current.write_bytes(b'new')
            trim_cache(root,keep=[current],byte_limit=3,file_limit=1)
            self.assertFalse(old.exists());self.assertEqual(source.read_bytes(),b'protected');self.assertTrue(current.exists())

    def test_estimates_stay_in_original_same_layer_rectangles(self):
        n={'guides':[[0,0,10,2,1],[30,30,32,40,2],[0,0,10,2,11]],'pins':[
            {'name':'a#RECT1','terminal':'a','layer':1,'box':[-1,0,1,1]},
            {'name':'a#RECT2','terminal':'a','layer':1,'box':[100,100,101,101]}]}
        e=estimate_paths(n,{1:{'direction':'HORIZONTAL'},2:{'direction':'VERTICAL'}})
        self.assertIn('非 Router 真实 A*',e['label'])
        for segment in e['segments']:
            g=n['guides'][segment['guide_index']]
            self.assertEqual(segment['layer'],g[4])
            for x,y in (segment['a'],segment['b']):
                self.assertTrue(g[0]<=x<=g[2] and g[1]<=y<=g[3])
        self.assertEqual(len(e['access_candidates']),1)
        self.assertEqual(e['access_candidates'][0]['rect'],'a#RECT1')
        self.assertFalse(any('edges' in s or 'via' in s for s in e['segments']))

    def test_estimate_budget(self):
        n={'guides':[[0,0,10,2,1]]*5000,'pins':[]}
        e=estimate_paths(n,{},limit=30)
        self.assertEqual(len(e['segments']),30);self.assertTrue(e['truncated'])

    def test_spatial_clipping_coverage_and_pin_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'snapshot.json'
            pin={'name':'a#RECT1','terminal':'a','layer':1,'x':.5,'y':.5,'node':None,'box':[0,0,1,1]}
            root={'die':[0,0,100,100],'x_lines':[0,10,100],'y_lines':[0,10,100],
                  'layers':[{'number':1}], 'metrics':{},'nets':[
                      {'net':'near','terminal_count':1,'cross_die':False,'pins':[pin,dict(pin,name='a#RECT2')],'guides':[[0,0,10,10,1]],'edges':[]},
                      {'net':'far','cross_die':False,'pins':[],'guides':[[50,50,60,60,1]],'edges':[]} ]}
            path.write_text(json.dumps(root),encoding='utf8');s=GuideIndex(path,Path(temp)/'cache')
            for _ in range(500):
                state=s.status()
                if state['state']!='indexing':break
                time.sleep(.01)
            self.assertEqual(state['state'],'ready',state)
            g=s.geometry({'box':'0,0,9,9','full':'1'})
            self.assertEqual([n['net'] for n in g['nets']],['near'])
            self.assertEqual(s.geometry({'box':'0,0,9,9','layer':'2'})['nets'],[])
            c=s.cell(0,0);self.assertEqual(c['resources'],[])
            self.assertEqual(c['coverage'],[{'layer':1,'rectangles':1,'nets':1}])
            self.assertEqual(s.cell(1,0)['coverage'],[]) # boundary touching is not coverage
            p=s.pins({'detail':'1'})['items'][0]['p']
            self.assertEqual(p['box'],[0,0,1,1]);self.assertEqual(p['terminal'],'a')
            self.assertEqual(s.net('near')['terminal_count'],1)

    def test_streaming_def_matches_original_case001_geometry(self):
        run=PROJECT/'outputs/runs/open_3d_case001/case001_v5/run.json'
        if not run.exists():self.skipTest('Existing case001_v5 required; never generate it')
        inputs=json.loads(run.read_text(encoding='utf8'))['inputs'];module=parser_module('design.py')
        original=module.Design(inputs['def'],inputs['lef'])
        with tempfile.TemporaryDirectory() as temp:
            streamed=read_design(module,inputs['def'],inputs['lef'],Path(temp)/'def.sqlite')
            try:
                self.assertEqual(streamed.die,original.die);self.assertEqual(streamed.grid,original.grid)
                self.assertEqual(set(streamed.nets),set(original.nets))
                for name in original.nets:
                    self.assertEqual([tuple(x) for x in streamed.nets[name]],original.nets[name])
                    for inst,pin in original.nets[name]:
                        self.assertEqual([(l,tuple(b)) for l,b in streamed.pin_shapes(inst,pin)],[(l,tuple(b)) for l,b in original.pin_shapes(inst,pin)])
            finally:streamed.ui_db.close()


if __name__=='__main__':unittest.main()
