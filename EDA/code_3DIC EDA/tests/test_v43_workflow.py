"""V4.3 experiment safety: no real Router, scorer, WSL or historical file writes."""
import sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import validate_v4_3 as v

class WorkflowSafety(unittest.TestCase):
 def probe(self,args,**kwargs):
  if args[-1]=='comm,args':return SimpleNamespace(returncode=0,stdout=self.processes)
  return SimpleNamespace(returncode=0,stdout=b'MemAvailable: 10000000 kB\nSwapFree: 6000000 kB\nboot-identity\n')
 def setUp(self):self.processes=b'COMMAND COMMAND\npython python idle\n'
 def allowed(self,gib):
  with patch.object(v,'windows_memory',return_value={'commit_available_bytes':int(gib*2**30)}),patch.object(v.subprocess,'run',side_effect=self.probe),patch.object(v.shutil,'disk_usage',return_value=SimpleNamespace(free=30*2**30)):
   return v.gate('open_3d_case005')['allowed']
 def test_six_gib_gate_not_lowered(self):self.assertFalse(self.allowed(5.99));self.assertTrue(self.allowed(6))
 def test_busy_router_blocks(self):self.processes=b'COMMAND COMMAND\nrouter_v4_2 router_v4_2 --case elsewhere\n';self.assertFalse(self.allowed(10))
 def test_busy_scorer_blocks(self):self.processes=b'COMMAND COMMAND\nreference_20261103 reference_20261103 elsewhere\n';self.assertFalse(self.allowed(10))
 def test_existing_experiment_never_overwritten(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(v,'ROOT',Path(tmp)):
   directory=Path(tmp)/'ablations/toy/joint';directory.mkdir(parents=True);sentinel=directory/'route.guide';sentinel.write_bytes(b'original history')
   with patch.object(v,'timed_process') as run:
    with self.assertRaises(FileExistsError):v.experiment('toy','joint')
    run.assert_not_called();self.assertEqual(sentinel.read_bytes(),b'original history')
 def test_existing_formal_never_overwritten(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(v,'ROOT',Path(tmp)):
   directory=Path(tmp)/'formal/open_3d_case005';directory.mkdir(parents=True);sentinel=directory/'resource_gate.json';sentinel.write_bytes(b'keep')
   with patch.object(v,'launch') as run:
    with self.assertRaises(FileExistsError):v.formal('open_3d_case005')
    run.assert_not_called();self.assertEqual(sentinel.read_bytes(),b'keep')
if __name__=='__main__':unittest.main()
