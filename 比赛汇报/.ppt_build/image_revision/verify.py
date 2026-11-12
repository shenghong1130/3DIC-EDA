from pathlib import Path
import zipfile,xml.etree.ElementTree as E,hashlib,json,shutil
from PIL import Image,ImageChops
b=Path(__file__).resolve().parent;root=b.parent.parent
original=root/'3DIC比赛汇报_清华模板优化版.pptx';draft=b/'edited.pptx'
ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
def text(z,n):return '\n'.join(t.text or '' for t in E.fromstring(z.read(n)).findall('.//a:t',ns))
with zipfile.ZipFile(original) as a,zipfile.ZipFile(draft) as z:
 for i in range(1,16):
  n=f'ppt/notesSlides/notesSlide{i}.xml'
  oldnote=text(a,n);newnote=text(z,n)
  if oldnote.endswith('\n'+str(i)):oldnote=oldnote.rsplit('\n',1)[0]
  assert newnote.startswith(oldnote),f'Notes changed on {i}'
  if i not in (7,9,10):
   n=f'ppt/slides/slide{i}.xml';assert text(a,n)==text(z,n),f'Text changed on {i}'
 for n in a.namelist():
  if n.startswith('ppt/charts/') or n.startswith('ppt/embeddings/'):
   assert a.read(n)==z.read(n),f'Chart or workbook changed: {n}'
unchanged=[]
for i in range(1,16):
 if i in (7,9,10):continue
 name=f'slide-{i:02d}.png'
 old=root/'.ppt_build/verified_render'/name
 assert ImageChops.difference(Image.open(old).convert('RGB'),Image.open(b/'render'/name).convert('RGB')).getbbox() is None,f'Render changed on {i}'
 unchanged.append(i)
for suffix,src in [('pptx',draft),('pdf',b/'edited.pdf')]:
 dest=root/f'3DIC比赛汇报_清华模板优化版_v2.{suffix}'
 assert not dest.exists(),f'Output exists: {dest}'
 shutil.copy2(src,dest)
report={'status':'pass','modified_slides':[7,9,10],'unchanged_render_verified':unchanged,'original_notes_preserved':True,'all_chart_and_workbook_parts_byte_identical':True,'source_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),'slide_count':15}
(b/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))
