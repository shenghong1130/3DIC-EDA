#!/usr/bin/env python3
"""Refine TSV positions using incremental global wire/congestion/thermal costs.

The interface occupancy price is conservative; it is not an official scorer.
Only source LEF/DEF/thermal and a self-generated seed GUIDE are consumed.
"""
import argparse
from bisect import bisect_right
from collections import Counter
import json
import math
import heapq
from pathlib import Path
import time
from adaptive_router import AdaptiveRouter
from design import Design
from guide_rules import select_tsv
from router import tree_edges
from routing_thermal import ThermalPreference
from validate import read_guide, check_net


class ThermalMoments:
    def __init__(self, heat, grid, counts, gain=8):
        self.heat, self.grid, self.gain = heat, grid, gain
        self.ambient=getattr(heat,'ambient',0.0)
        total = squares = 0.0
        for i, row in enumerate(heat.sample_rows):
            height = (heat.sample_rows[i+1] if i+1<len(heat.sample_rows) else heat.rows)-(row if i else 0)
            for j, col in enumerate(heat.sample_cols):
                width = (heat.sample_cols[j+1] if j+1<len(heat.sample_cols) else heat.cols)-(col if j else 0)
                value = max(0.0,heat.temperatures.get((row, col), heat.min_temp)-self.ambient)
                total += value*width*height
                squares += value*value*width*height
        cells = heat.rows*heat.cols
        self.mean = total/cells
        self.norm = math.sqrt(max(0, squares-total*total/cells))
        self.count = sum(counts.values())
        self.squares = sum(n*n for n in counts.values())
        self.covariance = math.fsum(self.value(cell)*n for cell,n in counts.items())
        self.centering = self.count*self.count/cells

    def value(self, cell):
        return max(0.0,self.heat.temperature(*self.grid.center(*cell))-self.ambient)-self.mean

    def score(self, covariance=None, squares=None):
        covariance = self.covariance if covariance is None else covariance
        squares = self.squares if squares is None else squares
        denom = self.norm*math.sqrt(max(0, squares-self.centering))
        correlation = covariance/denom if denom>1e-15 else 0
        return 100*(0.5+0.5*math.tanh(self.gain*correlation))


