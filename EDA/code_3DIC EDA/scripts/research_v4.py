"""Read-only platform/geometry/reference attribution. Never launches a Router."""
import argparse, collections, csv, hashlib, io, json, math, shutil, sqlite3, sys, tempfile, zipfile
from pathlib import Path
from common import PROJECT, EDA, sha256
from scoring import reference_directory
from compare_v36 import small_metadata
sys.path.insert(0,str(PROJECT/'ui'))
from result_store import Cursor

REPORT=PROJECT/'outputs/reports/v4/research'

def save(path,data):
    with path.open('x',encoding='utf8') as f:json.dump(data,f,ensure_ascii=False,indent=2)

def members(path):
    c=Cursor(path)
    try:
        c.take('{')
        while c.char()!='}':
            key,_,_=c.value();c.take(':')
            if key=='nets':
                for item,_,_ in c.array():yield item
                return
            c.skip()
            if c.char()==',':c.take(',')
    finally:c.close()

def official():
    base=EDA/'\u8865\u5145\u8d44\u6599';archive=PROJECT/'outputs/reports/official_platform/v3_6_26152'
    sources=[base/'scoring_result_3_6.zip',base/'prediction_result_3_6.zip']
    if archive.exists():
        for path in sources:
            if sha256(path)!=sha256(archive/path.name):raise ValueError('Existing archive conflict')
        return archive
    for path in sources:
        with zipfile.ZipFile(path) as z:
            if z.testzip():raise ValueError('ZIP CRC error')
    archive.mkdir(parents=True,exist_ok=False);identity={}
    for path in sources:
        with path.open('rb') as src,(archive/path.name).open('xb') as dst:shutil.copyfileobj(src,dst)
        identity[path.name]=sha256(path)
    with zipfile.ZipFile(sources[0]) as scoring,zipfile.ZipFile(sources[1]) as prediction:
        scores=json.loads(scoring.read('scores.json'));assert scores['total_score']==45.92
        for case in sorted(scores['cases']):
            label=case.removeprefix('open_3d_')+'_v3_6';guide=PROJECT/'outputs/runs'/case/label/'route.guide'
            h=hashlib.sha256()
            with prediction.open(case+'.output.guide') as f:
                for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
            assert h.hexdigest()==sha256(guide),case+' platform Guide mismatch'
            row=json.loads(scoring.read('eval_temp/'+case+'/evaluation_report.json'))
            save(archive/(case+'_evaluation_report.json'),row);identity[case]={'guide_sha256':h.hexdigest(),'raw_report':row,'platform_scorer_sha256':None,'platform_input_sha256':None}
        save(archive/'scores.json',scores);save(archive/'identity.json',identity)
    return archive

