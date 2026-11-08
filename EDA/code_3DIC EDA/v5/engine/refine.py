#!/usr/bin/env python3
"""Selective rip-up/reroute with an explicit global wire/congestion objective.

Only supplied DEF/LEF/thermal inputs are required. An optional self-generated
seed GUIDE avoids repeating the initialization in development experiments.
Scores used for acceptance remain local proxy scores, not official grades.
"""
import argparse
from bisect import bisect_right
from collections import Counter
import heapq
import json
import math
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import time
from design import Design
from router import Router,tree_edges
from routing_resources import RoutingResources
from validate import read_guide,check_net,check_net_legacy,overlaps
from grid_router import GridRouter
from guide_rules import select_tsv


def tsv_center(rects,points):
    if len({(p.layer-1)//10 for p in points})<2:
        return None
    candidates=[]
    for a in (r for r in rects if r[4]==10):
        for b in (r for r in rects if r[4]==11):
            if overlaps(a,b,True):
                candidates.append((max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])))
    candidates=list(dict.fromkeys(candidates))
    if not 1<=len(candidates)<=2:
        raise ValueError('Invalid TSV candidate count')
    if len(candidates)==2:
        interface=[p for p in points if p.layer in (10,11)]
        inside=[bool(interface) and all(r[0]<=p.x<=r[2] and r[1]<=p.y<=r[3] for p in interface) for r in candidates]
        if not any(inside):
            raise ValueError('Ambiguous TSV candidates')
        if inside[0]!=inside[1]:
            candidates=[candidates[0 if not inside[0] else 1]]
    r=max(candidates,key=lambda r:(r[2]-r[0])*(r[3]-r[1]))
    return ((r[0]+r[2])/2,(r[1]+r[3])/2)


