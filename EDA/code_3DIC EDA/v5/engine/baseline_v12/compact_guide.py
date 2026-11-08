"""Input-only connected-subgraph compaction of an existing valid GUIDE.

Every retained GCell already belonged to the same net. Preserve every covered
physical PIN shape and every TSV candidate box. Never add routing resources or
move TSVs; accept only non-increasing rectangle-proxy wire cost.
"""
import argparse,heapq,json,time
from collections import defaultdict,deque,Counter
from pathlib import Path
from design import Design
from guide_cost import cost_adapter
from guide_rules import tsv_candidates,select_tsv
from validate import read_guide,check_net,validate


def compact_net(d,r,name,rects,max_nodes=40000):
    used=set()
    for box in rects:used.update(r.footprint(box))
    required=set();grid=r.d._guide_grid
    for shapes in d.net_pin_shapes(name):
        for layer,box in shapes:
            required.update(used & r.footprint((*box,layer)))
    candidates=tsv_candidates(rects)
    for box in candidates:
        for layer in (10,11):required.update(r.footprint((*box,layer)))
    cross=len({(p.layer-1)//10 for p in d.points(name)})==2
    bridge=None
    if cross:
        box=select_tsv(rects,[s for pin in d.net_pin_shapes(name) for s in pin])
        col,row=grid.cell((box[0]+box[2])/2,(box[1]+box[3])/2)
        bridge=row*r.cols+col
    if not required:return rects,0
    horizontal={layer:d.layers[layer]['direction']=='HORIZONTAL' for layer in d.layers}
    def neighbors(key,space):
        layer,index=divmod(key,r.cells);row,col=divmod(index,r.cols)
        if horizontal[layer]:
            if col and key-1 in space:yield key-1,1
            if col+1<r.cols and key+1 in space:yield key+1,1
        else:
            if row and key-r.cols in space:yield key-r.cols,1
            if row+1<r.rows and key+r.cols in space:yield key+r.cols,1
        for other in (layer-1,layer+1):
            if other not in horizontal:continue
            if {layer,other}=={10,11} and index!=bridge:continue
            adjacent=other*r.cells+index
            if adjacent in space:yield adjacent,7 if {layer,other}=={10,11} else 4
    alive=set(used);degree={v:sum(1 for _ in neighbors(v,alive)) for v in alive}
    queue=deque(v for v in sorted(alive) if degree[v]<=1 and v not in required)
    while queue:
        v=queue.popleft()
        if v not in alive or degree[v]>1 or v in required:continue
        adj=list(neighbors(v,alive));alive.remove(v)
        for u,_ in adj:
            degree[u]-=1
            if degree[u]<=1 and u not in required:queue.append(u)
    edge_count=sum(degree[v] for v in alive)//2
    if edge_count>=len(alive) and len(alive)<=max_nodes:
        root=10*r.cells+bridge if bridge is not None else min(required)
        tree={root};todo=required-tree;distance={root:0};parent={};heap=[(0,root)]
        while todo and heap:
            dist,v=heapq.heappop(heap)
            if dist!=distance.get(v):continue
            if v in todo:
                path=[];cur=v
                while cur not in tree:path.append(cur);cur=parent[cur]
                tree.update(path);todo.difference_update(tree)
                for u in path:distance[u]=0;heapq.heappush(heap,(0,u))
                continue
            for u,weight in neighbors(v,alive):
                trial=dist+weight
                if trial<distance.get(u,float('inf')):
                    distance[u]=trial;parent[u]=v;heapq.heappush(heap,(trial,u))
        if todo:raise ValueError(name+': original cell graph disconnected')
        alive=tree
    groups=defaultdict(list)
    for key in alive:
        layer,index=divmod(key,r.cells);row,col=divmod(index,r.cols)
        groups[layer,row if horizontal[layer] else col].append(col if horizontal[layer] else row)
    result=[]
    for (layer,fixed),positions in sorted(groups.items()):
        positions.sort();start=last=positions[0]
        def emit(a,b):
            return (r.x[a],r.y[fixed],r.x[b+1],r.y[fixed+1],layer) if horizontal[layer] else (r.x[fixed],r.y[a],r.x[fixed+1],r.y[b+1],layer)
        for pos in positions[1:]:
            if pos!=last+1:result.append(emit(start,last));start=pos
            last=pos
        result.append(emit(start,last))
    if tsv_candidates(result)!=candidates:return rects,0
    before,_=r.candidate(rects,0);after,_=r.candidate(result,0)
    if after>before+1e-9:return rects,0
    if len(alive)==len(used) and after>=before-1e-9:return rects,0
    check_net(d,name,result)
    return result,len(used)-len(alive)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--def',dest='def_file',type=Path,required=True)
    for name in ('lef','seed-guide','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--max-nodes',type=int,default=40000);a=p.parse_args()
    if a.output.exists():p.error('Output exists')
    started=time.monotonic();d=Design(a.def_file,a.lef);r=cost_adapter(d);stats=Counter();old=new=0
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('w',encoding='utf-8',newline='\n') as stream:
        for name,rects in read_guide(a.seed_guide):
            candidate,removed=compact_net(d,r,name,rects,a.max_nodes)
            c0,_=r.candidate(rects,0);c1,_=r.candidate(candidate,0);old+=c0;new+=c1
            stats.update(nets=1,removed_cells=removed,changed_nets=candidate!=rects,rectangles=len(candidate))
            stream.write(name+'\n(\n')
            for x,y,xx,yy,layer in candidate:stream.write(f'{x} {y} {xx} {yy} {d.layers[layer]["name"]}\n')
            stream.write(')\n')
            if stats['nets']%20000==0:print('compacted',stats['nets'],dict(stats),flush=True)
    from guide_invariants import check_tsv_geometry
    checked=validate(d,a.output);gate=check_tsv_geometry(a.seed_guide,a.output)
    report=dict(version='0.8-input-only-compaction',initial={'route_cost_dbu':old*r.step},final={'route_cost_dbu':new*r.step},
                stats=dict(stats),validation=checked,interface_gate=gate,seconds=time.monotonic()-started,
                routing_cells='SUBSET_OF_SAME_NET_SEED',official_score=None)
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)

if __name__=='__main__':main()
