"""Local, sequential V44 research. No Git mutation, exclusive outputs, frozen scorer."""
import argparse,json,subprocess,sys,time,uuid,shutil
from pathlib import Path
from common import PROJECT,sha256,catalog,linux_path,backend_command,timed_process
from diagnostics import windows_memory
from evaluate_reference_20261103 import source_identity,execute as reference
from compare_v36 import small_metadata
from analyze_v44 import save
ROOT=PROJECT/'outputs/reports/v4_4_congestion_research'
DEV=PROJECT/'outputs/development/v4_4'
FLAGS=['GR_V3_FINAL','GR_V3_5','GR_V3_6','GR_V4','GR_V4_2','GR_V4_3','GR_V4_4']
REFERENCE_SOURCE='9a33e788a847f356bdea51fc596ea6fe66e1d22f606c049bf7eb62021caf8b51'

def gate(large=False):
    from validate_v4_3 import gate as check
    minimum=(6 if large else 2)*2**30
    before=windows_memory()
    if before.get('commit_available_bytes',0)<minimum:
        return {'allowed':False,'windows':before,'required_bytes':minimum,'reason':'Insufficient host Commit before WSL probe; do not start WSL'}
    try:
        result=check('open_3d_case005' if large else 'open_3d_case001')
        # A cold/restarted WSL itself consumes Commit. Its startup cost must
        # not be mistaken for spare capacity available to the compiler/Router.
        after=windows_memory();result['windows_before_probe']=before;result['windows_after_probe']=after
        result['allowed']=result['allowed'] and after.get('commit_available_bytes',0)>=minimum
        return result
    except (OSError,subprocess.TimeoutExpired) as e:
        return {'allowed':False,'windows':windows_memory(),'environment_error':str(e),'reason':'WSL probe unavailable; do not launch'}

