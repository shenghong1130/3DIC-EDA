"""Same executable fixtures against the independent 20261103 ruleset."""
import tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import test_reference_scorer as legacy
import evaluate_reference_20261103 as latest

class Reference20261103Tests(legacy.ReferenceScorerTests):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='reference_20261103_tests_')
        cls.directory=Path(cls.temp.name);cls.executable=cls.directory/'reference'
        cls.backend='wsl' if __import__('os').name=='nt' else 'native'
        try:latest.build(cls.executable,cls.backend)
        except Exception:cls.temp.cleanup();raise
    def score(self):
        out=self.directory_case/('out'+str(len(list(self.directory_case.glob('out*')))))
        out.mkdir();return latest.execute(self.executable,self.inputs,self.guide,out,self.backend,256,30)[0],out
    def test_explicit_new_identity_and_unknown_official_assignment(self):
        report,_=self.score();self.assertEqual(report['ruleset'],'REFERENCE_20261103')
        self.assertFalse(report['official']);self.assertFalse(report['official_track_assignment_confirmed'])
        self.assertEqual(report['pin_assignment_policy'],'nearest_in_cell')
    def test_zero_capacity_route_rejected_but_fixed_pin_preserved(self):
        path=self.inputs['def'];text=path.read_text();text='\n'.join(line for line in text.splitlines() if not line.endswith('LAYER metal1 ;'))
        path.write_text(text);report,_=self.score()
        self.assertEqual(report['static_legality'],'REFERENCE_INVALID')
        self.assertGreater(report['error_counts'].get('ZERO_CAPACITY_ROUTE',0),0)
        self.assertGreater(report['by_layer']['1']['fixed_demand'],0)

if __name__=='__main__':unittest.main()
