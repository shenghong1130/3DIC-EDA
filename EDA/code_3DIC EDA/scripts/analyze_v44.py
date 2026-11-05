"""Read-only frozen reference CSV audit; bounded heaps and one result Net at a time."""
import csv, collections, heapq, json, math, sys
from pathlib import Path
from common import PROJECT, sha256
sys.path.insert(0,str(PROJECT/'ui'))
from result_store import Cursor

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8') as f: json.dump(value,f,ensure_ascii=False,indent=2)

def audit(csvpath,result):
    layers=collections.defaultdict(collections.Counter); hist=collections.Counter(); top=[]; total=0
    with csvpath.open(newline='') as f:
        for row in csv.DictReader(f):
            r={k:int(v) for k,v in row.items()}; c,fixed,u,o=r['capacity'],r['fixed_demand'],r['routing_demand'],r['overflow']
            assert o==max(0,fixed+u-c) and r['demand']==fixed+u
            b=max(0,fixed-c); layers[r['layer']].update(cells=1,overflow=o,fixed_baseline=b,routing_increment=o-b,
                routing_demand=u,fixed_demand=fixed,hotspots=int(o>0),zero_capacity_demand=(fixed+u if c==0 else 0))
            hist[o]+=1; total+=o
            if o:
                item=(o,r['node'],r['col'],r['row'],r['layer'],c,fixed,u)
                if len(top)<1000:heapq.heappush(top,item)
                elif item>top[0]:heapq.heapreplace(top,item)
    def top_sum(n):
        value=0
        for over,count in sorted(hist.items(),reverse=True):
            take=min(n,count);value+=take*over;n-=take
            if not n:break
        return value
    active=sum(hist.values()); hot=active-hist[0]
    distribution={str(p):{'emitted_cells':math.ceil(active*p), 'overflow':top_sum(math.ceil(active*p)),
                          'fraction':top_sum(math.ceil(active*p))/total if total else 0,
                          'hot_cells_only_overflow':top_sum(math.ceil(hot*p))} for p in [.01,.05,.1]}
    hotids={v[1]:v[0] for v in top}; owners=[]; tsv=collections.Counter(); count=0; vias=0
    cursor=Cursor(result)
    try:
        cursor.take('{')
        while cursor.char()!='}':
            key=cursor.value()[0];cursor.take(':')
            if key in ('x_lines','y_lines'):
                data=cursor.value()[0]
                if key=='x_lines':nx=len(data)-1
                else:ny=len(data)-1
            elif key=='nets':
                for net,_,_ in cursor.array():
                    count+=1; pins={p.get('access_node',p['node']) for p in net['pins']}; degree=collections.Counter()
                    for a,b in net['edges']:
                        degree[a]+=1;degree[b]+=1
                        vias+=a//(nx*ny)!=b//(nx*ny) and min(a,b)//(nx*ny)!=10
                    hits=[v for v in net['nodes'] if v in hotids]
                    impact=sum(hotids[v] for v in hits)
                    movable=sum(v not in pins and degree[v]==2 and (not net['cross_die'] or v not in (net['tsv_node'],net['tsv_node']+nx*ny)) for v in hits)
                    item=(impact,net['net'],len(hits),movable,len(net['pins']),len(net['nodes']))
                    if impact:
                        if len(owners)<40:heapq.heappush(owners,item)
                        elif item>owners[0]:heapq.heapreplace(owners,item)
                    if net['cross_die']:tsv[net['tsv_node']]+=1
            else:cursor.skip()
            if cursor.char()=='}':break
            cursor.take(',')
    finally:cursor.close()
    return {'csv_sha256':sha256(csvpath),'result_sha256':sha256(result),'by_layer':dict(layers),
            'overflow':total,'fixed_baseline':sum(r['fixed_baseline'] for r in layers.values()),
            'routing_increment':sum(r['routing_increment'] for r in layers.values()),'emitted_resource_cells':active,
            'hotspot_cells':hot,'top_fraction':distribution,'top_cells':sorted(top,reverse=True)[:40],
            'net_hotspot_incidence_proxy':sorted(owners,reverse=True),'net_incidence_note':'NOT additive demand attribution; degree-two count indicates structurally movable path only, not legal feasibility',
            'net_count':count,'via_edge_count':vias,'tsv_count':sum(tsv.values()),'tsv_occupied_cells':len(tsv),
            'max_tsv_colocation':max(tsv.values(),default=0),'tsv_footprint_overflow':'UNKNOWN',
            'tsv_access_path_overflow':'UNKNOWN','tsv_attribution_reason':'CSV lacks per-Net own-Pin exemptions and per-track footprint provenance; cannot separate nonadditive max(Guide,TSV) from aggregate demand',
            'scope':'REFERENCE_20261103; emitted demanded cells (unused GCells excluded from percentile denominator)',
            'maximum_reader_chars':cursor.maximum}

def main():
    root=PROJECT/'outputs/reports/v4_4_congestion_research'; root.mkdir(parents=True,exist_ok=True)
    rows={}
    for suffix in ('001','003','005'):
        case='open_3d_case'+suffix;label='case'+suffix+'_v4_3'
        folder=PROJECT/'outputs/reports/scoring/reference_20261103'/case/label
        if not (folder/'resources.csv').exists():rows[case]={'status':'NOT_RUN','exact_reference_decomposition':'UNKNOWN'};continue
        rows[case]=audit(folder/'resources.csv',PROJECT/'outputs/runs'/case/label/'result.json')
        print(case,rows[case]['overflow'],rows[case]['fixed_baseline'],flush=True)
    save(root/'baseline_audit.json',rows)
    for name,keys in {'congestion_by_layer':['by_layer','scope'],'hotspot_analysis':['top_fraction','top_cells','hotspot_cells','net_hotspot_incidence_proxy','net_incidence_note'],
        'tsv_congestion_analysis':['tsv_count','tsv_occupied_cells','max_tsv_colocation','tsv_footprint_overflow','tsv_access_path_overflow','tsv_attribution_reason'],
        'fixed_vs_routing_overflow':['overflow','fixed_baseline','routing_increment','scope']}.items():
        save(root/(name+'.json'),{c:{k:r.get(k,'UNKNOWN') for k in keys} for c,r in rows.items()})

if __name__=='__main__':main()
