import json, pathlib, hashlib, zipfile, urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[2]
CORE=ROOT/'EDA'/'code_3DIC EDA'
OUT=ROOT/'汇报PPT'
def load(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()
data={'platform':{},'reference':{},'runs':{},'sources':{}}
for version,folder in [('v3_5','v3_5_24699'),('v3_6','v3_6_26152')]:
    base=CORE/'outputs/reports/official_platform'/folder
    data['platform'][version]={'scores':load(base/'scores.json')}
    for p in base.glob('*.json'):
        if p.name!='scores.json':
            data['platform'][version][p.stem]=load(p)
    zips=[p for p in base.glob('*prediction*.zip')]
    for p in zips:
        with zipfile.ZipFile(p) as z:
            data['platform'][version]['zip_members']={i.filename:hashlib.sha256(z.read(i)).hexdigest() for i in z.infolist() if not i.is_dir() and 'guide' in i.filename}
for model in ['reference_20261029','reference_20261103']:
    for case in ['open_3d_case001','open_3d_case003','open_3d_case005']:
        for v in ['v3_1','v3_2','v3_3','v3_4','v3_5','v3_6','v4','v4_2']:
            label=case.replace('open_3d_','')+'_'+v
            candidates=[CORE/'outputs/reports/scoring'/model/case/label, CORE/'outputs/reports'/model/case/label]
            for d in candidates:
                if d.exists():
                    records={str(p.relative_to(CORE)):load(p) for p in d.rglob('*.json') if p.stat().st_size<2500000}
                    data['reference'][model+'/'+case+'/'+label]=records
                    break
for case in ['open_3d_case001','open_3d_case003','open_3d_case005']:
    for v in ['v1','v2','v3_1','v3_2','v3_3','v3_4','v3_5','v3_6','v4','v4_2']:
        label=case.replace('open_3d_','')+'_'+v
        d=CORE/'outputs/runs'/case/label
        if (d/'run.json').exists():
            r=load(d/'run.json'); g=d/'route.guide'
            data['runs'][case+'/'+label]={'run':r,'guide_sha256_actual':sha(g) if g.exists() else None,'guide_bytes':g.stat().st_size if g.exists() else None}
for p in [CORE/'outputs/reports/v4/decision.json',CORE/'outputs/reports/v4_2/final_results.json',CORE/'outputs/reports/v4_2/decision.json',CORE/'docs/router_versions.json']:
    data['sources'][str(p.relative_to(ROOT))]=load(p)
try:
    with urllib.request.urlopen('http://127.0.0.1:8501/',timeout=5) as r: data['ui']={'status':r.status,'bytes':len(r.read())}
except Exception as e: data['ui']={'error':str(e)}
(OUT/'assets/data_verified.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
print('ui',data['ui'])
for v,d in data['platform'].items():
    print('PLATFORM',v,json.dumps(d['scores'],ensure_ascii=False)[:1200])
    for name,rep in d.items():
        if name.endswith('evaluation_report'): print(name,json.dumps(rep,ensure_ascii=False)[:1500])
for k,recs in data['reference'].items():
    print('REFERENCE',k,[(path,list(r)[:15]) for path,r in recs.items()])
for k,d in data['runs'].items():
    r=d['run']; print('RUN',k, {x:r.get(x) for x in ['status','returncode','runtime_seconds','elapsed_seconds','peak_memory_bytes','router_sha256','config_path']})
