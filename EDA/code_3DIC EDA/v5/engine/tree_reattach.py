"""Experimental TSV reattachment to existing same-die routing components.

Move the landing, attach to nearby tree branches rather than the old landing,
then prune abandoned branches before accepting. All geometry and per-cell risk
checks remain hard gates. No case names, routes, or scoring reports are used.
"""
import argparse,heapq,json,math,time
from bisect import bisect_left,bisect_right
from collections import deque
from pathlib import Path
from design import Design,Point
from grid_router import GridRouter
from guide_rules import select_tsv
from compact_guide import compact_net
from guarded_thermal import GuardedThermal
from joint_thermal import JointThermal
from routing_thermal import ThermalPreference
from adaptive_router import merge_corridors
from validate import read_guide,validate,check_net
from tsv_obstacles import obstacles_for


def cut_landing(d,grid,rects,cell):
    col,row=cell;result=[]
    for box in rects:
        x,y=grid.center(col,row)
        if box[4]!=10 or not (box[0]<=x<box[2] and box[1]<=y<box[3]):result.append(box);continue
        if d.layers[10]['direction']=='HORIZONTAL':
            if box[0]<grid.x[col]:result.append((box[0],box[1],grid.x[col],box[3],10))
            if grid.x[col+1]<box[2]:result.append((grid.x[col+1],box[1],box[2],box[3],10))
        else:
            if box[1]<grid.y[row]:result.append((box[0],box[1],box[2],grid.y[row],10))
            if grid.y[row+1]<box[3]:result.append((box[0],grid.y[row+1],box[2],box[3],10))
    return result


