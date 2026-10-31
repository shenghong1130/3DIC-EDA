"""Executable reference checks using synthetic DEF/LEF/Guide, never Router."""
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from evaluate_reference import build, execute


class ReferenceScorerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='gr_reference_tests_')
        cls.directory=Path(cls.temp.name);cls.executable=cls.directory/'reference'
        cls.backend='wsl' if os.name=='nt' else 'native'
        try:build(cls.executable,cls.backend)
        except Exception:
            cls.temp.cleanup();raise

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def setUp(self):
        self.temp_case=tempfile.TemporaryDirectory(prefix='fixture_',dir=self.directory)
        self.directory_case=Path(self.temp_case.name)
        lef='VERSION 5.8 ;\n'
        for l in range(1,21):
            lef+=f'LAYER metal{l}\n TYPE ROUTING ;\n DIRECTION '+('HORIZONTAL' if l%2 else 'VERTICAL')+f' ;\n PITCH 0.2 ;\n WIDTH 0.1 ;\nEND metal{l}\n'
        lef+='LAYER tsv_layer\n TYPE CUT ;\nEND tsv_layer\n'
        for l in range(1,20):
            lef+=f'VIA V{l} DEFAULT\n LAYER metal{l} ;\n RECT -0.1 -0.1 0.1 0.1 ;\n LAYER '+('tsv_layer ;\n RECT -2.5 -2.5 2.5 2.5 ;\n LAYER ' if l==10 else '')+f'metal{l+1} ;\n RECT -0.1 -0.1 0.1 0.1 ;\nEND V{l}\n'
        lef+='MACRO DUMMY\n SIZE 1 BY 1 ;\n PIN A\n PORT\n LAYER metal1 ;\n RECT 0.1 0.1 0.2 0.2 ;\n END\n END A\nEND DUMMY\nEND LIBRARY\n'
        self.inputs={k:self.directory_case/('input.'+k) for k in ['lef','def','thermal']}
        self.inputs['lef'].write_text(lef)
        self.make_def()
        self.inputs['thermal'].write_text(json.dumps({'meta':{'ambientTemp':25},'nodes':[[.5,.5,0,26],[1.5,.5,0,28]]}))
        self.guide=self.directory_case/'route.guide';self.guide.write_text('n\n(\n0 0 3000 1000 metal1\n)\n')

    def tearDown(self):self.temp_case.cleanup()

    def make_def(self,cross=False):
        tracks='\n'.join(f'TRACKS '+('Y' if l%2 else 'X')+f' 50 DO 100 STEP 200 LAYER metal{l} ;' for l in range(1,21))
        first,second=(10,11) if cross else (1,1)
        xy1,xy2=('6500 6500','6500 6500') if cross else ('500 500','2500 500')
        de=f'VERSION 5.8 ;\nDESIGN ref ;\nUNITS DISTANCE MICRONS 1000 ;\nDIEAREA ( 0 0 ) ( 20000 20000 ) ;\n{tracks}\nGCELLGRID X 0 DO 21 STEP 1000 ;\nGCELLGRID Y 0 DO 21 STEP 1000 ;\nCOMPONENTS 0 ;\nEND COMPONENTS\nPINS 2 ;\n- a + NET n + PORT + LAYER metal{first} ( -1 -1 ) ( 1 1 ) + FIXED ( {xy1} ) N ;\n- b + NET n + PORT + LAYER metal{second} ( -1 -1 ) ( 1 1 ) + FIXED ( {xy2} ) N ;\nEND PINS\nNETS 1 ;\n- n ( PIN a ) ( PIN b ) ;\nEND NETS\nEND DESIGN\n'
        self.inputs['def'].write_text(de)

    def score(self):
        out=self.directory_case/('out'+str(len(list(self.directory_case.glob('out*')))))
        out.mkdir()
        return execute(self.executable,self.inputs,self.guide,out,self.backend,256,30)[0],out

    def test_valid_and_repeat_deterministic(self):
        a,out=self.score();b,_=self.score()
        self.assertEqual(a['static_legality'],'REFERENCE_VALID')
        for key in ['overflow','by_layer','wirelength_dbu','thermal_correlation','physical_workspace']:
            self.assertEqual(a[key],b[key],key)
        self.assertIsNone(a['congestion_score']);self.assertIsNone(a['total_score'])
        self.assertTrue((out/'resources.csv').is_file())

    def test_duplicate_rectangles_do_not_duplicate_demand(self):
        a,_=self.score();text=self.guide.read_text();self.guide.write_text(text.replace(')\n','0 0 3000 1000 metal1\n)\n'))
        b,_=self.score();self.assertEqual(a['by_layer'],b['by_layer'])

    def test_nonpreferred_rectangle_and_wrong_axis_stitch_rejected(self):
        self.guide.write_text('n\n(\n0 0 3000 2000 metal1\n)\n')
        a,_=self.score();self.assertIn('PREFERRED_DIRECTION',a['error_counts'])
        self.guide.write_text('n\n(\n0 0 3000 1000 metal1\n0 1000 3000 2000 metal1\n)\n')
        a,_=self.score();self.assertIn('NET_CONNECTIVITY',a['error_counts'])

    def test_missing_pin_and_disconnected_path_rejected(self):
        self.guide.write_text('n\n(\n0 0 1000 1000 metal1\n2000 0 3000 1000 metal1\n)\n')
        a,_=self.score();self.assertIn('NET_CONNECTIVITY',a['error_counts']);self.assertIn('PIN_ACCESS',a['error_counts'])

    def test_tsv_pin_obs_exclusion(self):
        self.make_def(cross=True);self.guide.write_text('n\n(\n6000 6000 7000 7000 metal10\n6000 6000 7000 7000 metal11\n)\n')
        a,_=self.score();self.assertIn('TSV_PIN_OBS',a['error_counts']);self.assertEqual(a['tsv_count'],1)

    def test_missing_tsv_via_declaration(self):
        self.make_def(cross=True)
        self.inputs['lef'].write_text(re.sub(r'VIA V10 DEFAULT.*?END V10\n','',self.inputs['lef'].read_text(),flags=re.S))
        self.guide.write_text('n\n(\n6000 6000 7000 7000 metal10\n6000 6000 7000 7000 metal11\n)\n')
        a,_=self.score();self.assertIn('TSV_DECLARATION',a['error_counts'])

    def test_bad_guide_geometry_and_unknown_net(self):
        self.guide.write_text('n\n(\n0 0 0 1000 metal1\n)\n')
        a,_=self.score();self.assertIn('GUIDE_RECTANGLE',a['error_counts'])
        self.guide.write_text('unknown\n(\n0 0 1000 1000 metal1\n)\n')
        with self.assertRaises(RuntimeError):self.score()

    def test_no_track_pin_minimum_and_own_net_exemption(self):
        a,out=self.score();self.assertEqual(a['by_layer']['1']['fixed_demand'],2)
        self.assertEqual(a['by_layer']['1']['routing_demand'],1)


if __name__=='__main__':unittest.main()
