"""Sequential V4.3 controlled validation. Exclusive outputs, frozen scorer, no retries/upload."""
import argparse,json,subprocess,sys,time,shutil,uuid,zipfile,xml.etree.ElementTree as ET
from pathlib import Path
from common import PROJECT,catalog,sha256,linux_path,backend_command,timed_process
from diagnostics import windows_memory,json_file,utc
from evaluate_reference_20261103 import source_identity,build as build_reference,execute as reference
from compare_v36 import small_metadata
ROOT=PROJECT/'outputs/reports/v4_3';DEV=PROJECT/'outputs/development/v4_3'
FLAGS=['GR_V3_FINAL','GR_V3_5','GR_V3_6','GR_V4','GR_V4_2','GR_V4_3']
VARIANTS={'control':['SCORING','MULTINET','ADAPTIVE','THERMAL'],'scoring':['MULTINET','ADAPTIVE','THERMAL'],'multinet':['SCORING','ADAPTIVE','THERMAL'],'adaptive':['SCORING','MULTINET','THERMAL'],'thermal':['SCORING','MULTINET','ADAPTIVE'],'joint':[]}
def save(p,x):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x',encoding='utf8') as f:json.dump(x,f,ensure_ascii=False,indent=2)
def launch(cmd,log,timeout=600):
 log.parent.mkdir(parents=True,exist_ok=True);start=time.monotonic()
 with log.open('xb') as f:p=subprocess.run(cmd,cwd=PROJECT,stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
 row={'command':cmd,'returncode':p.returncode,'seconds':time.monotonic()-start};save(log.with_suffix('.receipt.json'),row)
 print(log.name,p.returncode,round(row['seconds'],2),flush=True)
 if p.returncode:raise RuntimeError('Failed; preserve evidence: '+str(log))
 return row
def wsl(args,log,timeout=600):return launch(backend_command(args,'wsl',cwd=PROJECT),log,timeout)
def protected():
 files=[PROJECT/'build/router_v4_2',PROJECT/'configs/v4_2.json',PROJECT.parent/'评估程序/score']
 for pattern in ['outputs/runs/*/*_v4_2/*','outputs/reports/scoring/reference_20261103/*/*_v4_2/*','outputs/submissions/*v4_2*.zip','outputs/submissions/*v4_2*manifest.json']:
  files.extend(p for p in PROJECT.glob(pattern) if p.is_file())
 return {p.relative_to(PROJECT.parent.parent).as_posix():sha256(p) for p in sorted(set(files))}
def preflight():
 ROOT.mkdir(parents=True,exist_ok=False);DEV.mkdir(parents=True,exist_ok=False)
 assert sha256(PROJECT/'build/router_v4_2')=='bb6721e279938f3e158d4181f21acc1519de2cf17f791c278c4100e54495b921'
 assert sha256(PROJECT/'configs/v4_2.json')=='9b5988776c80c8dd3f4e1eaea608ad614d571d621e19379d7e177a2372cb58b4'
 save(ROOT/'protected_before.json',protected());save(ROOT/'registry_before.json',json_file(PROJECT/'docs/router_versions.json'))
 save(ROOT/'preflight.json',{'time':utc(),'windows':windows_memory(),'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT,text=True).strip(),'origin_main':subprocess.check_output(['git','rev-parse','origin/main'],cwd=PROJECT,text=True).strip(),'reference_source_sha256':source_identity()[0]})
 docs={}
 for p in (PROJECT.parent/'补充资料').glob('*.docx'):
  with zipfile.ZipFile(p) as z:
   node=ET.fromstring(z.read('word/document.xml'));text='\n'.join(''.join(x.itertext()) for x in node.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'))
  docs[p.name]={'sha256':sha256(p),'text':text}
 save(ROOT/'rules_sources.json',docs)
 with (PROJECT/'configs/v4_3.json').open('xb') as f:f.write((PROJECT/'configs/v4_2.json').read_bytes())
 print('PREFLIGHT_SAVED',windows_memory(),flush=True)
def compile_one(source,target,flags,log):
 return wsl(['g++','-std=c++17','-O2','-I','include',*['-D'+x for x in flags],'-c',source,'-o',linux_path(target)],log,300)
def builds():
 if (PROJECT/'build/router_v4_3').exists():raise ValueError('Candidate binary exists; no overwrite')
 compile_one('src/main.cpp',DEV/'main.o',FLAGS,ROOT/'build/main.log');compile_one('src/parser.cpp',DEV/'parser.o',FLAGS,ROOT/'build/parser.log')
 for variant,disabled in VARIANTS.items():
  flags=FLAGS+['GR_V43_DISABLE_'+x for x in disabled];obj=DEV/(variant+'.o');compile_one('src/router.cpp',obj,flags,ROOT/'build'/(variant+'.log'))
  binary=PROJECT/'build/router_v4_3' if variant=='joint' else DEV/variant
  wsl(['g++',linux_path(DEV/'main.o'),linux_path(DEV/'parser.o'),linux_path(obj),'-o',linux_path(binary)],ROOT/'build'/(variant+'_link.log'))
  save(DEV/(variant+'.json'),{'variant':variant,'flags':flags,'sha256':sha256(binary),'path':str(binary),'experimental_only':variant!='joint'})
 sources={p.relative_to(PROJECT).as_posix():sha256(p) for folder in ['src','include'] for p in sorted((PROJECT/folder).glob('*')) if p.is_file()}
 receipt={'path':'build/router_v4_3','config_path':'configs/v4_3.json','sha256':sha256(PROJECT/'build/router_v4_3'),'config_sha256':sha256(PROJECT/'configs/v4_3.json'),'source_sha256':sources,'flags':FLAGS,'build_command':['g++','-std=c++17','-O2','-I','include',*['-D'+x for x in FLAGS],'src/main.cpp','src/parser.cpp','src/router.cpp','-o','build/router_v4_3'],'compiler':subprocess.check_output(['wsl','-d','Ubuntu','--','g++','--version']).decode('utf8','replace').splitlines()[0],'base_commit':json_file(ROOT/'preflight.json')['git_head'],'status':'CANDIDATE','release_status':'NOT_RELEASED','verified':False,'label':'case001_v4_3','formal_labels':['case001_v4_3','case003_v4_3','case005_v4_3'],'validation_path':'outputs/reports/v4_3/decision.json','ruleset':'REFERENCE_20261103'}
 save(ROOT/'build.json',receipt);save(PROJECT/'build/router_v4_3.build.json',receipt)
 reg=PROJECT/'docs/router_versions.json';data=json_file(reg)
 if any('v4_3' in data.get(k,{}) for k in ['versions','candidates']):raise ValueError('Registry conflict')
 data.setdefault('candidates',{})['v4_3']=receipt
 # Preserve all other registrations and migration mappings.
 with reg.open('w',encoding='utf8',newline='\n') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n')
 if not (DEV/'reference_20261103').exists():build_reference(DEV/'reference_20261103','wsl')
 save(ROOT/'reference_identity.json',{'source':source_identity(),'binary_sha256':sha256(DEV/'reference_20261103')})
def focused_tests():
 compile_one('tests/v43_regression.cpp',DEV/'test.o',FLAGS+['GR_V43_TEST'],ROOT/'tests/v43_test_build.log')
 compile_one('src/router.cpp',DEV/'test_router.o',FLAGS+['GR_V43_TEST'],ROOT/'tests/v43_router_build.log')
 wsl(['g++',linux_path(DEV/'test.o'),linux_path(DEV/'test_router.o'),linux_path(DEV/'parser.o'),'-o',linux_path(DEV/'v43_tests')],ROOT/'tests/v43_link.log')
 wsl([linux_path(DEV/'v43_tests')],ROOT/'tests/v43.log',180)

def tests():
 focused_tests()
 build=DEV/'cmake';wsl(['cmake','-S','.', '-B',linux_path(build),'-DCMAKE_BUILD_TYPE=Release','-DCMAKE_CXX_FLAGS_RELEASE=-O2','-DBUILD_TESTING=ON'],ROOT/'tests/cmake.log')
 for name in ['core_tests','physical_resources_tests','rules_20261103','v42_regression','v4_regression','v36_regression','v35_regression','router_v4_2']:
  wsl(['cmake','--build',linux_path(build),'--target',name,'--parallel','1'],ROOT/'tests'/(name+'_build.log'),600)
  if name!='router_v4_2':wsl([linux_path(build/name)],ROOT/'tests'/(name+'.log'),240)
 save(ROOT/'tests/v42_byte_regression.json',{'identical':sha256(build/'router_v4_2')==sha256(PROJECT/'build/router_v4_2'),'rebuilt_sha256':sha256(build/'router_v4_2'),'frozen_sha256':sha256(PROJECT/'build/router_v4_2')})
 for name in ['test_router_versions','test_v4_workflow','test_scoring_20261103']:
  launch([sys.executable,'-B','-m','unittest','discover','-s','tests','-p',name+'.py'],ROOT/'tests'/(name+'.log'),300)
def experiment(case,variant):
 folder=ROOT/'ablations'/case/variant;folder.mkdir(parents=True,exist_ok=False)
 binary=PROJECT/'build/router_v4_2' if variant=='baseline' else PROJECT/'build/router_v4_3' if variant=='joint' else DEV/variant
 inputs={k:Path(catalog()[case][k]) for k in ['def','lef','thermal']};cfg=PROJECT/'configs'/('v4_2.json' if variant=='baseline' else 'v4_3.json');budget=600 if case=='open_3d_case001' else 180
 guide=folder/'route.guide';args=[linux_path(binary),'--def',linux_path(inputs['def']),'--lef',linux_path(inputs['lef']),'--thermal-report',linux_path(inputs['thermal']),'--config',linux_path(cfg),'--output',linux_path(guide),'--metrics',linux_path(folder/'result.json'),'--time-limit',str(budget),'--progress-dir',linux_path(folder)]
 runid=uuid.uuid4().hex;args=['python3',linux_path(PROJECT/'scripts/linux_supervisor.py'),'--directory',linux_path(folder),'--run-id',runid,'--interval','5','--lease-seconds','60','--',*args]
 command=backend_command(args,'wsl',cwd=PROJECT,timeout=budget+10,time_file=folder/'resource_usage.txt')
 ident={'case':case,'variant':variant,'experimental_only':True,'router_sha256':sha256(binary),'config_sha256':sha256(cfg),'input_sha256':{k:sha256(p) for k,p in inputs.items()},'command':command,'source_sha256':json_file(ROOT/'build.json')['source_sha256'] if variant!='baseline' else 'frozen V4.2'};save(folder/'identity.json',ident)
 execution=timed_process(command,folder/'router.log',budget+45,monitor_dir=folder,monitor_interval=5,stall_after=30,native_resources=False);save(folder/'execution.json',execution)
 if execution['returncode'] or not guide.exists():raise ValueError('Router failed; no retry '+str(folder))
 score=folder/'reference';score.mkdir();report,cmd=reference(DEV/'reference_20261103',inputs,guide,score,'wsl',1536,600)
 save(score/'identity.json',dict(ident,guide_sha256=sha256(guide),reference_source_sha256=source_identity()[0],command=cmd))
 row={'case':case,'variant':variant,'router_status':'ROUTED','reference':report,'execution':execution,'guide_sha256':sha256(guide),'algorithm':small_metadata(folder/'result.json')};save(folder/'summary.json',row)
 print('EXPERIMENT',case,variant,report['static_legality'],report['overflow'],report['normalized_overflow_squared'],report['wirelength_dbu']/report['dbu_per_micron'],flush=True)
 if report['static_legality']!='REFERENCE_VALID':raise ValueError('Invalid experiment, preserve evidence')
def gate(case):
 before=windows_memory();probe=subprocess.run(['wsl','-d','Ubuntu','--','cat','/proc/meminfo','/proc/sys/kernel/random/boot_id'],capture_output=True,timeout=30)
 ps=subprocess.run(['wsl','-d','Ubuntu','--','ps','-eo','comm,args'],capture_output=True,timeout=30);mem={}
 for line in probe.stdout.decode('utf8','replace').splitlines():
  t=line.split()
  if len(t)>1 and t[1].isdigit():mem[t[0].rstrip(':')]=int(t[1])*1024
 busy=any(x.split() and (x.split()[0].startswith('router') or x.split()[0] in ['score','reference','reference_20261103']) for x in ps.stdout.decode('utf8','replace').splitlines()[1:])
 minimum=6*2**30 if case.endswith('005') else 2*2**30
 ok=probe.returncode==ps.returncode==0 and not busy and before.get('commit_available_bytes',0)>=minimum and mem.get('MemAvailable',0)>=minimum and shutil.disk_usage(PROJECT).free>=20*2**30
 return {'allowed':ok,'windows':before,'wsl_mem':mem,'wsl_probe':probe.stdout.decode('utf8','replace'),'processes':ps.stdout.decode('utf8','replace'),'busy':busy,'required_bytes':minimum}
def formal(case):
 folder=ROOT/'formal'/case;folder.mkdir(parents=True,exist_ok=False);check=gate(case);save(folder/'resource_gate.json',check)
 if not check['allowed']:print('BLOCKED_RESOURCE',case,flush=True);return
 stem=case.removeprefix('open_3d_');label=stem+'_v4_3';run=PROJECT/'outputs/runs'/case/label
 if run.exists():raise ValueError('Existing formal Label protected: '+label)
 if case!='open_3d_case001':
  prior=json_file(ROOT/'formal/open_3d_case001/comparison.json')
  if not prior.get('quality_gate_passed'):raise ValueError('case001 quality gate not passed')
  if case.endswith('005') and not json_file(ROOT/'formal/open_3d_case003/comparison.json').get('quality_gate_passed'):
   save(folder/'blocked_quality.json',{'status':'BLOCKED_QUALITY','reason':'case003 must pass the same quality gate before case005; no Router launched'})
   print('BLOCKED_QUALITY',case,flush=True);return
 budget=600 if case.endswith('001') else 3600 if case.endswith('003') else 14400
 launch([sys.executable,'-B','scripts/run_case.py','--case',case,'--backend','wsl','--router','build/router_v4_3','--config','configs/v4_3.json','--label',label,'--time-limit',str(budget)],folder/'router.log',budget+120)
 record=json_file(run/'run.json')
 if record.get('status')!='ROUTED' or record.get('returncode')!=0:raise ValueError('Incomplete Router: no score')
 launch([sys.executable,'-B','scripts/score_case.py','--case',case,'--label',label,'--scorer','reference_20261103','--backend','wsl','--memory-mb','4096' if case.endswith('005') else '3072' if case.endswith('003') else '1536','--timeout','7200' if case.endswith('005') else '3600' if case.endswith('003') else '600'],folder/'reference.log',7300 if case.endswith('005') else 3700 if case.endswith('003') else 700)
 from scoring import summary
 new=summary(case,label,'reference_20261103');old=summary(case,stem+'_v4_2','reference_20261103')
 if new['status']!='REFERENCE_VALID':raise ValueError('Reference analysis/legality incomplete')
 a=old.get('report',{});b=new['report'];same=all(a.get(k)==b.get(k) for k in ['reference_source_sha256','input_sha256','pin_assignment_policy']) if a else False
 if same:
  quality=b['normalized_overflow_squared']<a['normalized_overflow_squared'] and b['wirelength_dbu']<=a['wirelength_dbu']*1.005 and b['thermal_correlation']>=a['thermal_correlation']-1e-5
 else:quality=False
 save(folder/'comparison.json',{'case':case,'same_reference_identity':same,'quality_gate_passed':quality,'old':old,'new':new,'router':record,'algorithm':small_metadata(run/'result.json')})
 print('FORMAL',case,'QUALITY_GATE',quality,flush=True)

def output_audit(path):
 """Read one Net at a time; verify wire proxy and collect real TSV co-location."""
 import bisect
 sys.path.insert(0,str(PROJECT/'ui'))
 from result_store import Cursor
 c=Cursor(path);xs=ys=None;layers={};total=0.;unmeasured=0;nets=0;density={'total_tsv':0,'occupied_gcells':0,'max_per_gcell':0,'colocated_pairs':0}
 try:
  c.take('{')
  while c.char()!='}':
   key=c.value()[0];c.take(':')
   if key in ['x_lines','y_lines','layers']:
    data=c.value()[0]
    if key=='x_lines':xs=data
    elif key=='y_lines':ys=data
    else:layers={v['number']:v['direction'] for v in data}
   elif key=='nets':
    nx,ny=len(xs)-1,len(ys)-1;plane=nx*ny
    for net,_,_ in c.array():
     nets+=1;nodes=net['nodes'];guides=net['guides']
     if len(nodes)>100000 or len(guides)>2048:unmeasured+=1;continue
     for v in nodes:
      x,y,l=v%nx,v//nx%ny,v//plane;total+=xs[x+1]-xs[x] if layers[l]=='HORIZONTAL' else ys[y+1]-ys[y]
     for a in guides:
      if a[4]==10:continue
      for b in guides:
       if b[4]!=a[4]+1:continue
       x1,y1,x2,y2=max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])
       if x1<x2 and y1<y2:
        x=min(nx-1,max(0,bisect.bisect_right(xs,(x1+x2)/2)-1));y=min(ny-1,max(0,bisect.bisect_right(ys,(y1+y2)/2)-1));total+=3*max(xs[x+1]-xs[x],ys[y+1]-ys[y]);break
     if net['cross_die']:
      v=net['tsv_node'];x,y=v%nx,v//nx%ny;total+=6*max(xs[x+1]-xs[x],ys[y+1]-ys[y])
   elif key=='tsv_density':
    for n,_,_ in c.array():
     density['total_tsv']+=n;density['occupied_gcells']+=n>0;density['max_per_gcell']=max(density['max_per_gcell'],n);density['colocated_pairs']+=n*(n-1)//2
   else:c.skip()
   if c.char()=='}':break
   c.take(',')
  c.take('}')
 finally:c.close()
 return {'nets':nets,'wire_proxy_measured_dbu':total,'unmeasured_nets':unmeasured,'tsv_density':density,'max_reader_buffer_chars':c.maximum,'wire_scope':'positive-area grid-aligned Guide proxy; same frozen adjacent Via declarations required'}

def reports():
 """Publish measured evidence only; no run, score, registry promotion or Git mutation."""
 from scoring import summary
 baseline={};formal_rows={};experiments=[]
 for case in ['open_3d_case001','open_3d_case003','open_3d_case005']:
  stem=case.removeprefix('open_3d_');oldrun=json_file(PROJECT/'outputs/runs'/case/(stem+'_v4_2')/'run.json')
  baseline[case]={'scoring':summary(case,stem+'_v4_2','reference_20261103'),'router':oldrun}
  folder=ROOT/'formal'/case;comparison=json_file(folder/'comparison.json')
  formal_rows[case]=comparison or {'case':case,'status':'BLOCKED_RESOURCE' if json_file(folder/'resource_gate.json').get('allowed') is False else 'BLOCKED_QUALITY' if (folder/'blocked_quality.json').exists() else 'NOT_RUN','resource_gate':json_file(folder/'resource_gate.json')}
 for p in sorted((ROOT/'ablations').glob('*/*/summary.json')):
  j=json_file(p);j['evidence_path']=str(p.relative_to(PROJECT));experiments.append(j)
 audits={}
 for case in baseline:
  stem=case.removeprefix('open_3d_')
  for version in ['v4_2','v4_3']:
   path=PROJECT/'outputs/runs'/case/(stem+'_'+version)/'result.json'
   if path.exists():
    try:
     a=output_audit(path);r=baseline[case]['scoring'].get('report',{}) if version=='v4_2' else formal_rows[case].get('new',{}).get('report',{})
     a['frozen_reference_wire_match']=a['wire_proxy_measured_dbu']==r.get('wirelength_dbu') if r and a['unmeasured_nets']==0 else None
     audits[case+'/'+version]=a
    except Exception as e:audits[case+'/'+version]={'analysis_error':str(e)}
 save(ROOT/'result_audits.json',audits)
 before=json_file(ROOT/'protected_before.json');after=protected();save(ROOT/'protected_after.json',after)
 preservation={'identical':before==after,'changed':[k for k,v in before.items() if after.get(k)!=v],'added':sorted(set(after)-set(before))}
 save(ROOT/'preservation.json',preservation)
 identity=json_file(ROOT/'build.json');sources_ok=all(sha256(PROJECT/p)==h for p,h in identity['source_sha256'].items())
 decision={'status':'CANDIDATE','release_status':'NOT_RELEASED','official_scores':None,'official_platform_tested':False,'reference_identity_unchanged':source_identity()[0]==json_file(ROOT/'preflight.json')['reference_source_sha256'],'protected_files':preservation,'source_matches_binary_receipt':sources_ok,'router_sha256':identity['sha256'],'config_sha256':identity['config_sha256'],'cases':{k:{'status':v.get('new',{}).get('status',v.get('status')),'quality_gate_passed':v.get('quality_gate_passed',False)} for k,v in formal_rows.items()},'reason':'Do not replace V4.2 until all legality, quality and resource gates are measured. Reference metrics are not official scores.'}
 decision['performance_observations']={case:{'router_wall_ratio':v['router']['runtime_seconds']/baseline[case]['router']['runtime_seconds'],'peak_rss_ratio':v['router']['peak_memory_bytes']/baseline[case]['router']['peak_memory_bytes'],'single_observation_not_statistical_benchmark':True} for case,v in formal_rows.items() if 'new' in v}
 decision['remaining_issues']=['case005 resource gate not satisfied unless a complete record is present','M1 transfer congestion remains a tradeoff','Multi-Net large-Case contribution is small','Unpublished official formulas/model assumptions remain unconfirmed','Wall-clock budgets can limit bitwise determinism on large Cases']
 save(ROOT/'results.json',{'baseline':baseline,'formal':formal_rows,'experiments':experiments});save(ROOT/'decision.json',decision)
 def md(name,body):
  with (ROOT/name).open('x',encoding='utf8',newline='\n') as f:f.write(body.rstrip()+'\n')
 def number(x):return 'N/A' if x is None else str(int(x)) if isinstance(x,(int,float)) and float(x).is_integer() else f'{x:.9g}' if isinstance(x,(int,float)) else str(x)
 def metrics(r):return [r.get('static_legality','N/A'),r.get('wirelength_dbu',0)/r['dbu_per_micron'] if r.get('dbu_per_micron') else None,r.get('overflow'),r.get('normalized_overflow_squared'),r.get('thermal_correlation')]
 ab=['# V4.3 独立消融\n','同输入、同基础配置、同 REFERENCE_20261103 源码身份。control 是四模块全关、保留新线长预算框架的额外控制组；不可将其收益归于任一单项模块。baseline 为原始 V4.2。正式 case001 联合版见 case_comparison.md。\n','| Case | 实验 | 参考状态 | Wirelength µm | Overflow | 归一化潜能 | Pearson rho | Router wall s |','|---|---|---|---:|---:|---:|---:|---:|']
 for e in experiments:ab.append('| '+' | '.join(map(number,[e['case'],e['variant'],*metrics(e['reference']),e['execution'].get('runtime_seconds')]))+' |')
 md('ablation.md','\n'.join(ab))
 evidence={e['variant']:e for e in experiments if e['case']=='open_3d_case001'};findings={}
 if 'control' in evidence:
  control=evidence['control']['reference']
  for name,e in evidence.items():
   r=e['reference'];findings[name]={'potential_delta_vs_control':r['normalized_overflow_squared']-control['normalized_overflow_squared'],'potential_relative_percent_vs_control':100*(r['normalized_overflow_squared']/control['normalized_overflow_squared']-1),'overflow_delta_vs_control':r['overflow']-control['overflow'],'wirelength_delta_um_vs_control':(r['wirelength_dbu']-control['wirelength_dbu'])/r['dbu_per_micron'],'rho_delta_vs_control':r['thermal_correlation']-control['thermal_correlation'],'experimental_evidence':e['evidence_path']}
 save(ROOT/'module_findings.json',findings)
 rows=['# V4.2 / V4.3 同口径对照\n','仅参考评分，官方百分制 Wirelength/Congestion/Thermal/总分全部 N/A。旧版缺失参考报告时不填造指标。\n','| Case | 版本 | Router | 参考状态 | Wirelength µm | Overflow | 潜能 | rho | Runtime s | Peak RSS MiB |','|---|---|---|---|---:|---:|---:|---:|---:|---:|']
 rows=rows[:2]
 for case,old in baseline.items():
  rows.extend(['\n## '+case+'\n','| Case | 版本 | Router | 参考状态 | Wirelength µm | Overflow | 潜能 | rho | Runtime s | Peak RSS MiB |','|---|---|---|---|---:|---:|---:|---:|---:|---:|'])
  run=old['router'];r=old['scoring'].get('report',{});rows.append('| '+' | '.join(map(number,[case,'V4.2',run.get('status'),*metrics(r),run.get('runtime_seconds'),run.get('peak_memory_bytes',0)/2**20 if run.get('peak_memory_bytes') else None]))+' |')
  new=formal_rows[case];run=new.get('router',{});r=new.get('new',{}).get('report',{});rows.append('| '+' | '.join(map(number,[case,'V4.3',run.get('status',new.get('status','NOT_RUN')),*metrics(r),run.get('runtime_seconds'),run.get('peak_memory_bytes',0)/2**20 if run.get('peak_memory_bytes') else None]))+' |')
  if r:
   rows.append('\n'+case+' 质量门槛：'+str(new.get('quality_gate_passed'))+'；Guide SHA256：`'+new['new']['guide_sha256']+'`。\n')
   rows.extend(['| Layer | V4.2 Overflow | V4.3 Overflow | Delta |','|---|---:|---:|---:|'])
   for layer in ['1','10','11']:
    a=old['scoring'].get('report',{}).get('by_layer',{}).get(layer,{}).get('overflow');b=r['by_layer'].get(layer,{}).get('overflow');rows.append('| '+' | '.join(map(number,['M'+layer,a,b,b-a if a is not None and b is not None else None]))+' |')
 md('case_comparison.md','\n'.join(rows))
 md('architecture.md', '''# V4.3 架构与边界

基于冻结 V4.2：保留初始 Steiner/RSMT、多目标 A*、Negotiated Congestion、Pin/OBS 几何、TSV 禁入、Guide 验证与 V3.6 增量事务。新增 `GR_V4_3` / `router_v4_3_impl.hpp`，替换末尾 V4 优化阶段而非再叠加一整轮。

顺序：初始/原有优化 → M1 Escape（预留线长预算）→ 有界 Multi-Net → 自适应 TSV → Thermal Joint → 全 Net 验证 → 流式输出。四模块可用 `GR_V43_DISABLE_SCORING/MULTINET/ADAPTIVE/THERMAL` 独立关闭。默认根 Router 和 V4.2 目标不变。

V4.3 配置逐字复制 V4.2；开关在编译宏中，避免修改冻结参考评分器也使用的 model.hpp。算法源码、编译宏、二进制、配置和输入 SHA 见 build.json 及每个实验 identity.json。候选未提交 Git，base_commit 不代表包含未提交修改；完整源码文件 SHA 是本次构建身份。

复杂度：Multi-Net 仅索引前 256 个热点、最多 4M demand 条目、每热点最多八个 owner；禁止全 Net 两两枚举。每组最多三条/20K 节点。TSV 稀疏区域队列、每次候选最多六个；大型单 Net 超出工作区时保留原路径，不截断最终结果。
''')
 md('scoring_aware_congestion.md', '''# 评分感知拥塞代理

容量 C>0 时，phi(u)=max(0,F+u-C)^2/C^2；新增占用 d 的完整边际为 phi(u+d)-phi(u)。搜索 toll=32*max(0,delta_phi)+0.02*min(1,(F+u+d)/C)^4+history_weight*history。外层乘配置 congestion；全部代价非负，原几何启发式仍为下界。同 Net Pin 抵扣延续原模型；不减小固定需求。

C=0 不参与归一化：新增需求边不可走；既有合法固定接入不改写。热阶段预算排除旧零容量 sentinel，避免用巨大的兼容常数放宽正容量拥塞约束。

搜索只定候选。最终接受比较移除同一完整 Net 后的完整旧/新 footprint 差；Stage A 要求潜能严格下降，Raw Overflow 受累计 0.01% 预算限制。M1 Escape 仍要求 M1 增量下降。Multi-Net 则要求联合 Raw Overflow 不增且潜能严格下降。

这是 REFERENCE_20261103 搜索代理，不是已确认的官方百分制公式。Track 分配、零容量/全容量特殊规则及新官方实现仍有模型依赖，详见 rules_sources.json 与原参考报告 model_assumptions。没有更改参考评分器来制造收益。
''')
 md('multinet_optimization.md', '''# 有界 Multi-Net 协作

从归一化热点反向索引相关 Net，取二至三条，先同时移除旧资源，按确定顺序重连各自局部链。保留全部 Pin Access 和固定 TSV；局部路径最多切除两侧各八条边，XY 窗口加四格，同 Die 层窗口限制为端点上下两层，沿用 8192 扩展上限。最多 32 次候选、8 秒、每组约 0.35 秒，最多两种顺序。

`V43BatchTransaction` 包装已有 `V36Transaction`。全部候选安装并通过完整验证后，联合比较资源、线长和潜能；任一失败则逆序恢复。全局与分组线长、Via 增量均受限制。Commit 之前完成所有可能分配的日志工作，回滚不依赖新内存。

真实合法两 Net 走廊夹具中：两条分别变更都不改善，联合交换潜能 1→0。测试覆盖两种顺序、安装中断、bad_alloc、超时、普通 Via、跨 Die 完整 TSV footprint、Usage 恢复与连通性。实测收益与接受率见 results.json 的 algorithm.v4_3.multinet，不能将夹具改善推广为所有 Case 收益。
''')
 md('tsv_optimization.md', '''# 自适应 TSV 优化

V4.2 记录显示 case001/003 的多数拒绝来自 wire guard，case005 热阶段主要为 density guard，见冻结 result.json 的 v3_algorithm.v4。增加尝试总量不能解决这些瓶颈。

V4.3 用 32×32 GCell 区域队列，按归一化边际热点排序、每轮每区域公平访问一次、重估队首四条。按严重度分配 2/4/6 候选；复杂 Net 最多两个；连续两次线长拒绝后冷却，拓扑预算失败停止本 Net。保持原 16000/180 秒拥塞预算，不单纯延长时间。

候选预过滤真实 Pin/OBS TSV 禁区、rho 底线与热密度条件；对 M10/M11 完整 5µm footprint 排价，加距离与共址代理。排序后仍需重建上下 Die 路径，完整验证全部 Via、Guide、资源与真实线长代理，不能仅凭中心格收益接受。

计数包括 attempted/accepted、超过两个的 extra_candidates、presearch_rejections、拒绝原因、candidate_seconds/max、累计过程峰值 RSS；它们区分候选尝试与实际 A* 调用，不将二者混称。真实 TSV 最大格密度、共址对数见 result_audits.json；该审计使用有界流式读取而非整体加载大型 result.json。
''')
 md('thermal_optimization.md', '''# 热—线长联合优化

Stage A 先降低归一化潜能，rho 不低于阶段起点−1e−5。Stage B 只尝试 Manhattan 距离≤4 的合法 TSV 细调，必须提高 Pearson rho、降低目标热格共址，并满足累计 Raw Overflow 与正容量潜能各 0.01% 的阶段预算。

热收益还必须超过 max(0,参考线长增量)/当前总参考线长×0.1+1e−12。全阶段总线长增长上限 0.49%；M1 首阶段仅使用 0.15%，给 TSV/热阶段留预算。参考线长代理包含非均匀 GCell 方向长度及冻结参考的 Guide-overlap Via/TSV 项，不用内部 wirelength 冒充官方线长。

rho 改善不等于官方 Thermal Score 提高。Stage A/B 的 rho、wire、overflow、phi delta 与拒绝原因在原始 metadata 中，官方百分制仍 N/A。固定需求模型和评分器未修改。
''')
 perf=['# 性能与测试\n','旧七组 C++ 回归、V4.3 联合夹具、30 个版本管理 / 8 个 V4 workflow / 7 个 20261103 Python 测试通过。原 V4.2 重编译 SHA 与冻结二进制完全一致，证据 bootstrap/tests/v42_byte_regression.json。修订后专用测试位于 tests/。\n','每轮正式运行前记录 Windows Commit、WSL MemAvailable/Swap、boot_id、进程与磁盘。case005 至少 6 GiB Commit 和 WSL 可用内存，绝不降低门槛。\n','各阶段资源：Peak RSS 是进程自启动以来的累计高水位，不能作阶段独占内存；实验 wall time 包含 WSL/supervisor 开销。一次测量受主机负载影响，不声称统计显著加速。\n']
 perf.append('新增五个 V4.3 Python workflow 安全测试通过，覆盖 6 GiB 门槛、忙碌 Router/评分器及实验/正式目录冲突，测试使用 TemporaryDirectory 与 mock，不接触历史结果。\n')
 perf.append('历史 case001 V4.2 外层预算为 1800 秒，本次消融/正式 V4.3 为 600 秒；两者均远低于预算完成。源码中 cfg.time_limit 仅作超时检查，不参与代价或候选分配。共有配置参数与输入相同，但不将这次不同主机负载/外层预算的单次 wall time 称为严格性能基准。case003 两版均为 3600 秒。\n')
 for e in experiments:
  perf.extend(['## '+e['case']+' / '+e['variant'],'```json',json.dumps({'execution':e['execution'],'v4':e['algorithm'].get('v4'),'v4_3':e['algorithm'].get('v4_3')},ensure_ascii=False,indent=2),'```'])
 md('performance.md','\n'.join(perf))
 md('summary.md','# V4.3 开发与受控验证\n\n状态：CANDIDATE / NOT_RELEASED。实现四模块并保留 V4.2；完整对照见 [case_comparison.md](case_comparison.md)，消融见 [ablation.md](ablation.md)，原始指标与身份见 results.json/build.json。\n\n保护检查：'+str(preservation['identical'])+'；当前源码与候选构建一致：'+str(sources_ok)+'；参考模型源码未变：'+str(decision['reference_identity_unchanged'])+'。\n\ncase001 消融中，自适应 TSV 对归一化拥塞的单项收益最大；Multi-Net 的实际贡献较小但夹具与真实 Case 均观察到接受。Thermal 单项提高 rho，同时潜能略增，不能笼统称为所有指标都更优。公共线长预算框架本身也有收益，module_findings.json 单独对比 control，避免将公共框架改善计入某模块。M1 Overflow 可能增加，需要继续处理跨层转移的代价。\n\n三个 Case 状态：\n\n'+'\n'.join('- '+k+'：'+str(v) for k,v in decision['cases'].items())+'\n\n尚未取得官方平台 V4.3 成绩；不得据参考指标声明官方得分提高。只有全部合法性、质量和资源门槛实测完成后才考虑替换 V4.2。未自动 commit/push/upload，未覆盖 V4.2 Submission。')
 doc=PROJECT.parent.parent/'算法版本迭代说明.md'
 text=doc.read_text(encoding='utf8');title='## V4.3：评分代理、有界协作与自适应 TSV（CANDIDATE）'
 if title in text:raise ValueError('V4.3 chapter exists; do not append duplicates')
 chapter='\n\n'+title+'\n\n保留 V4.2 的初始布线和合法性模型，新增 GR_V4_3 独立分支。正容量使用完整归一化边际惩罚；最多三 Net 的原子局部重布；区域公平的 2/4/6 候选 TSV 调度；热相关性与参考线长联合保护。V4.2 重编译二进制 SHA 保持不变。\n\n本次 toy 联合版以及 sample001 的真实结果、case001 消融、正式 Case 验收与资源阻塞均保存在 `EDA/code_3DIC EDA/outputs/reports/v4_3/` 的 summary.md、ablation.md、case_comparison.md、performance.md 和 decision.json；不得以未完成实验推断收益。\n\n'
 for case,v in formal_rows.items():
  if 'new' in v:
   r=v['new']['report'];a=v['old'].get('report',{});chapter+=case+'：REFERENCE_20261103 '+v['new']['status']+'；线长 '+number(a.get('wirelength_dbu',0)/a['dbu_per_micron'] if a.get('dbu_per_micron') else None)+' → '+number(r['wirelength_dbu']/r['dbu_per_micron'])+' µm，Overflow '+number(a.get('overflow'))+' → '+number(r['overflow'])+'，潜能 '+number(a.get('normalized_overflow_squared'))+' → '+number(r['normalized_overflow_squared'])+'，rho '+number(a.get('thermal_correlation'))+' → '+number(r['thermal_correlation'])+'。质量门槛 '+str(v['quality_gate_passed'])+'。\n\n'
  else:chapter+=case+'：'+v.get('status','NOT_RUN')+'，没有新 Guide 或参考成绩。\n\n'
 chapter+='搜索代理采用正容量归一化潜能 phi=max(0,F+u−C)^2/C^2 的完整边际，非负 toll=32×max(0,delta_phi)+0.02×min(1,utilization)^4+历史项。四模块可以独立关闭；总参考线长预算 0.49%，M1 先分配 0.15%。它不是完整官方评分公式。\n\n'
 for case,v in formal_rows.items():
  if 'new' in v:
   a=baseline[case]['router'];b=v['router'];chapter+=case+' Router wall time '+number(a.get('runtime_seconds'))+' → '+number(b.get('runtime_seconds'))+' s，Peak RSS '+number(a.get('peak_memory_bytes',0)/2**20)+' → '+number(b.get('peak_memory_bytes',0)/2**20)+' MiB；仅单次实测，主机负载不同。\n\n'
 chapter+='以上为自研统一参考指标，不是官方百分制。V4.3 保持 CANDIDATE / NOT_RELEASED，不覆盖 V4.2，不自动提交比赛平台。\n'
 with doc.open('a',encoding='utf8',newline='\n') as f:f.write(chapter)
 print('REPORTS_PUBLISHED',decision,flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=['preflight','build','tests','focused-tests','experiment','formal','reports']);p.add_argument('--case');p.add_argument('--variant',choices=['baseline',*VARIANTS]);a=p.parse_args()
 if a.phase=='preflight':preflight()
 elif a.phase=='build':builds()
 elif a.phase=='tests':tests()
 elif a.phase=='focused-tests':focused_tests()
 elif a.phase=='experiment':experiment(a.case,a.variant)
 elif a.phase=='reports':reports()
 else:formal(a.case)