class Relocator:
    def __init__(self, design, thermal, gain=8, candidate_limit=2, global_thermal=False, max_reroutes=8000, full_tsv_price=False):
        self.d = design
        self.router = AdaptiveRouter(design, thermal)
        self.router.pair_limit = 3
        self.router.tsv_pin_credit = False
        self.router.global_thermal_candidates = global_thermal
        self.global_thermal = global_thermal
        self.full_tsv_price=full_tsv_price
        self.router.full_tsv_price=full_tsv_price
        self.max_reroutes = max_reroutes
        self.targets = []
        self.cross_records=[]
        self.candidate_limit = candidate_limit
        self.r = self.router.resources
        self.gain = gain
        self.wire = self.penalty = self.reference = 0.0
        self.stats = Counter()

    def counts(self, name, rects):
        wire, used = self.r.candidate(rects, 0)
        exempt = self.r.pin_cells(name)
        counts = Counter({key: 1 for key in used-exempt})
        points = self.d.points(name)
        cell = None
        if len({(p.layer-1)//10 for p in points}) == 2:
            box = select_tsv(rects, [s for pin in self.d.net_pin_shapes(name) for s in pin])
            cell = self.router.grid.cell((box[0]+box[2])/2, (box[1]+box[3])/2)
            for layer in (10, 11):
                key = layer*self.r.cells+cell[1]*self.r.cols+cell[0]
                # Price a TSV landing as a full interface track bundle. This
                # encourages safe spacing; observed official M11 counts differ.
                counts[key] += self.r.capacity(key) if layer == 11 else 1
        return wire, counts, cell

    def phi(self, key, amount):
        cap = self.r.capacity(key)
        return (0.1*max(0,amount-cap)/cap)**2 if cap else (10000 if amount else 0)

    def update(self, counts, sign):
        change = []
        for key, count in counts.items():
            old = self.r.usage.get(key, 0)
            new = old+sign*count
            if new < 0:
                raise ValueError('Negative resource demand')
            change.append(self.phi(key,new)-self.phi(key,old))
            self.r.usage[key] = new
        return math.fsum(change)

    def initialize(self, guide):
        for name, rects in read_guide(guide):
            wire, counts, cell = self.counts(name, rects)
            self.wire += wire
            self.update(counts, 1)
            if cell is not None:
                self.router.tsv_usage[cell] += 1
                self.cross_records.append((name,cell))
                if self.global_thermal:
                    item = (-self.router.heat.temperature(*self.router.grid.center(*cell)), name)
                    if len(self.targets)<self.max_reroutes:
                        heapq.heappush(self.targets,item)
                    elif item>self.targets[0]:
                        heapq.heapreplace(self.targets,item)
            pts = self.d.points(name)
            edges = tree_edges(pts)
            planar = sum(abs(pts[i].x-pts[j].x)+abs(pts[i].y-pts[j].y) for i,j in edges)/self.r.step
            bends = sum(pts[i].x != pts[j].x and pts[i].y != pts[j].y for i,j in edges)
            self.reference += planar+3*(bends+10*(cell is not None))+6*(cell is not None)
        self.penalty = math.fsum(self.phi(key, amount) for key, amount in self.r.usage.items())
        self.thermal = ThermalMoments(self.router.heat, self.router.grid, self.router.tsv_usage, self.gain)
        if self.global_thermal and self.full_tsv_price:
            self.targets=[]
            for name,cell in self.cross_records:
                key=11*self.r.cells+cell[1]*self.r.cols+cell[0]
                amount,cap=self.r.usage.get(key,0),self.r.capacity(key)
                risk=self.phi(key,amount)-self.phi(key,max(0,amount-cap))
                heat=(self.router.heat.temperature(*self.router.grid.center(*cell))-self.router.heat.min_temp)/max(self.router.heat.range,1e-9)
                item=(risk+0.0001*(1-heat),name)
                if len(self.targets)<self.max_reroutes:heapq.heappush(self.targets,item)
                elif item>self.targets[0]:heapq.heapreplace(self.targets,item)
        self.targets = {name for _,name in self.targets}

    def objective(self, wire, penalty, thermal):
        return 45*min(1,self.reference/max(wire,1e-9))+30/math.sqrt(1+max(0,penalty))+0.25*thermal

    def snapshot(self):
        return dict(route_cost_dbu=self.wire*self.r.step, congestion_penalty_HEURISTIC=self.penalty,
                    thermal_estimate=self.thermal.score(), objective_HEURISTIC=self.objective(self.wire,self.penalty,self.thermal.score()))

    def reroute(self, name, old):
        if self.global_thermal and name not in self.targets:
            return old
        old_wire, old_counts, old_cell = self.counts(name, old)
        if old_cell is None:
            return old
        old_score = self.objective(self.wire, self.penalty, self.thermal.score())
        removed = self.update(old_counts, -1)
        self.router.tsv_usage[old_cell] -= 1
        self.router.net_name = name
        self.router.exempt = self.r.pin_cells(name)
        points = self.d.points(name)
        pins = [s for pin in self.d.net_pin_shapes(name) for s in pin]
        # Evaluate several thermal/geometry/occupancy alternatives, not only the
        # first greedy choice. Candidates share the same removed-net state.
        ranked = self.router.candidate_centers(points)
        if self.global_thermal:
            old_center = self.router.grid.center(*old_cell)
            geometry_old = sum(abs(p.x-old_center[0])+abs(p.y-old_center[1]) for p in points)
            old_site={ln*self.r.cells+old_cell[1]*self.r.cols+old_cell[0] for ln in (10,11)}
            regular_increment=math.fsum(self.phi(key,self.r.usage.get(key,0)+count)-self.phi(key,self.r.usage.get(key,0))
                for key,count in old_counts.items() if key not in old_site)
            def estimate(center):
                cell = self.router.grid.cell(*center)
                if cell == old_cell:
                    covariance, squares = self.thermal.covariance, self.thermal.squares
                else:
                    a,b=self.router.tsv_usage[old_cell]+1,self.router.tsv_usage[cell]
                    covariance=self.thermal.covariance+self.thermal.value(cell)-self.thermal.value(old_cell)
                    squares=self.thermal.squares+2*(b-a+1)
                geometry_new=sum(abs(p.x-center[0])+abs(p.y-center[1]) for p in points)
                wire=max(1,self.wire+(geometry_new-geometry_old)/self.r.step)
                penalty=self.penalty
                if self.full_tsv_price:
                    landing=0.0
                    for ln in (10,11):
                        key=ln*self.r.cells+cell[1]*self.r.cols+cell[0]
                        count=(self.r.capacity(key) if ln==11 else 1)+(0 if key in self.router.exempt else 1)
                        amount=self.r.usage.get(key,0)
                        landing+=self.phi(key,amount+count)-self.phi(key,amount)
                    penalty=max(0,self.penalty+removed+regular_increment+landing)
                # Compare global thermal gain against the estimated added wire;
                # the exact candidate is still checked before acceptance below.
                return -self.objective(wire,penalty,self.thermal.score(covariance,squares)),center
            ranked=sorted(ranked,key=estimate)
        best = None
        centers = ranked[:self.candidate_limit]
        for center in centers:
            try:
                from adaptive_router import merge_corridors
                rects = merge_corridors(self.router._build(points, center), self.d.layers)
                wire, counts, cell = self.counts(name, rects)
                if cell != self.router.grid.cell(*center):
                    continue
            except ValueError:
                continue
            delta = math.fsum(self.phi(key,self.r.usage.get(key,0)+count)-self.phi(key,self.r.usage.get(key,0)) for key,count in counts.items())
            if cell == old_cell:
                covariance, squares = self.thermal.covariance, self.thermal.squares
            else:
                a = self.router.tsv_usage[old_cell]+1
                b = self.router.tsv_usage[cell]
                covariance = self.thermal.covariance+self.thermal.value(cell)-self.thermal.value(old_cell)
                squares = self.thermal.squares+2*(b-a+1)
            penalty = max(0,self.penalty+removed+delta)
            score = self.objective(self.wire-old_wire+wire, penalty, self.thermal.score(covariance,squares))
            if score > old_score+1e-9 and (best is None or score > best[0]):
                try:
                    check_net(self.d,name,rects)
                except ValueError:
                    continue
                best = score, rects, wire, counts, cell, penalty, covariance, squares
        self.stats['attempted'] += 1
        if best is None:
            self.update(old_counts, 1)
            self.router.tsv_usage[old_cell] += 1
            return old
        _, rects, wire, counts, cell, penalty, covariance, squares = best
        self.update(counts, 1)
        self.router.tsv_usage[cell] += 1
        self.wire += wire-old_wire
        self.penalty = penalty
        self.thermal.covariance, self.thermal.squares = covariance, squares
        self.stats['accepted'] += 1
        self.stats['moved_tsv'] += cell != old_cell
        return rects


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--def',dest='def_file',type=Path,required=True)
    p.add_argument('--lef',dest='lef_file',type=Path,required=True)
    p.add_argument('--thermal-report',type=Path,required=True)
    p.add_argument('--seed-guide',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--thermal-gain',type=float,default=8)
    p.add_argument('--candidate-limit',type=int,default=2)
    p.add_argument('--global-thermal',action='store_true')
    p.add_argument('--max-reroutes',type=int,default=8000)
    p.add_argument('--full-tsv-price',action='store_true')
    a=p.parse_args()
    if a.output.exists():
        p.error('Output already exists')
    start=time.perf_counter()
    d=Design(a.def_file,a.lef_file)
    heat=ThermalPreference(d,a.thermal_report,2,'region')
    if a.candidate_limit < 1:
        p.error('candidate-limit must be positive')
    if a.max_reroutes < 1:
        p.error('max-reroutes must be positive')
    engine=Relocator(d,heat,a.thermal_gain,a.candidate_limit,a.global_thermal,a.max_reroutes,a.full_tsv_price)
    engine.initialize(a.seed_guide)
    initial=engine.snapshot()
    print('Initialized',json.dumps(initial),flush=True)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    counts=Counter()
    temp=a.output.with_suffix('.tmp')
    with temp.open('w',encoding='utf-8',newline='\n') as stream:
        for name,rects in read_guide(a.seed_guide):
            rects=engine.reroute(name,rects)
            stream.write(name+'\n(\n')
            for x,y,xx,yy,layer in rects:
                stream.write(f'{x} {y} {xx} {yy} {d.layers[layer]["name"]}\n')
            stream.write(')\n')
            counts.update(nets=1,rectangles=len(rects))
            if counts['nets']%10000==0:
                print('processed',counts['nets'],dict(engine.stats),flush=True)
    temp.replace(a.output)
    recomputed=math.fsum(engine.phi(key,amount) for key,amount in engine.r.usage.items())
    if abs(recomputed-engine.penalty)>1e-7*max(1,recomputed):
        raise ValueError('Resource penalty drift')
    moments=ThermalMoments(heat,engine.router.grid,engine.router.tsv_usage,a.thermal_gain)
    if abs(moments.score()-engine.thermal.score())>1e-6:
        raise ValueError('Thermal moments drift')
    report=dict(counts,version='0.5-relocated',pin_mode='physical',seconds=time.perf_counter()-start,
                initial=initial,final=engine.snapshot(),stats=dict(engine.stats),thermal_gain=a.thermal_gain,
                global_thermal=a.global_thermal,max_reroutes=a.max_reroutes,full_tsv_price=a.full_tsv_price,official_evaluation='NOT_RUN')
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
