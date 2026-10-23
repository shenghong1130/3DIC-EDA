from pathlib import Path
import unittest,json,subprocess,sys,tempfile,os,math
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'scripts'))
from common import catalog,linux_path,safe_label
class Helpers(unittest.TestCase):
    def test_dataset_versions(self):
        c=catalog();self.assertIn('toy',c);self.assertIn('sample001_legacy',c);self.assertNotEqual(c['sample001']['lef'],c['sample001_legacy']['lef'])
    def test_path_and_labels(self):
        self.assertEqual(safe_label('run-01'),'run-01')
        for value in ['../official','x/y','..','a b']:
            with self.assertRaises(ValueError):safe_label(value)
class ParserIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe=Path(os.environ.get('ROUTER_EXE',str(PROJECT/'outputs/router.exe' if os.name=='nt' else PROJECT/'router')))
        if not cls.exe.exists():raise unittest.SkipTest('Build native router first or set ROUTER_EXE')
    def setUp(self):
        base=PROJECT/'outputs/tests';base.mkdir(parents=True,exist_ok=True);self.tmp=tempfile.TemporaryDirectory(prefix='路径 space ',dir=base);self.dir=Path(self.tmp.name)
        lef='VERSION 5.8 ;\nUNITS\n DATABASE MICRONS 1000 ;\nEND UNITS\nPROPERTYDEFINITIONS\n LAYER LEF58_CORNERSPACING STRING ;\nEND PROPERTYDEFINITIONS\n'
        for l in [1,2]:lef+=f'LAYER metal{l}\n TYPE ROUTING ;\n DIRECTION '+('HORIZONTAL' if l==1 else 'VERTICAL')+f' ;\n WIDTH 0.1 ;\n PITCH 0.2 ;\nEND metal{l}\n'
        lef+='VIA V12 DEFAULT\n LAYER metal1 ;\n RECT 0 0 1 1 ;\n LAYER metal2 ;\n RECT 0 0 1 1 ;\nEND V12\nMACRO BUF\n SIZE 2 BY 4 ;\n ORIGIN 0 0 ;\n PIN A\n PORT\n LAYER metal1 ;\n RECT 0.1 0.2 0.3 0.4 ;\n END\n END A\nEND BUF\nEND LIBRARY\n'
        self.lef=self.dir/'input.lef';self.lef.write_text(lef,encoding='utf8')
        tracks='\n'.join(f'TRACKS {axis} 50 DO 100 STEP 100 LAYER metal{l} ;' for axis in ['X','Y'] for l in [1,2])
        de=f'VERSION 5.8 ;\nDESIGN tiny ;\nUNITS DISTANCE MICRONS 1000 ;\nDIEAREA ( 0 0 ) ( 10000 10000 ) ;\n{tracks}\nGCELLGRID X 0 DO 11 STEP 1000 ;\nGCELLGRID Y 0 DO 11 STEP 1000 ;\nVIAS 0 ;\nEND VIAS\nCOMPONENTS 2 ;\n- u1 BUF + PLACED ( 1000 2000 ) FS ;\n- u2 BUF + PLACED ( 6000 6000 ) N ;\nEND COMPONENTS\nPINS 1 ;\n- io + NET n + PORT + LAYER metal2 ( -50 -50 ) ( 50 50 ) + FIXED ( 9000 9000 ) N ;\nEND PINS\nNETS 1 ;\n- n ( u1 A ) ( u2 A ) ( PIN io ) + USE SIGNAL ;\nEND NETS\nEND DESIGN\n'
        self.de=self.dir/'input.def';self.de.write_text(de,encoding='utf8');self.thermal=self.dir/'thermal.json';self.thermal.write_text(json.dumps({'meta':{'ambientTemp':25},'nodes':[[x+.5,y+.5,0,25+x*.1] for x in range(10) for y in range(10)]}),encoding='utf8')
    def tearDown(self):self.tmp.cleanup()
    def run_router(self,*extra):
        args=[str(self.exe),'--def',str(self.de),'--lef',str(self.lef),'--thermal-report',str(self.thermal),'--output',str(self.dir/'route.guide'),'--metrics',str(self.dir/'result.json'),'--trace',str(self.dir/'trace.jsonl'),'--iterations','0',*extra]
        return subprocess.run(args,capture_output=True,timeout=30)
    def test_units_orientation_io_thermal_trace(self):
        p=self.run_router();self.assertEqual(p.returncode,0,p.stderr.decode(errors='replace'));d=json.loads((self.dir/'result.json').read_text(encoding='utf8'));pins={p['name']:p for p in d['nets'][0]['pins']}
        self.assertAlmostEqual(pins['u1.A']['x'],1200);self.assertAlmostEqual(pins['u1.A']['y'],5700);self.assertEqual(pins['PIN.io']['layer'],2);self.assertEqual(d['dbu_per_micron'],1000)
        self.assertAlmostEqual(d['heat'][9],25.9);self.assertIsNone(d['metrics']['thermal_correlation_local'])
        events=[json.loads(x) for x in (self.dir/'trace.jsonl').read_text().splitlines()];self.assertEqual(events[0]['event'],'parse_started');self.assertEqual(events[-1]['event'],'routing_complete');self.assertTrue(any(x['event']=='path_found' for x in events))
        state={}
        for event in events:
            if event['event']=='net_added':state[event['net']]=event
            elif event['event']=='net_removed':state.pop(event['net'],None)
        self.assertEqual(state['n']['edges'],d['nets'][0]['edges'])
    def test_missing_pin_fails_without_guide(self):
        self.de.write_text(self.de.read_text().replace('( u1 A )','( u1 missing )'),encoding='utf8');p=self.run_router();self.assertNotEqual(p.returncode,0);self.assertIn(b'Undefined macro pin',p.stderr);self.assertFalse((self.dir/'route.guide').exists())
    def test_invalid_layer_geometry_fails(self):
        self.lef.write_text(self.lef.read_text().replace('RECT 0.1 0.2 0.3 0.4','POLYGON 0.1 0.2 0.3 0.4'),encoding='utf8');p=self.run_router();self.assertNotEqual(p.returncode,0);self.assertFalse((self.dir/'route.guide').exists())
    def test_negative_config_rejected(self):
        c=self.dir/'bad.json';c.write_text('{"congestion":-1}');p=self.run_router('--config',str(c));self.assertNotEqual(p.returncode,0);self.assertFalse((self.dir/'route.guide').exists())
if __name__=='__main__':unittest.main()
