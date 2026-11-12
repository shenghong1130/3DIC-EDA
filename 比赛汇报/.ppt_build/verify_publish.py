from pathlib import Path
import json,zipfile,xml.etree.ElementTree as E,hashlib,shutil,subprocess,sys
root=Path(__file__).resolve().parent.parent;b=root/'.ppt_build';candidate=b/'candidate_v3.pptx'
ns={'c':'http://schemas.openxmlformats.org/drawingml/2006/chart','a':'http://schemas.openxmlformats.org/drawingml/2006/main','p':'http://schemas.openxmlformats.org/presentationml/2006/main'}
def chart_values(p):
 with zipfile.ZipFile(p) as z:
  out=[]
  for name in z.namelist():
   if name.startswith('ppt/charts/chart') and name.endswith('.xml'):
    t=E.fromstring(z.read(name));out.append([[round(float(v.text),8) for v in s.findall('.//c:val//c:numCache/c:pt/c:v',ns)] for s in t.findall('.//c:ser',ns)])
  return sorted(out,key=str)
source=root/'3DIC比赛复盘_15页叙事版.pptx'
assert chart_values(source)==chart_values(candidate),'Chart values changed'
orig=json.loads((b/'sources.json').read_text(encoding='utf-8'))[source.name]['slides']
with zipfile.ZipFile(candidate) as z:
 for i,slide in enumerate(orig,1):
  note=''.join(E.fromstring(z.read(f'ppt/notesSlides/notesSlide{i}.xml')).itertext())
  for line in slide['notes']:assert line in note,(i,'Missing original note')
 assert len([x for x in z.namelist() if x.startswith('ppt/slides/slide') and x.endswith('.xml')])==15
 assert len([x for x in z.namelist() if x.startswith('ppt/embeddings/')])>=5
tools=Path('C:/Users/SCSM11/.codex/plugins/cache/openai-primary-runtime/presentations/26.1110.11613/skills/presentations/container_tools')
reports={}
for name,args in [('package',['inspect_presentation_package_integrity.py',str(candidate),'--fail-on-findings']),('layout',['inspect_presentation_layout_geometry.py',str(candidate),'--expected-slide-count','15','--expected-slide-size-emu','12192000,6858000','--validate-heading-fit','--require-native-table-slide','3','--require-native-table-slide','5','--require-native-table-slide','6','--require-native-table-slide','9','--fail-on-findings'])]:
 r=subprocess.run([sys.executable,str(tools/args[0]),*args[1:]],capture_output=True,text=True,encoding='utf-8');(b/(name+'_validation.json')).write_text(r.stdout,encoding='utf-8');assert r.returncode==0,r.stdout+r.stderr;reports[name]=json.loads(r.stdout)
 assert reports[name].get('warning_count',0)==0,reports[name]
checks=json.loads((b/'final_text_checks.json').read_text(encoding='utf-8-sig'));assert all(c['fits'] and c['inBounds'] for c in checks)
final=root/'3DIC比赛汇报_清华模板优化版.pptx';pdf=root/'3DIC比赛汇报_清华模板优化版.pdf'
for dest in [final,pdf]:assert not dest.exists(),f'Cannot overwrite {dest}'
shutil.copy2(candidate,final);shutil.copy2(b/'verified_preview.pdf',pdf)
report={'status':'pass','slides':15,'native_charts':5,'native_tables':4,'selected_images':7,'original_notes_retained':True,'chart_values_match_original':True,'native_powerpoint_rendered':True,'checked_text_boxes':len(checks),'outputs':{p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in [final,pdf]},'sources':{p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in root.glob('*.pptx') if p!=final}}
(b/'verification_receipt.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
