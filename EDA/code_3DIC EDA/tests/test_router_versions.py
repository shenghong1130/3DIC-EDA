"""Temporary Mock registries/binaries only; no routing, old runs or Git writes."""
import contextlib,hashlib,io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import router_versions as versions
import manage_router_versions as manager
import run_case

def digest(data):return hashlib.sha256(data).hexdigest()
def record(v,data):
    return {'path':'build/router_'+v,'sha256':digest(data),'git_commit':'a'*40,'compiler':'mock compiler',
            'build_command':['g++'],'verified':True,'verification':{'method':versions.METHOD,
            'rebuilt_sha256':digest(data),'source_sha256':{'src/router.cpp':digest(b'source')},
            'cli_help_returncode':0,'cli_required_options_verified':True}}

class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.file=self.root/'versions.json';self.data={'schema_version':1,'versions':{'v1':record('v1',b'v1'),'v2':record('v2',b'v2')}};self.save()
    def save(self):self.file.write_text(json.dumps(self.data),encoding='utf8')
    def identify(self,data):return versions.identify_router(digest(data),self.file)
    def test_v1_v2_match_bytes(self):
        for v in ['v1','v2']:self.assertEqual(self.identify(v.encode())['router_version'],v)
    def test_unknown_and_changed_bytes(self):
        self.assertEqual(self.identify(b'not registered')['router_version'],'UNKNOWN')
        self.assertEqual(self.identify(b'v2 modified')['router_version'],'UNKNOWN')
    def test_missing_invalid_and_unreadable(self):
        self.file.unlink();self.assertEqual(self.identify(b'v2')['router_version_status'],'REGISTRY_MISSING')
        for bad in ['{broken','[]','{"schema_version":999,"versions":{}}','{"schema_version":1,"versions":{"v2":[]}}']:
            self.file.write_text(bad);self.assertEqual(self.identify(b'v2')['router_version_status'],'REGISTRY_INVALID')
        self.assertEqual(versions.identify_router(None,self.file)['router_version_status'],'BINARY_UNREADABLE')
    def test_verified_flag_is_not_enough(self):
        self.data['versions']['v2'].pop('verification');self.save();self.assertEqual(self.identify(b'v2')['router_version'],'UNKNOWN')
    def test_unverified_and_inconsistent_proof(self):
        self.data['versions']['v2']['verified']=False;self.save();self.assertEqual(self.identify(b'v2')['router_version'],'UNKNOWN')
        self.data['versions']['v2']['verified']=True;self.data['versions']['v2']['verification']['rebuilt_sha256']='0'*64;self.save()
        self.assertEqual(self.identify(b'v2')['router_version'],'UNKNOWN')
    def test_ambiguous_never_picks_first(self):
        self.data['versions']['v3']=record('v3',b'v2');self.save();r=self.identify(b'v2')
        self.assertEqual(r['router_version'],'UNKNOWN');self.assertEqual(r['router_version_status'],'AMBIGUOUS');self.assertEqual(r['router_version_matches'],['v2','v3'])

class RunMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='router version space ');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        for folder in ['build','configs','src','include','inputs']:(self.root/folder).mkdir()
        for name in ['case.def','case.lef','thermal.json']:(self.root/'inputs'/name).write_text('mock input')
        (self.root/'router').write_bytes(b'v1');(self.root/'build/router_v1').write_bytes(b'v1');(self.root/'build/router_v2').write_bytes(b'v2')
        (self.root/'configs/default.json').write_text('{"wire":1,"time_limit":999}',encoding='utf8')
        self.registry=self.root/'build/router_versions.json';self.registry.write_text(json.dumps({'schema_version':1,'versions':{'v1':record('v1',b'v1'),'v2':record('v2',b'v2')}}))
        self.target=self.root/'outputs/runs/toy/new_label';self.launch_record=None
    def invoke(self,router=None,code=1,status='FINISHED'):
        inputs={'toy':{'def':str(self.root/'inputs/case.def'),'lef':str(self.root/'inputs/case.lef'),'thermal':str(self.root/'inputs/thermal.json')}}
        def launch(*args,**kwargs):
            self.launch_record=json.loads((self.target/'run.json').read_text(encoding='utf8'))
            self.assertEqual(self.launch_record['status'],'RUNNING')
            if code==0:
                (self.target/'route.guide').write_text('mock Guide');(self.target/'result.json').write_text('{}')
            return {'returncode':code,'runtime_seconds':.001,'process_status':status}
        args=['run_case.py','--case','toy','--backend','wsl','--label','new_label','--config',str(self.root/'configs/default.json'),'--time-limit','12','--no-monitor']
        if router is not None:args+=['--router',str(router)]
        out=io.StringIO()
        with patch.object(run_case,'PROJECT',self.root),patch.object(run_case,'OUTPUTS',self.root/'outputs'),patch.object(run_case,'catalog',return_value=inputs),patch.object(run_case,'run_dir',return_value=self.target),patch.object(run_case,'timed_process',side_effect=launch),patch.object(versions,'PROJECT',self.root),patch.object(versions,'REGISTRY',self.registry),patch.object(sys,'argv',args),contextlib.redirect_stdout(out):
            result=run_case.main()
        return result,json.loads((self.target/'run.json').read_text(encoding='utf8')),out.getvalue()
    def test_default_binary_and_config_override_on_failure(self):
        code,r,text=self.invoke();self.assertEqual(code,1);self.assertEqual(r['router_version'],'v1');self.assertEqual(r['router_binary'],'router')
        self.assertEqual(r['config_path'],'configs/default.json');self.assertEqual(r['config']['time_limit'],999)
        self.assertEqual(r['effective_time_limit_seconds'],12);self.assertEqual(r['config_overrides'],{'time_limit':12})
        self.assertEqual(self.launch_record['router_version'],'v1');self.assertIn('Router Version : V1',text)
        self.assertIn('Results',text);self.assertIn('CLI Overrides',text);self.assertEqual(r['router_sha256'],digest(b'v1'))
        self.assertIn('source_sha256',r);self.assertIn('command',r)
    def test_selected_v2_success_and_renamed_file(self):
        # Filename deliberately contains no version tag.
        other=self.root/'build/renamed_router';other.write_bytes(b'v2')
        code,r,text=self.invoke(other,code=0);self.assertEqual(code,0);self.assertEqual(r['status'],'ROUTED');self.assertEqual(r['router_version'],'v2')
        self.assertEqual(r['router_binary'],'build/renamed_router');self.assertIn('Router Version : V2',text)
    def test_selected_v1(self):
        _,r,_=self.invoke(self.root/'build/router_v1');self.assertEqual(r['router_version'],'v1')
    def test_missing_registry_preserves_execution_and_timeout_metadata(self):
        self.registry.unlink();_,r,text=self.invoke(status='TIMEOUT',code=124)
        self.assertEqual(r['router_version'],'UNKNOWN');self.assertEqual(r['router_version_status'],'REGISTRY_MISSING')
        self.assertEqual(self.launch_record['config_path'],'configs/default.json');self.assertEqual(r['process_status'],'TIMEOUT')
        self.assertIn('Router Version : UNKNOWN',text)
    def test_corrupt_registry_does_not_break_old_flow(self):
        self.registry.write_text('{bad');code,r,_=self.invoke(code=0)
        self.assertEqual(code,0);self.assertEqual(r['status'],'ROUTED');self.assertEqual(r['router_version_status'],'REGISTRY_INVALID')
    def test_file_named_v2_with_wrong_bytes_is_unknown(self):
        (self.root/'build/router_v2').write_bytes(b'unknown binary');_,r,_=self.invoke(self.root/'build/router_v2')
        self.assertEqual(r['router_version'],'UNKNOWN')
    def test_ambiguous_version_is_displayed_and_persisted(self):
        data=json.loads(self.registry.read_text());data['versions']['v3']=record('v3',b'v1');self.registry.write_text(json.dumps(data))
        _,r,text=self.invoke();self.assertEqual(r['router_version'],'UNKNOWN');self.assertEqual(r['router_version_status'],'AMBIGUOUS')
        self.assertEqual(r['router_version_matches'],['v1','v3']);self.assertIn('Ambiguous SHA256',text)

class RegistrationSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'build').mkdir();(self.root/'docs').mkdir();self.file=self.root/'build/router_versions.json'
        self.source=self.root/'candidate';self.source.write_bytes(b'\x7fELFmock')
    @contextlib.contextmanager
    def isolated(self):
        with patch.object(manager,'PROJECT',self.root),patch.object(manager,'REGISTRY',self.file),patch.object(manager,'MIRROR',self.root/'docs/router_versions.json'),patch.object(manager,'load_registry',side_effect=lambda:versions.load_registry(self.file)),patch.object(manager,'commit_id',return_value='a'*40),patch.object(manager,'git_sources',return_value={'src/router.cpp':b'source'}):yield
    def test_existing_binary_never_overwritten(self):
        destination=self.root/'build/router_v3';destination.write_bytes(b'preserve me')
        with self.isolated(),self.assertRaisesRegex(ValueError,'already exists'):manager.register('v3',self.source,'HEAD','new')
        self.assertEqual(destination.read_bytes(),b'preserve me');self.assertFalse(self.file.exists())
    def test_cannot_alias_v2_as_v3(self):
        r=record('v2',self.source.read_bytes());self.file.write_text(json.dumps({'schema_version':1,'versions':{'v2':r}}));original=self.file.read_bytes()
        with self.isolated(),self.assertRaisesRegex(ValueError,'already registered as v2'):manager.register('v3',self.source,'HEAD','new')
        self.assertFalse((self.root/'build/router_v3').exists());self.assertEqual(self.file.read_bytes(),original)
    def test_bad_provenance_does_not_publish_version(self):
        with self.isolated(),patch.object(manager,'rebuild',side_effect=ValueError('Rebuilt SHA256 differs')),self.assertRaisesRegex(ValueError,'SHA256 differs'):manager.register('v3',self.source,'HEAD','new')
        self.assertFalse(self.file.exists());self.assertFalse((self.root/'build/router_v3').exists())
    def test_final_entry_cannot_skip_committed_source_proof(self):
        destination=self.root/'build/router_v3_final';destination.write_bytes(self.source.read_bytes());original=destination.read_bytes()
        with self.isolated(),patch.object(manager,'rebuild',side_effect=ValueError('Rebuilt SHA256 differs')),self.assertRaisesRegex(ValueError,'SHA256 differs'):
            manager.register('v3',destination,'HEAD','Final',final=True)
        self.assertEqual(destination.read_bytes(),original);self.assertFalse(self.file.exists())
    def test_final_entry_is_not_a_v1_v2_alias(self):
        with self.isolated(),self.assertRaisesRegex(ValueError,'only valid for v3'):manager.register('v2',self.source,'HEAD','invalid',final=True)

if __name__=='__main__':unittest.main()