def route_context(d,r,grid,name,rects,max_nodes=12000):
    box=select_tsv(rects,[s for pin in d.net_pin_shapes(name) for s in pin]);cell=grid.cell((box[0]+box[2])/2,(box[1]+box[3])/2)
    base=cut_landing(d,grid,rects,cell);used=set()
    for box in base:used.update(r.footprint(box))
    if len(used)>max_nodes:raise ValueError('Reattachment cell budget exceeded')
    def adjacent(key):
        layer,index=divmod(key,r.cells);row,col=divmod(index,r.cols)
        if d.layers[layer]['direction']=='HORIZONTAL':
            if col:yield key-1
            if col+1<r.cols:yield key+1
        else:
            if row:yield key-r.cols
            if row+1<r.rows:yield key+r.cols
        for other in (layer-1,layer+1):
            if other in d.layers and (other-1)//10==(layer-1)//10:yield other*r.cells+index
    remaining=set(used);components=[]
    while remaining:
        root=min(remaining);remaining.remove(root);component={root};todo=deque([root])
        while todo:
            key=todo.popleft()
            for other in adjacent(key):
                if other in remaining:remaining.remove(other);component.add(other);todo.append(other)
        tier=(root//r.cells-1)//10;components.append((tier,component))
    if len(components)>8:raise ValueError('Too many disconnected attachment components')
    return dict(base=base,used=used,components=components,old_cell=cell)


def reattach(d,r,grid,context,center,mode='nearest',allowed=None,additions_allowed=None):
    router=GridRouter(d);result=list(context['base']);col,row=grid.cell(*center)
    for tier,component in context['components']:
        current_used=r.candidate(result,0)[1] if additions_allowed is not None else None
        target=10 if tier==0 else 11;scored=[]
        for key in component:
            layer,index=divmod(key,r.cells);y,x=divmod(index,r.cols);xy=grid.center(x,y)
            planar=(abs(xy[0]-center[0])+abs(xy[1]-center[1]))/r.step
            scored.append((planar,abs(layer-target),layer,xy))
        nearest=min(scored,key=lambda x:(x[0],x[1],x[2],x[3]));anchors=[nearest]
        if mode=='wire':
            for candidate in heapq.nsmallest(3,scored,key=lambda x:(x[0]+3*x[1],x[0],x[2],x[3])):
                if candidate[3] not in {a[3] for a in anchors}:anchors.append(candidate)
        layers=[l for l in d.layers if (l-1)//10==tier and l not in (10,11)]
        pairs=[(h,v) for h in layers for v in layers if abs(h-v)==1 and d.layers[h]['direction']=='HORIZONTAL' and d.layers[v]['direction']=='VERTICAL']
        best=None
        for _,_,layer,xy in anchors:
            choices=sorted(pairs,key=lambda pair:(min(abs(layer-pair[0])+abs(target-pair[1]),abs(layer-pair[1])+abs(target-pair[0])),pair))[:2]
            for pair in choices:
                for pattern in ('hv','vh'):
                    bridge=set();router.path_pattern=pattern
                    router.connect(bridge,Point(*xy,layer),Point(*center,target),pair)
                    if additions_allowed is None:
                        candidate=merge_corridors(result+list(bridge),d.layers)
                        if allowed is not None and not allowed(candidate):continue
                        cost,_=r.candidate(candidate,0);chosen=candidate
                    else:
                        bridge=merge_corridors(list(bridge),d.layers);cost,used=r.candidate(bridge,0)
                        if not additions_allowed(used,current_used):continue
                        def weight(key):return (r.stepx if d.layers[key//r.cells]['direction']=='HORIZONTAL' else r.stepy)/r.step
                        planar=math.fsum(weight(k) for k in used)
                        cost=math.fsum(weight(k) for k in used-current_used)+max(0,cost-planar)
                        chosen=bridge
                    key=(cost,pair,pattern,layer,xy)
                    if best is None or key<best[0]:best=key,chosen
        if best is None:raise ValueError('No risk-preserving component attachment')
        result=best[1] if additions_allowed is None else merge_corridors(result+best[1],d.layers)
        if allowed is not None and not allowed(result):raise ValueError('Selected component attachment violates resource gate')
    return result


class TreeReattach(GuardedThermal):
    def __init__(self,d,heat,budget=512):
        if budget<1:raise ValueError('Reattachment budget must be positive')
        super().__init__(d,heat,budget);self.global_thermal=False;self.context=None

    def tree_centers(self,rects):
        grid=self.router.grid;heat=self.router.heat;centers=set()
        for box in rects:
            if box[4] in (10,11):continue
            horizontal=self.d.layers[box[4]]['direction']=='HORIZONTAL'
            a=grid.x_index[box[0]] if horizontal else grid.y_index[box[1]]
            b=(grid.x_index[box[2]] if horizontal else grid.y_index[box[3]])-1
            fixed=grid.y_index[box[1]] if horizontal else grid.x_index[box[0]]
            samples=heat.sample_cols if horizontal else heat.sample_rows
            positions={a,b};positions.update(samples[bisect_left(samples,a):bisect_right(samples,b)])
            for position in positions:
                for delta in (-2,-1,0,1,2):
                    pos=position+delta
                    if a<=pos<=b:centers.add(grid.center(pos,fixed) if horizontal else grid.center(fixed,pos))
        return centers

    def initialize(self,guide):
        JointThermal.initialize(self,guide);old_cells=dict(self.cross_records);heap=[];baseline=self.thermal.score()
        derivative=45*min(self.reference,self.wire)/max(1,self.wire)**2;obstacles=obstacles_for(self.d)
        for name,rs in read_guide(guide):
            if name not in old_cells:continue
            points=self.d.points(name)
            if len(points)>128:continue
            _,own,old=self.counts(name,rs);oldxy=self.router.grid.center(*old)
            groups=[[p for p in points if (p.layer-1)//10==tier] for tier in (0,1)]
            boxes=[(min(p.x for p in ps),min(p.y for p in ps),max(p.x for p in ps),max(p.y for p in ps)) for ps in groups if ps]
            def extension(xy):return sum(max(0,b[0]-xy[0],xy[0]-b[2])+max(0,b[1]-xy[1],xy[1]-b[3]) for b in boxes)
            best=-float('inf');old_extension=extension(oldxy)
            for xy in self.tree_centers(rs):
                cell=self.router.grid.cell(*xy)
                if cell==old or self.router.tsv_usage[cell] or obstacles.conflicts(xy):continue
                keys=[l*self.r.cells+cell[1]*self.r.cols+cell[0] for l in (10,11)]
                if any(self.r.capacity(k)<=0 or self.r.usage.get(k,0)>own.get(k,0) for k in keys):continue
                covariance=self.thermal.covariance+self.thermal.value(cell)-self.thermal.value(old)
                squares=self.thermal.squares+2*(1-self.router.tsv_usage[old])
                benefit=.25*(self.thermal.score(covariance,squares)-baseline)
                score=benefit-derivative*max(0,extension(xy)-old_extension)/self.r.step
                best=max(best,score)
            if best<=1e-12:continue
            item=(best,name)
            if len(heap)<self.max_reroutes:heapq.heappush(heap,item)
            elif item>heap[0]:heapq.heapreplace(heap,item)
        self.targets={name for _,name in heap};self.stats['positive_potential_targets']=len(self.targets)

    def local_centers(self,points):
        grid=self.router.grid;obstacles=obstacles_for(self.d);result=[]
        groups=[[p for p in points if (p.layer-1)//10==tier] for tier in (0,1)]
        boxes=[(min(p.x for p in ps),min(p.y for p in ps),max(p.x for p in ps),max(p.y for p in ps)) for ps in groups if ps]
        def extension(xy):return sum(max(0,b[0]-xy[0],xy[0]-b[2])+max(0,b[1]-xy[1],xy[1]-b[3]) for b in boxes)
        old_extension=extension(grid.center(*self.old_cell))
        for xy in self.tree_centers(self.original):
            cell=grid.cell(*xy)
            if cell==self.old_cell or self.router.tsv_usage[cell] or obstacles.conflicts(xy):continue
            if any(self.r.usage.get(l*self.r.cells+cell[1]*self.r.cols+cell[0],0)>0 or self.r.capacity(l*self.r.cells+cell[1]*self.r.cols+cell[0])<=0 for l in (10,11)):continue
            covariance=self.thermal.covariance+self.thermal.value(cell)-self.thermal.value(self.old_cell)
            squares=self.thermal.squares+2*(self.router.tsv_usage[cell]-(self.router.tsv_usage[self.old_cell]+1)+1)
            wire=self.wire+(extension(xy)-old_extension)/self.r.step
            score=self.objective(max(1,wire),self.penalty,self.thermal.score(covariance,squares));result.append((-score,xy))
        return [xy for _,xy in sorted(result)]

    def shared_build(self,points,center):
        best=None;pins=self.r.pin_cells(self.active_name)
        def additions_allowed(used,current_used):
            col,row=self.router.grid.cell(*center)
            landing={l*self.r.cells+row*self.r.cols+col for l in (10,11)}
            counts={k:(self.r.capacity(k) if k in landing else 1) for k in (used-current_used-pins)|(used&landing)}
            return self.safe_counts(counts)
        for mode in ('nearest','wire'):
            try:
                rs=reattach(self.d,self.r,self.router.grid,self.context,center,mode,additions_allowed=additions_allowed)
                check_net(self.d,self.active_name,rs);rs,_=compact_net(self.d,self.r,self.active_name,rs,12000)
                cost,counts,_=self.counts(self.active_name,rs)
                if not self.safe_counts(counts):continue
            except ValueError:continue
            added=math.fsum(self.phi(k,self.r.usage.get(k,0)+n)-self.phi(k,self.r.usage.get(k,0)) for k,n in counts.items())
            previous=math.fsum(self.phi(k,self.r.usage.get(k,0)+n)-self.phi(k,self.r.usage.get(k,0)) for k,n in self.old_counts.items())
            penalty=max(0,self.penalty-previous+added)
            objective=self.objective(self.wire-self.old_wire+cost,penalty,self.thermal.score())
            key=(-objective,cost,mode)
            if best is None or key<best[0]:best=key,rs
        if best is None:raise ValueError('No valid tree reattachment')
        return best[1]

    def reroute(self,name,rs):
        if name not in self.targets:return rs
        try:self.context=route_context(self.d,self.r,self.router.grid,name,rs)
        except ValueError:self.stats['context_budget_rejected']+=1;return rs
        self.old_wire=self.counts(name,rs)[0]
        return super().reroute(name,rs)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--def',dest='def_file',type=Path,required=True)
    for key in ('lef','thermal-report','seed-guide','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--max-reroutes',type=int,default=512);a=p.parse_args()
    if a.output.exists():p.error('Output exists')
    start=time.monotonic();d=Design(a.def_file,a.lef);validate(d,a.seed_guide)
    e=TreeReattach(d,ThermalPreference(d,a.thermal_report,2,'region'),a.max_reroutes);e.initialize(a.seed_guide);initial=e.snapshot();print('INITIAL',json.dumps(initial),dict(e.stats),flush=True)
    a.output.parent.mkdir(parents=True,exist_ok=True);temp=a.output.with_suffix('.tmp.guide')
    with temp.open('x',encoding='utf8',newline='\n') as f:
        for i,(name,rs) in enumerate(read_guide(a.seed_guide),1):
            rs=e.reroute(name,rs);f.write(name+'\n(\n')
            for x,y,xx,yy,l in rs:f.write(f'{x} {y} {xx} {yy} {d.layers[l]["name"]}\n')
            f.write(')\n')
            if i%20000==0:print('REATTACH',i,dict(e.stats),flush=True)
    report=dict(version='0.14-tree-reattachment-experiment',initial=initial,final=e.snapshot(),stats=dict(e.stats),validation=validate(d,temp),seconds=time.monotonic()-start,official_score=None)
    temp.replace(a.output);a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':main()
