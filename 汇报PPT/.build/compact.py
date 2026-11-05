exec(open(__file__.replace('compact.py','inspect_data.py'),encoding='utf8').read().split("data={'platform'")[0])
d=load(OUT/'assets/data_verified.json')
for v,z in d['platform'].items():
 print('PLAT',v)
 for c,r in z['scores']['cases'].items():
  a=r['raw_report'];print(c,a['wirelength']['total_um'],a['congestion']['total_overflow'],{k:r.get(k) for k in ['wirelength_score','congestion_score','thermal_score']})
 print('HASH verification',[(n,h,h==d['runs'].get(n.split('/')[-1].replace('.output.guide','')+'/'+n.split('/')[-1].replace('open_3d_','').replace('.output.guide','')+'_'+v,{}).get('guide_sha256_actual')) for n,h in z['zip_members'].items()])
for k,recs in d['reference'].items():
 r=next((v for p,v in recs.items() if p.endswith('reference_report.json')),{});i=next((v for p,v in recs.items() if p.endswith('invocation.json')),{});
 print('REF',k, {x:r.get(x) for x in ['status','wirelength_um','total_overflow','normalized_overflow_squared','thermal_correlation','error_counts']});
 if k.endswith('case001_v3_5'): print('REPORT',json.dumps(r,ensure_ascii=False)[:3000], '\nIDENTITY',json.dumps(i,ensure_ascii=False)[:4000])
for k,z in d['runs'].items():
 if k.endswith(('v3_5','v3_6','v4','v4_2')):print('RUN',k,{x:z['run'].get(x) for x in ['status','returncode','wall_seconds','runtime_seconds','peak_rss_bytes','peak_memory_bytes','resource_usage','router_sha256','config_sha256']})