def launch(args,path,timeout=600):
    path.parent.mkdir(parents=True,exist_ok=True);t=time.monotonic()
    with path.open('xb') as f:p=subprocess.run(backend_command(args,'wsl',cwd=PROJECT),stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
    save(path.with_suffix('.receipt.json'),{'command':args,'returncode':p.returncode,'seconds':time.monotonic()-t})
    print(path.name,p.returncode,round(time.monotonic()-t,2),flush=True)
    if p.returncode:raise RuntimeError('Failed, evidence retained: '+str(path))

def build(attempt='initial'):
    if not attempt or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in attempt):raise ValueError('Invalid build attempt')
    logs=ROOT/'build_attempts'/attempt
    check=gate();save(logs/'resource_gate.json',check)
    if not check['allowed']:print('BLOCKED_RESOURCE');return
    if (PROJECT/'build/router_v4_4').exists():raise ValueError('Protect existing binary')
    assert source_identity()[0]==REFERENCE_SOURCE
    folder=DEV/'cmake'
    launch(['cmake','-S','.', '-B',linux_path(folder),'-DCMAKE_BUILD_TYPE=Release','-DCMAKE_CXX_FLAGS_RELEASE=-O2','-DBUILD_TESTING=ON'],logs/'cmake.log')
    for name in ['router_v4_4','v44_regression']:
        check=gate();save(logs/(name+'_gate.json'),check)
        if not check['allowed']:print('BLOCKED_RESOURCE',name);return
        launch(['cmake','--build',linux_path(folder),'--target',name,'--parallel','1'],logs/(name+'.log'),900)
    launch([linux_path(folder/'v44_regression')],logs/'v44_regression_test.log',180)
    # Copy only after real compilation and regression success. Root router is untouched.
    with (PROJECT/'build/router_v4_4').open('xb') as f:f.write((folder/'router_v4_4').read_bytes())
    launch(['chmod','+x',linux_path(PROJECT/'build/router_v4_4')],logs/'chmod.log')
    receipt={'status':'CANDIDATE','verified':False,'release_status':'NOT_RELEASED','path':'build/router_v4_4',
             'config_path':'configs/v4_4.json','sha256':sha256(PROJECT/'build/router_v4_4'),
             'config_sha256':sha256(PROJECT/'configs/v4_4.json'),'source_sha256':{p.relative_to(PROJECT).as_posix():sha256(p) for folder in ['src','include'] for p in sorted((PROJECT/folder).glob('*')) if p.is_file()},
             'flags':FLAGS,'reference_source_sha256':source_identity()[0],
             'base_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT,text=True).strip()}
    save(ROOT/'build.json',receipt);save(PROJECT/'build/router_v4_4.build.json',receipt)
    p=PROJECT/'docs/router_versions.json';r=json.loads(p.read_text(encoding='utf8'))
    if any('v4_4' in r.get(k,{}) for k in ['versions','candidates']):raise ValueError('Registry conflict')
    r.setdefault('candidates',{})['v4_4']=receipt
    p.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf8')

def tests():
    check=gate();save(ROOT/'tests_resource_gate.json',check)
    if not check['allowed']:print('BLOCKED_RESOURCE');return
    folder=DEV/'cmake'
    for name in ['core_tests','physical_resources_tests','rules_20261103','v42_regression','v4_regression','v36_regression','v35_regression','v43_regression','router_v4_3']:
        check=gate();save(ROOT/'tests'/(name+'_resource_gate.json'),check)
        if not check['allowed']:print('BLOCKED_RESOURCE',name);return
        launch(['cmake','--build',linux_path(folder),'--target',name,'--parallel','1'],ROOT/'tests'/(name+'_build.log'),900)
        if name!='router_v4_3':launch([linux_path(folder/name)],ROOT/'tests'/(name+'.log'),240)
    match=sha256(folder/'router_v4_3')==sha256(PROJECT/'build/router_v4_3')
    save(ROOT/'tests/v43_byte_regression.json',{'identical':match,'rebuilt_sha256':sha256(folder/'router_v4_3'),'frozen_sha256':sha256(PROJECT/'build/router_v4_3')})
    if not match:raise ValueError('Historical V43 binary compilation changed')

def experiment(case,variant,budget):
    if variant not in ['joint','cost','negotiation','layer','tsv']:raise ValueError('Unknown component')
    if not 0<=budget<=.05:raise ValueError('Wire budget must be finite and 0..0.05')
    if case not in catalog():raise ValueError('Unknown Case')
    receipt=json.loads((ROOT/'build.json').read_text())
    if sha256(PROJECT/'build/router_v4_4')!=receipt['sha256']:raise ValueError('Router identity changed')
    if any(sha256(PROJECT/p)!=s for p,s in receipt['source_sha256'].items()):raise ValueError('Source changed since tested build')
    if case not in ['toy','sample001']:
        for small in ['toy','sample001']:
            tested=list((ROOT/'experiments'/small).glob('*/summary.json'))
            if not any(json.loads(p.read_text())['reference']['static_legality']=='REFERENCE_VALID' for p in tested):
                raise ValueError('Small-case validation is required before larger experiments')
    if case in ['open_3d_case003','open_3d_case005']:
        candidates=list((ROOT/'experiments/open_3d_case001').glob('joint_*/summary.json'))
        baseline=json.loads((PROJECT/'outputs/reports/scoring/reference_20261103/open_3d_case001/case001_v4_3/reference_report.json').read_text())
        approved=False
        for p in candidates:
            row=json.loads(p.read_text());r=row['reference']
            if row['wire_budget']==budget and r['static_legality']=='REFERENCE_VALID' and r['reference_source_sha256']==REFERENCE_SOURCE and r['overflow']<baseline['overflow'] and r['normalized_overflow_squared']<baseline['normalized_overflow_squared'] and r['wirelength_dbu']<=baseline['wirelength_dbu']*(1+budget) and r['thermal_correlation']>=baseline['thermal_correlation']-1e-5:approved=True
        if not approved:raise ValueError('Measured case001 quality gate required; no large Router started')
    folder=ROOT/'experiments'/case/(variant+'_'+str(budget).replace('.','p'));folder.mkdir(parents=True,exist_ok=False)
    check=gate(case.endswith('005'));save(folder/'resource_gate.json',check)
    if not check['allowed']:print('BLOCKED_RESOURCE',case,variant,budget,flush=True);return
    assert source_identity()[0]==REFERENCE_SOURCE
    cfg=json.loads((PROJECT/'configs/v4_4.json').read_text());cfg['v44_wire_growth']=budget
    for module in ['cost','negotiation','layer','tsv']:cfg['v44_'+module]=int(variant in ['joint',module])
    config=folder/'config.json';save(config,cfg)
    inputs={k:Path(catalog()[case][k]) for k in ['def','lef','thermal']};binary=PROJECT/'build/router_v4_4'
    timeout=3600 if case.endswith('003') else 14400 if case.endswith('005') else 600 if case.endswith('001') and case!='sample001' else 180
    guide=folder/'route.guide';args=[linux_path(binary),'--def',linux_path(inputs['def']),'--lef',linux_path(inputs['lef']),'--thermal-report',linux_path(inputs['thermal']),'--config',linux_path(config),'--output',linux_path(guide),'--metrics',linux_path(folder/'result.json'),'--time-limit',str(timeout),'--progress-dir',linux_path(folder)]
    args=['python3',linux_path(PROJECT/'scripts/linux_supervisor.py'),'--directory',linux_path(folder),'--run-id',uuid.uuid4().hex,'--interval','5','--lease-seconds','60','--',*args]
    command=backend_command(args,'wsl',cwd=PROJECT,timeout=timeout+10,time_file=folder/'resource_usage.txt')
    ident={'case':case,'variant':variant,'experimental_only':True,'router_sha256':sha256(binary),'config_sha256':sha256(config),'config':cfg,'input_sha256':{k:sha256(p) for k,p in inputs.items()},'command':command,'build':json.loads((ROOT/'build.json').read_text())};save(folder/'identity.json',ident)
    execution=timed_process(command,folder/'router.log',timeout+45,monitor_dir=folder,monitor_interval=5,stall_after=30,native_resources=False);save(folder/'execution.json',execution)
    if execution['returncode'] or not guide.exists():raise ValueError('Router failed; no retry: '+str(folder))
    score=folder/'reference';score.mkdir();report,cmd=reference(PROJECT/'outputs/development/v4_3/reference_20261103',inputs,guide,score,'wsl',3072 if case.endswith('003') else 4096 if case.endswith('005') else 1536,3600 if case.endswith('003') else 7200 if case.endswith('005') else 600)
    save(score/'identity.json',{'guide_sha256':sha256(guide),'input_sha256':ident['input_sha256'],'reference_source_sha256':source_identity()[0],'command':cmd})
    from validate_v4_3 import output_audit
    row={'case':case,'variant':variant,'wire_budget':budget,'router_status':'ROUTED','reference':report,'execution':execution,'guide_sha256':sha256(guide),'algorithm':small_metadata(folder/'result.json'),'output_audit':output_audit(folder/'result.json')}
    save(folder/'summary.json',row)
    print('EXPERIMENT',case,variant,budget,report['static_legality'],report['overflow'],report['normalized_overflow_squared'],report['wirelength_dbu']/report['dbu_per_micron'],flush=True)
    if report['static_legality']!='REFERENCE_VALID':raise ValueError('Reference invalid; preserve evidence')

def main():
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['build','tests','experiment']);p.add_argument('--case');p.add_argument('--variant',default='joint');p.add_argument('--budget',type=float,default=.01);p.add_argument('--attempt',default='initial');a=p.parse_args()
    if a.phase=='build':build(a.attempt)
    elif a.phase=='tests':tests()
    else:experiment(a.case,a.variant,a.budget)

if __name__=='__main__':main()