def analyze():
    archive=official();gap={};coverage={};thermal={}
    with zipfile.ZipFile(archive/'scoring_result_3_6.zip') as z,zipfile.ZipFile(EDA/'\u8865\u5145\u8d44\u6599'/'scoring_result_3_5.zip') as old:
        for suffix in ['001','003','005']:
            case='open_3d_case'+suffix;label='case'+suffix+'_v3_6';refdir=reference_directory(case,label)
            ref=json.loads((refdir/'reference_report.json').read_text(encoding='utf8'));stats=small_metadata(PROJECT/'outputs/runs'/case/label/'result.json')['global_tsv']
            with tempfile.TemporaryDirectory(prefix='v4_attribution_') as temp:
                db=sqlite3.connect(str(Path(temp)/'resource.sqlite'));db.execute('pragma cache_size=-8192');db.execute('pragma temp_store=FILE');db.execute('pragma journal_mode=OFF')
                db.execute('create table official(l int,x int,y int,d int,c int,o int,primary key(l,x,y)) without rowid')
                with z.open('eval_temp/'+case+'/overflow_gcells.csv') as f:
                    rows=csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig'))
                    db.executemany('insert into official values(?,?,?,?,?,?)',((int(r['layer'].replace('metal','')),int(r['gcell_col']),int(r['gcell_row']),int(r['demand']),int(r['capacity']),int(r['overflow'])) for r in rows))
                db.execute('create table local(l int,x int,y int,d int,c int,o int,f int,r int,primary key(l,x,y)) without rowid')
                with (refdir/'resources.csv').open(encoding='utf8',newline='') as f:
                    db.executemany('insert into local values(?,?,?,?,?,?,?,?)',((int(r['layer']),int(r['col']),int(r['row']),int(r['demand']),int(r['capacity']),int(r['overflow']),int(r['fixed_demand']),int(r['routing_demand'])) for r in csv.DictReader(f)))
                db.commit();layers={}
                for l in range(1,21):
                    a=db.execute('select count(*),coalesce(sum(o),0) from official where l=?',(l,)).fetchone();b=db.execute('select count(*),coalesce(sum(o),0),coalesce(sum(max(0,f-c)),0),coalesce(sum(o-max(0,f-c)),0),sum(c=0 and d>0) from local where l=? and o>0',(l,)).fetchone()
                    match=db.execute('select count(*),coalesce(sum(abs(a.d-coalesce(b.d,0))),0),coalesce(sum(a.c!=b.c),0),coalesce(sum(b.o>0),0) from official a left join local b using(l,x,y) where a.l=?',(l,)).fetchone()
                    tp=match[3];layers[str(l)]={'official_hotspots':a[0],'reference_hotspots':b[0],'intersection':tp,'hotspot_recall':tp/a[0] if a[0] else None,'hotspot_precision':tp/b[0] if b[0] else None,'demand_mae_on_official_hotspots':match[1]/a[0] if a[0] else None,'capacity_mismatch_on_materialized_intersection':match[2],'official_overflow':a[1],'reference_overflow':b[1],'reference_fixed_baseline_overflow':b[2],'reference_routing_incremental_overflow':b[3],'reference_zero_capacity_demand_hotspots':b[4] or 0,'official_hotspots_missed':a[0]-tp}
                    top=db.execute('select count(*) from (select * from official where l=? order by o desc,x,y limit 1000) a join local b using(l,x,y) where b.o>0',(l,)).fetchone()[0]
                    layers[str(l)]['top1000_coverage']=top/min(a[0],1000) if a[0] else None
                offsets={}
                for dx in [-1,0,1]:
                    for dy in [-1,0,1]:offsets[f'{dx},{dy}']=db.execute('select count(*) from official a join local b on b.l=a.l and b.x=a.x+? and b.y=a.y+? where a.l=1 and b.o>0',(dx,dy)).fetchone()[0]
                if suffix=='005':
                    with (REPORT/'m1_hotspots.csv').open('x',encoding='utf8',newline='') as f:
                        w=csv.writer(f);w.writerow(['col','row','official_demand','official_capacity','official_overflow','reference_fixed','reference_routing','reference_overflow','reference_fixed_baseline','reference_routing_increment'])
                        w.writerows(db.execute('select a.x,a.y,a.d,a.c,a.o,coalesce(b.f,0),coalesce(b.r,0),coalesce(b.o,0),max(0,coalesce(b.f,0)-a.c),coalesce(b.o,0)-max(0,coalesce(b.f,0)-a.c) from official a left join local b using(l,x,y) where a.l=1 order by a.o desc,a.x,a.y'))
                gap[case]={'layers':layers,'m1_alignment_intersections':offsets,'mae_scope':'Only official positive-overflow CSV domain; non-hot official Demand is unavailable. No fitted subtraction.','reference_identity':ref['reference_source_sha256']};db.close()
            # Stream one Net at a time; no full result JSON / Pin index copy.
            high=node_limit=both=all_cross=0;points={};grid_cols=ref['grid_cols'];plane=grid_cols*ref['grid_rows']
            for n in members(PROJECT/'outputs/runs'/case/label/'result.json'):
                if not n['cross_die']:continue
                all_cross+=1;hp=len(n['pins'])>32;big=len(n['nodes'])>4096
                high+=hp;node_limit+=big and not hp;both+=hp and big
                v=int(n['tsv_node']);points[n['net']]=(v%grid_cols,(v//grid_cols)%ref['grid_rows'])
            with old.open('eval_temp/'+case+'/tsv_points.csv') as f:
                old_points={r['net']:(int(r['gcell_col']),int(r['gcell_row'])) for r in csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig'))}
            with z.open('eval_temp/'+case+'/tsv_points.csv') as f:
                platform_points={r['net']:(int(r['gcell_col']),int(r['gcell_row'])) for r in csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig'))}
            assert points==platform_points,'Platform/local TSV index mismatch'
            coverage[case]={'all_tsv_nets':all_cross,'high_pin_excluded':high,'node_limit_excluded_after_pin_guard':node_limit,'both_pin_and_node_limit':both,'v36_stats':stats,'final_positions_changed_vs_v35':sum(old_points[k]!=v for k,v in points.items()),'position_changes_from_initial_vs_redistribution':None,'limitation':'No pre-redistribution per-Net TSV snapshot/Trace in historical run; final differences cannot be causally split. Unranked potential improvements require a new bounded experiment, not historical inference.'}
            counts=collections.Counter(points.values());n=0;mean=m2=hd=0.
            with z.open('eval_temp/'+case+'/heat_gcell.csv') as f:
                for y,row in enumerate(csv.reader(io.TextIOWrapper(f,encoding='utf-8-sig'))):
                    for x,value in enumerate(row):
                        t=float(value);n+=1;delta=t-mean;mean+=delta/n;m2+=delta*(t-mean);hd+=counts.get((x,y),0)*t
            sd=sum(counts.values());dd=sum(v*v for v in counts.values());rho=(hd-sd*mean)/math.sqrt(m2*(dd-sd*sd/n)) if m2>0 and dd>sd*sd/n else 0
            raw=json.loads(z.read('eval_temp/'+case+'/evaluation_report.json'))
            thermal[case]={'reconstructed_platform_pearson':rho,'reported_platform_pearson':raw['thermal_tsv']['correlation_above_ambient'],'reference_pearson':ref['thermal_correlation'],'matrix_cells':n,'absolute_reconstruction_error':abs(rho-raw['thermal_tsv']['correlation_above_ambient']),'density':{'unique_gcells':len(counts),'colocated_gcells':sum(v>1 for v in counts.values()),'max_per_gcell':max(counts.values())}}
            print(case,'M1',layers['1'],'thermal',thermal[case],flush=True)
    save(REPORT/'official_model_gap.json',gap);save(REPORT/'tsv_coverage.json',coverage);save(REPORT/'thermal_model_alignment.json',thermal)
    m=gap['open_3d_case005']['layers']['1']
    (REPORT/'m1_resource_attribution.md').write_text('# case005 M1 resource attribution\n\n原始 DEF/LEF 几何按参考模型的 Track 并集计算，固定基线与 Routing 增量保持分离。官方 CSV 只有 Total Demand，不能据此声称完成官方固定/路由因果分解。\n\n```json\n'+json.dumps(m,ensure_ascii=False,indent=2)+'\n```\n\n本模型固定基线不等于官方固定基线；Pin/OBS 并集一般不超过实际 Track Capacity，但无 Track Pin 的虚拟 token 是例外。保留 M1 Pin 接入，仅修复非必需内部路径。Via 不是独立额外 Demand；每 Net 合并节点需求，真实 Pin GCell 抵扣。具体 Pin/OBS 位图独立核验见 m1_geometry_summary.json。\n',encoding='utf8')
    (REPORT/'tsv_coverage_diagnosis.md').write_text('# V3.6 TSV coverage\n\n'+''.join('## '+k+'\n\n```json\n'+json.dumps(v,ensure_ascii=False,indent=2)+'\n```\n' for k,v in coverage.items()),encoding='utf8')

if __name__=='__main__':analyze()
