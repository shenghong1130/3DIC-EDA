#!/usr/bin/env python3
"""Targeted rerouting with adaptive prices, layer alternatives and bounded 3D A*.

Inspired by recent routing research, not a reproduction of GPU/LLM frameworks.
TSV positions are frozen so thermal scores remain unchanged during this stage.
"""
import route_trace as _trace
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import tempfile
import time
from adaptive_router import AdaptiveRouter, merge_corridors
from calibrated_resources import CalibratedResources
from design import Design
from grid_router import GridRouter
from maze_router import MazeRouter
from refine import Refiner
from validate import check_net, read_guide
from guide_rules import select_tsv


class NegotiatedRefiner(Refiner):
    def __init__(self, design, maze_limit=300):
        adaptive=AdaptiveRouter(design)
        super().__init__(design,resources=adaptive.resources,router_override=adaptive)
        self.fixed=GridRouter(design)
        self.maze=MazeRouter(design,self.r)
        self.maze_limit=maze_limit
        self.history={}

    def counts(self,rects,points,name=None):
        cost,used=self.r.candidate(rects,0)
        counts=Counter(dict.fromkeys(used-self.r.pin_cells(name),1))
        center=None
        if len({(p.layer-1)//10 for p in points})==2:
            box=select_tsv(rects,[s for pin in self.d.net_pin_shapes(name) for s in pin])
            center=((box[0]+box[2])/2,(box[1]+box[3])/2)
            col,row=self.router.grid.cell(*center)
            for layer in (10,11):counts[layer*self.r.cells+row*self.r.cols+col]+=1
        return cost,counts,center

    def objective(self,cost,penalty):
        return 45*min(1,self.reference/max(cost,1e-9))+30/(1+max(0,penalty))**.5

    @_trace.observe_choice
    def reroute(self,name,rects):
        points=self.d.points(name)
        old_cost,old_counts,center=self.counts(rects,points,name)
        self.update(old_counts,-1)
        self.increment_cache={}
        base_penalty=max(0,self.penalty-self.increment(old_counts))
        base_cost=self.route_cost-old_cost
        best=(self.objective(self.route_cost,self.penalty),rects,old_cost,old_counts,self.penalty)
        seam={key for key in old_counts if key//self.r.cells in (10,11)}
        self.router.edge_options_cache={}
        self.router.exempt=self.r.pin_cells(name)
        self.router.net_name=name
        self.fixed.net_name=name
        self.maze.exempt=self.router.exempt
        self.maze.history=self.history

        def consider(candidate):
            nonlocal best
            candidate=merge_corridors(candidate,self.d.layers)
            try:cost,counts,actual=self.counts(candidate,points,name)
            except ValueError:return
            # Preserve all interface-layer demands, not only the TSV centroid.
            if actual!=center or {key for key in counts if key//self.r.cells in (10,11)}!=seam:return
            if any(counts[key]!=old_counts[key] for key in seam):return
            penalty=base_penalty+self.increment(counts)
            if penalty>self.penalty+1e-9:return
            objective=self.objective(base_cost+cost,penalty)
            if objective<=best[0]+1e-9:return
            try:check_net(self.d,name,candidate)
            except ValueError:return
            best=objective,candidate,cost,counts,penalty

        for weight in (8,24,64):
            self.router.congestion_weight=weight
            try:consider(self.router._build(points,center))
            except ValueError:pass
        for shift in range(8):
            self.fixed.layer_shift=shift
            for pattern in (('hv','vh') if shift%2 else ('hv',)):
                self.fixed.path_pattern=pattern
                try:consider(self.fixed._build(points,center))
                except ValueError:pass
        if self.stats['maze_attempted']<self.maze_limit and len(points)<=6:
            self.stats['maze_attempted']+=1
            try:
                consider(self.maze._build(points,center))
                self.stats['maze_completed']+=1
            except ValueError:pass
        _,chosen,cost,counts,penalty=best
        self.update(counts,1)
        self.increment_cache=None
        self.router.edge_options_cache=None
        self.route_cost=base_cost+cost
        self.penalty=penalty
        self.stats['attempted']+=1
        self.stats['accepted']+=chosen!=rects
        return chosen

    def run_pass(self,source,destination,limit):
        for key,amount in self.r.usage.items():
            cap=self.r.capacity(key)
            if cap and amount>cap:
                self.history[key]=min(100,self.history.get(key,0)+(amount-cap)/cap)
        selected=self.targets(source,limit)
        before=self.snapshot()
        start_accepted=self.stats['accepted']
        pending=[]
        for name,rects in read_guide(source):
            if name not in selected:continue
            _,counts,_=self.counts(rects,self.d.points(name),name)
            risk=math.fsum(self.phi(key,self.r.usage.get(key,0))-self.phi(key,max(0,self.r.usage.get(key,0)-count))
                          for key,count in counts.items() if key//self.r.cells not in (10,11))
            pending.append((risk,name,rects))
        print('Priority reroute targets',len(pending),flush=True)
        replacements={}
        for i,(_,name,rects) in enumerate(sorted(pending,key=lambda v:(-v[0],v[1])),1):
            replacements[name]=self.reroute(name,rects)
            if i%500==0:print('rerouted',i,dict(self.stats),flush=True)
        rectangles=0
        with Path(destination).open('w',encoding='utf-8',newline='\n') as stream:
            for name,rects in read_guide(source):
                rects=replacements.get(name,rects)
                stream.write(name+'\n(\n')
                for a,b,c,d,layer in rects:stream.write(f'{a} {b} {c} {d} {self.d.layers[layer]["name"]}\n')
                stream.write(')\n');rectangles+=len(rects)
        recomputed=math.fsum(self.phi(key,amount) for key,amount in self.r.usage.items())
        if abs(recomputed-self.penalty)>1e-6*max(1,recomputed):raise ValueError('Congestion drift')
        self.penalty=recomputed
        self.stats['final_rectangles']=rectangles
        after=self.snapshot()
        if after['wire_congestion_objective_PROXY']<before['wire_congestion_objective_PROXY']-1e-7:
            raise ValueError('Reroute objective regressed')
        return dict(before=before,after=after,accepted=self.stats['accepted']-start_accepted,targets=len(pending))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--def',dest='def_file',type=Path,required=True)
    p.add_argument('--lef',dest='lef_file',type=Path,required=True)
    p.add_argument('--seed-guide',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--passes',type=int,default=1)
    p.add_argument('--max-reroutes',type=int,default=12000)
    p.add_argument('--maze-limit',type=int,default=300)
    a=p.parse_args()
    if a.output.exists():p.error('Output exists')
    if a.passes<1 or a.max_reroutes<1 or a.maze_limit<0:p.error('Invalid search budget')
    start=time.perf_counter()
    d=Design(a.def_file,a.lef_file)
    engine=NegotiatedRefiner(d,a.maze_limit)
    initial=engine.initialize(a.seed_guide)
    print('Initialized',json.dumps(initial),flush=True)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    source=a.seed_guide
    passes=[]
    for i in range(a.passes):
        target=a.output if i==a.passes-1 else a.output.with_suffix(f'.pass{i+1}.guide')
        result=engine.run_pass(source,target,a.max_reroutes)
        passes.append(result);source=target
        print('Pass',i+1,json.dumps(result),flush=True)
    report=dict(version='0.5-negotiated',pin_mode='physical',seconds=time.perf_counter()-start,
        initial=initial,final=engine.snapshot(),passes=passes,stats=dict(engine.stats),
        official_evaluation='NOT_RUN',thermal='unchanged: all M10/M11 demands and actual TSV positions frozen')
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
