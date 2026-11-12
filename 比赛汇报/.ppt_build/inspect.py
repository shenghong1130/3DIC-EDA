from pathlib import Path
import zipfile,xml.etree.ElementTree as E,json
from PIL import Image,ImageOps,ImageDraw
root=Path(__file__).resolve().parent.parent
ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main','p':'http://schemas.openxmlformats.org/presentationml/2006/main'}
out={}
for f in root.glob('*.pptx'):
 with zipfile.ZipFile(f) as z:
  slides=[]
  names=sorted([n for n in z.namelist() if __import__('re').match(r'ppt/slides/slide\d+.xml$',n)],key=lambda n:int(__import__('re').search(r'slide(\d+)',n).group(1)))
  for n in names:
   t=E.fromstring(z.read(n)); idx=__import__('re').search(r'slide(\d+)',n).group(1)
   note=f'ppt/notesSlides/notesSlide{idx}.xml'
   slides.append({'slide':int(idx),'text':[''.join(p.itertext()) for p in t.findall('.//a:t',ns)],'notes':[''.join(p.itertext()) for p in E.fromstring(z.read(note)).findall('.//a:t',ns)] if note in z.namelist() else []})
  out[f.name]={'size':E.fromstring(z.read('ppt/presentation.xml')).find('p:sldSz',ns).attrib,'slides':slides}
(root/'.ppt_build'/'sources.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
ims=[]; meta=[]
for i,f in enumerate(sorted((root/'images').glob('*'))):
 im=Image.open(f).convert('RGB'); meta.append({'id':i+1,'file':f.name,'size':im.size})
 thumb=ImageOps.contain(im,(480,270)); tile=Image.new('RGB',(500,310),'white');tile.paste(thumb,((500-thumb.width)//2,25));ImageDraw.Draw(tile).text((10,290),f'{i+1:02d} {f.stem}',fill='black');ims.append(tile)
canvas=Image.new('RGB',(1500,310*((len(ims)+2)//3)), '#dddddd')
for i,im in enumerate(ims):canvas.paste(im,((i%3)*500,(i//3)*310))
canvas.save(root/'.ppt_build'/'images_contact.jpg')
(root/'.ppt_build'/'images_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
