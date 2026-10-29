"""UI indexing uses synthetic temporary files, never Router runs or official inputs."""
from pathlib import Path
import sys,json,tempfile,time,unittest,hashlib
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ui'))
from result_store import Store,Cursor,thermal_sample

class UIStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='ui_index_');self.root=Path(self.tmp.name);self.result=self.root/'result.json';self.cache=self.root/'cache'
        name='n中文"\\%_'
        self.nets=[{'net':name,'cross_die':False,'pins':[{'name':'端子"A','x':.5,'y':.5,'layer':1,'node':4},{'name':'B','x':.5,'y':.5,'layer':1,'node':4}],'nodes':[4,5],'edges':[[4,5]],'guides':[[0,0,2,1,1]],'tsv_node':None},
                   {'net':'later','cross_die':True,'pins':[{'name':'L','x':.5,'y':.5,'layer':10,'node':40},{'name':'U','x':.5,'y':.5,'layer':11,'node':44}],'nodes':[40,44],'edges':[[40,44]],'guides':[[0,0,1,1,10],[0,0,1,1,11]],'tsv_node':40},
                   {'net':'metadata_is_not_an_edge','cross_die':True,'pins':[],'nodes':[4,5],'edges':[[4,5]],'guides':[],'tsv_node':40}]
        self.data={'schema_version':1,'source':'independent_cpp_router','die':[0,0,2,2],'dbu_per_micron':1000,'design':'synthetic','x_lines':[0,1,2],'y_lines':[0,1,2],'layers':[{'number':i,'name':'metal'+str(i),'direction':'HORIZONTAL' if i%2 else 'VERTICAL'} for i in range(1,21)],'nets':self.nets,'instances':[{'name':'i','box':[0,0,1,1],'tiers':[1],'macro':'m'}],'resources':[[4,1,3,0,2],[40,2,1,0,0]],'heat':[25,26,27,28],'thermal_node_counts':[1,1,1,1],'tsv_density':[1,0,0,0],'ambient':25,'metrics':{'nets':3,'routed_nets':3},'warnings':[],'iterations':[],'trace_truncated':False}
        self.result.write_text(json.dumps(self.data,ensure_ascii=False,sort_keys=True,separators=(',',':')),encoding='utf8')
        self.original=self.result.read_bytes()
    def tearDown(self):self.tmp.cleanup()
    def ready(self,store):
        deadline=time.monotonic()+10;state=store.status()
        while state['state']=='indexing' and time.monotonic()<deadline:time.sleep(.01);state=store.status()
        self.assertEqual(state['state'],'ready',state);return store
    def store(self):return self.ready(Store(self.result,self.cache))
    def test_unicode_offsets_schema_and_all_net_pages(self):
        s=self.store()
        for n in self.nets:self.assertEqual(s.net(n['net']),n)
        summary=s.summary();self.assertEqual(summary['metrics']['nets'],3);self.assertEqual(summary['index_counts']['pins'],4);self.assertEqual(summary['nets'],[])
        page=s.search({'q':'%_','limit':'1'});self.assertEqual(page['total'],1);self.assertEqual(page['items'][0]['net'],self.nets[0]['net'])
        self.assertEqual(len(s.search({'offset':'2','limit':'1'})['items']),1)
        self.assertEqual(self.result.read_bytes(),self.original)
    def test_optional_v3_metadata_preserves_full_geometry(self):
        modes={'net_order_mode':2,'tree_route_mode':1,'reroute_priority_mode':1,'routing_strategy':'tree_aware'}
        self.data['algorithm']=modes;self.result.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf8')
        before=self.result.read_bytes();store=self.store()
        self.assertEqual(store.summary()['algorithm'],modes)
        for net in self.nets:self.assertEqual(store.net(net['net']),net)
        self.assertEqual(self.result.read_bytes(),before)
    def test_aggregation_exact_pin_pages_and_filters(self):
        s=self.store();overview=s.pins({});self.assertEqual(overview['total'],4)
        self.assertEqual(sum(p.get('count',1) for p in overview['items']),4)
        cluster=next(p for p in overview['items'] if p.get('cluster'))
        page=s.pins({'detail':'1','cluster':'1','layer':'1','box':','.join(map(str,cluster['box']))})
        self.assertEqual(page['total'],cluster['count']);self.assertEqual({p['p']['name'] for p in page['items']},{'端子"A','B'})
        self.assertEqual(s.pins({'cross':'1'})['total'],2)
        self.assertEqual(s.pins({'tier':'top'})['total'],1)
        self.assertEqual(s.pins({'box':'1,1,2,2'})['total'],0)
    def test_resources_tiles_and_tsv_need_real_edges(self):
        s=self.store();self.assertEqual(s.cell(0,0)['resources'],self.data['resources'])
        tiles=s.tiles({'view':'congestion','tier':'bottom'});self.assertEqual(tiles['max'],3)
        self.assertEqual(sum(t['value'] for t in s.tiles({'view':'density'})['tiles']),1)
        names=[n['net'] for n in s.geometry({})['nets']];self.assertEqual(names,['later'])
        self.assertEqual(s.instances({})['items'],self.data['instances'])
    def test_warm_cache_reuse_and_source_invalidation(self):
        s=self.store()
        with patch('result_store.Cursor',side_effect=AssertionError('full JSON import repeated')):
            reused=Store(self.result,self.cache);self.assertEqual(reused.status()['state'],'ready');reused.summary();reused.search({});reused.net('later')
        self.data['metrics']['nets']=4;self.result.write_text(json.dumps(self.data),encoding='utf8')
        changed=Store(self.result,self.cache);self.assertNotEqual(changed.token,s.token)
        with self.assertRaisesRegex(ValueError,'changed'):s.summary()
        self.ready(changed);self.assertEqual(changed.summary()['metrics']['nets'],4)
    def test_failed_import_never_publishes_index(self):
        self.result.write_bytes(self.original[:-5]);s=Store(self.result,self.cache);deadline=time.monotonic()+10
        while s.status()['state']=='indexing' and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(s.status()['state'],'error');self.assertFalse(s.path.exists());self.assertEqual(self.result.read_bytes(),self.original[:-5])
    def test_reference_preserves_true_pins_but_no_fake_route(self):
        guide=self.root/'reference.guide';guide.write_text('later\n(\n0 0 1 1 metal10\n0 0 1 1 metal11\n)\n')
        s=self.ready(Store(self.result,self.cache,guide));r=s.net('later',True)
        self.assertEqual(r['pins'],self.nets[1]['pins']);self.assertEqual(r['edges'],[]);self.assertEqual(r['guides'],self.nets[1]['guides'])
        self.assertEqual(s.summary(True)['metrics']['nets'],1);self.assertEqual(s.pins({'source':'reference'})['total'],2)
    def test_uniform_bounded_raw_thermal_sample(self):
        path=self.root/'thermal.json';path.write_text(json.dumps({'meta':{'ambientTemp':25,'unused_instances':['x\\"😀'*200000,{'nested':[1,2,3]}]},'nodes':[[i,i+1,0,25] for i in range(21001)]}))
        sample=thermal_sample(path,self.cache);self.assertLessEqual(len(sample['nodes']),10000);self.assertEqual(sample['total_nodes'],21001)
        self.assertTrue(all(p[0]==i*sample['stride'] for i,p in enumerate(sample['nodes'])))
        with patch('result_store.Cursor',side_effect=AssertionError('reparsed thermal input')):self.assertEqual(thermal_sample(path,self.cache),sample)
    def test_stream_chunk_boundaries_and_utf8_byte_positions(self):
        path=self.root/'chunks.json';values=[123456789012345,'中文\\"😀'*200000,{'pin':'端子'}]
        path.write_text(' '*(1024*1024-3)+json.dumps(values,ensure_ascii=False),encoding='utf8',newline='')
        reader=Cursor(path)
        try:
            found=list(reader.array());self.assertEqual([v[0] for v in found],values)
            with path.open('rb') as f:
                for expected,offset,length in found:f.seek(offset);self.assertEqual(json.loads(f.read(length)),expected)
            self.assertEqual(reader.char(),'');self.assertLess(reader.maximum,4*1024*1024)
        finally:reader.close()

if __name__=='__main__':unittest.main()