class Refiner:
    def __init__(self,design,grid_mode=True,resources=None,router_override=None):
        self.d=design
        self.r=resources if resources is not None else RoutingResources(design)
        self.grid_mode=grid_mode
        self.router=router_override if router_override is not None else (GridRouter if grid_mode else Router)(design)
        self.route_cost=0.0
        self.penalty=0.0
        self.reference=0.0
        self.stats=Counter()
        self.increment_cache=None
        for name in design.nets:
            pts=design.points(name)
            edges=tree_edges(pts)
            length=sum(abs(pts[i].x-pts[j].x)+abs(pts[i].y-pts[j].y) for i,j in edges)
            bends=sum(pts[i].x!=pts[j].x and pts[i].y!=pts[j].y for i,j in edges)
            crossing=len({(p.layer-1)//10 for p in pts})==2
            self.reference+=length/self.r.step+3*(bends+10*crossing)+6*crossing

    def phi(self,key,amount):
        cap=self.r.capacity(key)
        if cap<=0:
            return 10000.0 if amount>0 else 0.0
        return (0.1*max(0,amount-cap)/cap)**2

    def counts(self,rects,points,name=None):
        length,used=self.r.candidate(rects,0)
        counts=Counter({key:1 for key in used})
        if self.grid_mode and name is not None and len({(p.layer-1)//10 for p in points})==2:
            box=select_tsv(rects,[shape for pin in self.d.net_pin_shapes(name) for shape in pin])
            center=((box[0]+box[2])/2,(box[1]+box[3])/2)
        else:
            center=tsv_center(rects,points)
        if center is not None:
            col=min(self.r.cols-1,bisect_right(self.r.x,center[0])-1)
            row=min(self.r.rows-1,bisect_right(self.r.y,center[1])-1)
            for layer in (10,11):
                counts[layer*self.r.cells+row*self.r.cols+col]+=1
        return length,counts,center

    def update(self,counts,sign):
        for key,count in counts.items():
            amount=self.r.usage.get(key,0)+sign*count
            if amount<0:
                raise ValueError('Reroute removed a demand not in the resource model')
            if amount:
                self.r.usage[key]=amount
            else:
                self.r.usage.pop(key,None)

    def increment(self,counts):
        values=[]
        for key,count in counts.items():
            cache_key=(key,count)
            value=self.increment_cache.get(cache_key) if self.increment_cache is not None else None
            if value is None:
                amount=self.r.usage.get(key,0)
                value=self.phi(key,amount+count)-self.phi(key,amount)
                if self.increment_cache is not None:
                    self.increment_cache[cache_key]=value
            values.append(value)
        return math.fsum(values)

    def objective(self,cost,penalty):
        ratio=max(cost/max(self.reference,1e-9),1e-9)
        return 45*min(ratio,1/ratio)+30/math.sqrt(1+max(0,penalty))

    def initialize(self,guide):
        for name,rects in read_guide(guide):
            if self.grid_mode:check_net(self.d,name,rects)
            cost,counts,_=self.counts(rects,self.d.points(name),name)
            self.route_cost+=cost
            self.update(counts,1)
        self.penalty=math.fsum(self.phi(key,amount) for key,amount in self.r.usage.items())
        return self.snapshot()

    def snapshot(self):
        return {'route_cost_dbu':self.route_cost*self.r.step,
                'congestion_penalty':self.penalty,'congestion_estimate':100/math.sqrt(1+self.penalty),
                'wire_congestion_objective_PROXY':self.objective(self.route_cost,self.penalty)}

    def targets(self,guide,limit):
        heap=[]
        for name,rects in read_guide(guide):
            pts=self.d.points(name)
            _,counts,center=self.counts(rects,pts,name)
            mandatory=set()
            for p in pts:
                col=min(self.r.cols-1,bisect_right(self.r.x,p.x)-1)
                row=min(self.r.rows-1,bisect_right(self.r.y,p.y)-1)
                mandatory.add(p.layer*self.r.cells+row*self.r.cols+col)
            if center is not None:
                col=min(self.r.cols-1,bisect_right(self.r.x,center[0])-1)
                row=min(self.r.rows-1,bisect_right(self.r.y,center[1])-1)
                mandatory.update(layer*self.r.cells+row*self.r.cols+col for layer in (10,11))
            risk=math.fsum(self.phi(key,self.r.usage.get(key,0))-self.phi(key,max(0,self.r.usage.get(key,0)-count))
                           for key,count in counts.items() if key not in mandatory)
            if risk<=1e-5:
                continue
            item=(risk,name)
            if len(heap)<limit:
                heapq.heappush(heap,item)
            elif item>heap[0]:
                heapq.heapreplace(heap,item)
        return {name for risk,name in heap}

    def reroute(self,name,rects):
        points=self.d.points(name)
        old_cost,old_counts,center=self.counts(rects,points,name)
        self.update(old_counts,-1)
        self.increment_cache={}
        base_penalty=max(0,self.penalty-self.increment(old_counts))
        base_cost=self.route_cost-old_cost
        best=(self.objective(self.route_cost,self.penalty),rects,old_cost,old_counts,self.penalty)
        self.router.tsv_override=center
        if self.grid_mode:self.router.net_name=name
        variants=[(shift,pattern,0) for shift in (0,1,2,4) for pattern in ('hv','vh')]
        variants += [(0,pattern,offset) for pattern in ('zx','zy') for offset in (-2,0,2)]
        for shift,pattern,offset in variants:
            self.router.layer_shift=shift
            self.router.path_pattern=pattern
            self.router.detour_steps=offset
            try:candidate,_=self.router._route_net_fixed(points)
            except ValueError:continue
            if candidate==rects:
                continue
            try:
                cost,counts,new_center=self.counts(candidate,points,name)
            except ValueError:
                continue
            if new_center!=center:
                continue
            penalty=base_penalty+self.increment(counts)
            # This optimization specifically targets congestion: do not accept a
            # worse congestion penalty merely to improve the wire proxy.
            if penalty>self.penalty+1e-9:
                continue
            objective=self.objective(base_cost+cost,penalty)
            if objective>best[0]+1e-9:
                try:
                    (check_net if self.grid_mode else check_net_legacy)(self.d,name,candidate)
                except ValueError:
                    continue
                best=(objective,candidate,cost,counts,penalty)
        _,chosen,cost,counts,penalty=best
        self.update(counts,1)
        self.increment_cache=None
        if chosen!=rects:
            self.stats['accepted']+=1
        self.stats['attempted']+=1
        self.route_cost=base_cost+cost
        self.penalty=penalty
        return chosen

    def run_pass(self,source,destination,limit):
        selected=self.targets(source,limit)
        print('Reroute targets:',len(selected),flush=True)
        before=self.snapshot()
        start_accepted=self.stats['accepted']
        rectangles_out=0
        with Path(destination).open('w',encoding='utf-8',newline='\n') as stream:
            for name,rects in read_guide(source):
                if name in selected:
                    rects=self.reroute(name,rects)
                rectangles_out+=len(rects)
                stream.write(name+'\n(\n')
                for a,b,c,d,layer in rects:
                    stream.write(f'{a} {b} {c} {d} {self.d.layers[layer]["name"]}\n')
                stream.write(')\n')
        recomputed=math.fsum(self.phi(key,amount) for key,amount in self.r.usage.items())
        if abs(recomputed-self.penalty)>1e-6*max(1,recomputed):
            raise ValueError('Incremental congestion model drifted')
        self.penalty=recomputed
        self.stats['final_rectangles']=rectangles_out
        after=self.snapshot()
        if after['wire_congestion_objective_PROXY']<before['wire_congestion_objective_PROXY']-1e-7:
            raise ValueError('Reroute pass regressed its acceptance objective')
        return {'before':before,'after':after,'accepted':self.stats['accepted']-start_accepted,'targets':len(selected)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--def',dest='def_file',type=Path,required=True)
    p.add_argument('--lef',dest='lef_file',type=Path,required=True)
    p.add_argument('--thermal-report',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--pin-mode',choices=['physical','legacy'],default='physical')
    p.add_argument('--seed-guide',type=Path)
    p.add_argument('--passes',type=int,default=2)
    p.add_argument('--max-reroutes',type=int,default=6000)
    p.add_argument('--layer-shift',type=int,default=0)
    p.add_argument('--layer-policy',choices=['fixed','balanced'],default='fixed')
    p.add_argument('--congestion-weight',type=float,default=12)
    p.add_argument('--thermal-weight',type=float,default=1)
    p.add_argument('--thermal-policy',choices=['sample','region'],default='region')
    p.add_argument('--path-pattern',choices=['hv','vh','zx','zy'],default='hv')
    args=p.parse_args()
    if args.passes<1 or args.max_reroutes<1:
        p.error('passes and max-reroutes must be positive')
    started=time.perf_counter()
    args.output=args.output.resolve()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.refine-',dir=args.output.parent)).resolve()
    if stage.parent!=args.output.parent or not stage.name.startswith('.refine-'):
        raise ValueError('Unexpected refinement staging directory')
    source=args.seed_guide
    if source is None:
        source=stage/'seed.guide'
        command=[sys.executable,str(Path(__file__).with_name('router.py')),
                 '--def',str(args.def_file),'--lef',str(args.lef_file),
                 '--thermal-report',str(args.thermal_report),'--output',str(source),
                 '--pin-mode',args.pin_mode,'--thermal-weight',str(args.thermal_weight),
                 '--thermal-policy',args.thermal_policy,'--layer-policy',args.layer_policy,
                 '--layer-shift',str(args.layer_shift),'--congestion-weight',str(args.congestion_weight),
                 '--path-pattern',args.path_pattern]
        subprocess.run(command,check=True)
    d=Design(args.def_file,args.lef_file,args.pin_mode)
    print('Building complete resource model',flush=True)
    engine=Refiner(d)
    initial=engine.initialize(source)
    print('Initial:',json.dumps(initial),flush=True)
    passes=[]
    for i in range(args.passes):
        target=stage/f'pass{i+1}.guide'
        result=engine.run_pass(source,target,args.max_reroutes)
        passes.append(result)
        print('Pass',i+1,json.dumps(result),flush=True)
        source=target
        if not result['accepted']:
            break
    source.replace(args.output)
    report={'nets':len(d.nets),'rectangles':engine.stats['final_rectangles'],'seconds':round(time.perf_counter()-started,3),
            'pin_mode':args.pin_mode,'engine':'ripup_reroute','version':'0.4','guide_rules':'official-Q100-Q110','thermal_weight':args.thermal_weight,
            'thermal_policy':args.thermal_policy,'passes':passes,'initial':initial,
            'final':engine.snapshot(),'statistics':dict(engine.stats),
            'tsv_positions_preserved':True,'official_evaluation':'NOT_RUN'}
    args.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    if stage.resolve().parent!=args.output.parent or stage.is_symlink():
        raise ValueError('Refusing to clean an unexpected staging path')
    shutil.rmtree(stage)
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
