import json,pathlib,hashlib,zipfile,shutil
w=pathlib.Path(__file__).resolve().parents[1];p=w/'3DIC_EDA_算法迭代汇报.pptx';r=json.loads((w/'.build/validation.json').read_text(encoding='utf8'))
assert hashlib.sha256(p.read_bytes()).hexdigest()==r['finalSha256']
with zipfile.ZipFile(p) as z:
 assert z.testzip() is None
 slides=[n for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')];assert len(slides)==14
 charts=[n for n in z.namelist() if '/charts/chart' in n and n.endswith('.xml')];assert len(charts)==2
 texts=''.join(z.read(n).decode('utf8') for n in slides);assert '\ufffd' not in texts
 assert all(str(v) in z.read(charts[0]).decode('utf8') for v in [45.39,45.92])
 assert all(str(v) in z.read(charts[1]).decode('utf8') for v in [12096,11845,12097,10995,11619])
raw=w/'assets/data_verified.json'
if raw.exists():shutil.move(raw,w/'.build/data_verified_private.json')
pd=w/'assets/chart_data.json';data=json.loads(pd.read_text(encoding='utf8'));data.pop('runs',None)
for row in data['rows']:
 i=row['identity'];row['identity']={k:v for k,v in i.items() if k in ['case','label','ruleset','official','input_sha256','guide_sha256','router_sha256','router_version','config_sha256','reference_source_sha256','pin_assignment_policy','official_track_assignment_confirmed']}
pd.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
md=w/'数据来源与版本核对.md';s=md.read_text(encoding='utf8');s+='\n## 9. 制作与验证\n\n- 14 页、16:9，6 个原生表格、2 个原生柱状图，图表附可编辑数据工作簿。\n- 已检查 PPTX CRC、关系完整性、字体、文字与表格几何、图表数据、重新导入与14页预览。结构和布局检查均为 0 项错误、0 项警告。\n- 已逐页目视检查全部页面，最终 PNG 见 preview/。\n- 当前环境未在 Microsoft PowerPoint 原生应用内打开验证；可编辑性通过 OOXML 对象和工作簿检查，不代表所有 Office 实现均保证一致。\n- 全文约3838字，逐页安排850秒（约14.2分钟，含指图与停顿）。\n- 未运行任何 Router / 评分器，Git 既有文件没有改动。\n'
md.write_text(s,encoding='utf8')
qa={'slides':14,'native_tables':6,'native_charts':2,'pptx_sha256':r['finalSha256'],'bytes':r['byteCount'],'integrity':r['packageIntegrity']['status'],'layout_findings':r['presentationLayout']['finding_count'],'layout_warnings':r['presentationLayout']['warning_count'],'artifact_import':r['firstPartyImport']['passed'],'native_office_test':False}
(w/'assets/delivery_validation.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(qa,ensure_ascii=False))
