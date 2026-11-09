#!/usr/bin/env python3
"""Input-only routing with per-edge layer selection and thermal TSV relocation."""
import route_trace as _trace
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import time
from bisect import bisect_left, bisect_right

from design import Design, Point
from grid_router import GridRouter
from guide_rules import select_tsv
from routing_thermal import ThermalPreference
from calibrated_resources import CalibratedResources
from validate import check_net


def merge_corridors(rects, layers):
    """Union collinear full-cell corridors; preserve the exact layer geometry."""
    groups = defaultdict(list)
    for x1, y1, x2, y2, layer in rects:
        horizontal = layers[layer]['direction'] == 'HORIZONTAL'
        key = (layer, y1, y2) if horizontal else (layer, x1, x2)
        groups[key].append((x1, x2) if horizontal else (y1, y2))
    result = []
    for (layer, lo, hi), intervals in groups.items():
        start = end = None
        for a, b in sorted(intervals):
            if end is not None and a <= end:
                end = max(end, b)
            else:
                if end is not None:
                    result.append((start, lo, end, hi, layer) if layers[layer]['direction'] == 'HORIZONTAL' else (lo, start, hi, end, layer))
                start, end = a, b
        if end is not None:
            result.append((start, lo, end, hi, layer) if layers[layer]['direction'] == 'HORIZONTAL' else (lo, start, hi, end, layer))
    return sorted(result, key=lambda r: (r[4], r[0], r[1], r[2], r[3]))


class AdaptiveRouter(GridRouter):
    def __init__(self, design, thermal=None, congestion_weight=12, thermal_weight=2):
        super().__init__(design)
        self.resources = CalibratedResources(design, 'physical', True)
        self.heat = thermal
        self.congestion_weight = congestion_weight
        self.thermal_weight = thermal_weight
        self.exempt = set()
        self.tsv_usage = Counter()
        self.stats = Counter()
        self.edge_options_cache = None
        self.pair_limit = None
        self.tsv_pin_credit = True
        self.global_thermal_candidates = False
        self.full_tsv_price = False
        self.pairs = {}
        for tier in (0, 1):
            available = [n for n in design.layers if (1 <= n <= 9 if tier == 0 else 12 <= n <= 20)]
            self.pairs[tier] = [(h, v) for h in available for v in available if abs(h-v) == 1
                and design.layers[h]['direction'] == 'HORIZONTAL' and design.layers[v]['direction'] == 'VERTICAL']

    def pressure(self, used, pin_credit=True):
        r = self.resources
        pressure = 0.0
        for key in (used-self.exempt if pin_credit else used):
            amount, cap = r.usage.get(key, 0), r.capacity(key)
            if cap <= 0:
                pressure += 10000
                continue
            before, after = amount/cap, (amount+1)/cap
            pressure += after**4-before**4+20*(max(0, after-1)**2-max(0, before-1)**2)
        return pressure

    def connect(self, rects, p, q, pair):
        if p == q:
            rects.add(self.rect(p.x, p.y, p.x, p.y, p.layer))
            return
        tier = (p.layer-1)//10
        pairs = self.pairs[tier]
        # Short local nets need nearby access layers; long nets may use all pairs.
        distance = abs(p.x-q.x)+abs(p.y-q.y)
        if distance < 12*self.resources.step or self.pair_limit is not None:
            pairs = sorted(pairs, key=lambda pair: min(abs(p.layer-pair[0])+abs(q.layer-pair[1]),
                abs(p.layer-pair[1])+abs(q.layer-pair[0])))[:(self.pair_limit or 4)]
        cache_key=(p,q,tuple(pairs))
        options=self.edge_options_cache.get(cache_key) if self.edge_options_cache is not None else None
        if options is None:
            options=[]
            for selected in pairs:
                for pattern in ('hv','vh'):
                    self.path_pattern=pattern
                    candidate=set()
                    _trace.option_begin(self)
                    GridRouter.connect(self,candidate,p,q,selected)
                    candidate=merge_corridors(candidate,self.d.layers)
                    _trace.option_end(self, candidate)
                    wire,used=self.resources.candidate(candidate,0)
                    options.append((wire,self.pressure(used),selected,pattern,candidate))
            if self.edge_options_cache is not None:self.edge_options_cache[cache_key]=options
        best=None
        for wire,pressure,selected,pattern,candidate in options:
            score=wire+self.congestion_weight*pressure
            key=(score,wire,selected,pattern)
            if best is None or key<best[0]:best=(key,candidate)
        rects.update(best[1])
        _trace.option_selected(self, best[1])
        self.stats['edges'] += 1
        self.stats['pair_'+str(best[0][2])] += 1

    def candidate_centers(self, points):
        from tsv_obstacles import obstacles_for
        obstacles=obstacles_for(self.d)
        x, y = statistics.median(p.x for p in points), statistics.median(p.y for p in points)
        col, row = self.grid.cell(x, y)
        centers = {self.grid.center(max(0, min(self.resources.cols-1, col+dc)),
                                  max(0, min(self.resources.rows-1, row+dr)))
                   for dc, dr in ((0,0),(-1,0),(1,0),(0,-1),(0,1),(-2,0),(2,0),(0,-2),(0,2),
                                  (-2,-2),(-2,2),(2,-2),(2,2),(-4,0),(4,0),(0,-4),(0,4))}
        centers.update(self.grid.point_center(p.x, p.y) for p in points if p.layer in (10, 11))
        if self.full_tsv_price:
            free=[]
            for radius in range(1,21):
                for dc in range(-radius,radius+1):
                    dy=radius-abs(dc)
                    for dr in ({dy,-dy} if dy else {0}):
                        nc,nr=col+dc,row+dr
                        if not (0<=nc<self.resources.cols and 0<=nr<self.resources.rows) or self.tsv_usage[nc,nr]:continue
                        key=11*self.resources.cells+nr*self.resources.cols+nc
                        if (self.resources.usage.get(key,0)<self.resources.capacity(key)
                                and not obstacles.conflicts(self.grid.center(nc,nr))):free.append((nc,nr))
                if len(free)>=4:break
            centers.update(self.grid.center(*cell) for cell in free[:8])
        if self.heat is not None:
            centers.update(self.grid.point_center(*xy) for xy in self.heat.candidates(points))
            # Include the nearest point in neighboring temperature regions,
            # allowing controlled movement beyond the terminal bounding box.
            heat = self.heat
            ci = max(0, bisect_right(heat.sample_cols, col)-1)
            ri = max(0, bisect_right(heat.sample_rows, row)-1)
            for a in range(max(0, ci-1), min(len(heat.sample_cols), ci+2)):
                for b in range(max(0, ri-1), min(len(heat.sample_rows), ri+2)):
                    left, bottom = heat.sample_cols[a], heat.sample_rows[b]
                    right = heat.sample_cols[a+1]-1 if a+1<len(heat.sample_cols) else self.resources.cols-1
                    top = heat.sample_rows[b+1]-1 if b+1<len(heat.sample_rows) else self.resources.rows-1
                    cc, rr = max(left, min(right, col)), max(bottom, min(top, row))
                    for dc, dr in ((0,0),(1,0),(0,1),(1,1)):
                        centers.add(self.grid.center(min(right, cc+dc), min(top, rr+dr)))
            if self.global_thermal_candidates:
                # The global objective decides whether the thermal gain pays
                # for a long connection. No preselected routes are supplied.
                hot = sorted(heat.hot, key=lambda p: abs(p[0]-x)+abs(p[1]-y))[:8]+heat.hot[:8]
                for sx, sy in hot:
                    rr, cc = heat.cell(sx, sy)
                    ci, ri = bisect_right(heat.sample_cols,cc)-1, bisect_right(heat.sample_rows,rr)-1
                    right = heat.sample_cols[ci+1]-1 if ci+1<len(heat.sample_cols) else self.resources.cols-1
                    top = heat.sample_rows[ri+1]-1 if ri+1<len(heat.sample_rows) else self.resources.rows-1
                    c0, r0 = max(cc,min(right,col)), max(rr,min(top,row))
                    for dc,dr in ((0,0),(-1,0),(1,0),(0,-1),(0,1)):
                        centers.add(self.grid.center(max(cc,min(right,c0+dc)),max(rr,min(top,r0+dr))))
                    # Many cold nets see the same nearest corner of a hot
                    # region. Search for free landing cells inside that region
                    # rather than repeatedly proposing its occupied corner.
                    free = []
                    for radius in range(min(40, (right-cc)+(top-rr)+1)):
                        for dc in range(-radius,radius+1):
                            dy=radius-abs(dc)
                            for dr in ({dy,-dy} if dy else {0}):
                                nc,nr=c0+dc,r0+dr
                                if not (cc<=nc<=right and rr<=nr<=top) or self.tsv_usage[nc,nr]:
                                    continue
                                key=11*self.resources.cells+nr*self.resources.cols+nc
                                if (self.resources.usage.get(key,0)<self.resources.capacity(key)
                                        and not obstacles.conflicts(self.grid.center(nc,nr))):
                                    free.append((nc,nr))
                        if len(free)>=2:
                            break
                    centers.update(self.grid.center(*cell) for cell in free[:4])
        geometry = {xy: sum(abs(p.x-xy[0])+abs(p.y-xy[1]) for p in points) for xy in centers}
        scale = max(min(geometry.values()), 12*self.resources.step)
        def key(xy):
            c, r = self.grid.cell(*xy)
            heat = (self.heat.temperature(*xy)-self.heat.min_temp)/max(self.heat.range, 1e-9) if self.heat else 0
            keys = {ln*self.resources.cells+r*self.resources.cols+c for ln in (10,11)}
            # Avoid both fixed obstacles and multiple TSVs competing for one cell.
            pressure = self.pressure(keys, pin_credit=self.tsv_pin_credit)
            if self.full_tsv_price:
                pressure=0.0
                for k in keys:
                    amount,cap=self.resources.usage.get(k,0),self.resources.capacity(k)
                    count=(cap if k//self.resources.cells==11 else 1)+(0 if k in self.exempt else 1)
                    if cap:
                        before,after=amount/cap,(amount+count)/cap
                        pressure+=after**4-before**4+20*(max(0,after-1)**2-max(0,before-1)**2)
                    else:pressure+=10000
            length=geometry.get(xy)
            if length is None:length=sum(abs(p.x-xy[0])+abs(p.y-xy[1]) for p in points)
            return (length/scale-self.thermal_weight*heat+0.6*pressure+0.8*self.tsv_usage[c,r], length, xy)
        from tsv_obstacles import obstacles_for
        obstacles=obstacles_for(self.d)
        centers={xy for xy in centers if not obstacles.conflicts(xy)}
        # Geometry and thermal proposals can all be occupied. Expand around
        # the terminal median until there are empty landing alternatives.
        if len(centers)<4:
            for radius in range(1,max(self.resources.cols,self.resources.rows)):
                for dc in range(-radius,radius+1):
                    dy=radius-abs(dc)
                    for dr in ({dy,-dy} if dy else {0}):
                        nc,nr=col+dc,row+dr
                        if 0<=nc<self.resources.cols and 0<=nr<self.resources.rows:
                            xy=self.grid.center(nc,nr)
                            if not obstacles.conflicts(xy):centers.add(xy)
                if len(centers)>=4:break
        return sorted(centers, key=key)

    @_trace.observe_choice
    def route_net(self, points):
        self.exempt = self.resources.pin_cells(self.net_name)
        crossing = len({(p.layer-1)//10 for p in points}) == 2
        if crossing:
            pins = [shape for pin in self.d.net_pin_shapes(self.net_name) for shape in pin]
            for center in self.candidate_centers(points):
                rects = merge_corridors(self._build(points, center), self.d.layers)
                try:
                    box = select_tsv(rects, pins)
                    actual = ((box[0]+box[2])/2, (box[1]+box[3])/2)
                    if actual != center:
                        continue
                    check_net(self.d, self.net_name, rects)
                except ValueError:
                    self.stats['rejected_tsv_candidates'] += 1
                    continue
                self.last_tsv_center = center
                break
            else:
                raise ValueError('No valid adaptive TSV candidate: '+self.net_name)
        else:
            rects = merge_corridors(self._build(points, None), self.d.layers)
            check_net(self.d, self.net_name, rects)
        _, used = self.resources.candidate(rects, 0)
        for key in used-self.exempt:
            self.resources.usage[key] = self.resources.usage.get(key, 0)+1
        if crossing:
            c, r = self.grid.cell(*self.last_tsv_center)
            self.tsv_usage[c,r] += 1
            for ln in (10,11):
                key = ln*self.resources.cells+r*self.resources.cols+c
                self.resources.usage[key] = self.resources.usage.get(key, 0)+1
        return rects, crossing


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--def', dest='def_file', type=Path, required=True)
    p.add_argument('--lef', dest='lef_file', type=Path, required=True)
    p.add_argument('--thermal-report', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--thermal-weight', type=float, default=2)
    p.add_argument('--congestion-weight', type=float, default=12)
    a = p.parse_args()
    if a.thermal_weight < 0 or a.congestion_weight < 0:
        p.error('weights must be nonnegative')
    started = time.perf_counter()
    d = Design(a.def_file, a.lef_file)
    thermal = ThermalPreference(d, a.thermal_report, a.thermal_weight, 'region') if a.thermal_weight else None
    router = AdaptiveRouter(d, thermal, a.congestion_weight, a.thermal_weight)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    if a.output.exists():
        p.error('Refusing to overwrite an existing GUIDE')
    temp = a.output.with_suffix('.tmp')
    totals = Counter()
    with temp.open('w', encoding='utf-8', newline='\n') as stream:
        for name in d.nets:
            router.net_name = name
            rects, crossing = router.route_net(d.points(name))
            stream.write(name+'\n(\n')
            for x1,y1,x2,y2,ln in rects:
                stream.write(f'{x1} {y1} {x2} {y2} {d.layers[ln]["name"]}\n')
            stream.write(')\n')
            totals.update(nets=1, rectangles=len(rects), cross_die_nets=int(crossing))
            if totals['nets']%10000 == 0:
                print('routed', totals['nets'], 'seconds', round(time.perf_counter()-started, 1), flush=True)
    temp.replace(a.output)
    _trace.alias_guide(temp, a.output)
    report = dict(totals, version='0.5-adaptive', pin_mode='physical',
                  seconds=time.perf_counter()-started, stats=dict(router.stats),
                  thermal_weight=a.thermal_weight, congestion_weight=a.congestion_weight,
                  resource_profile='physical-half-open-own-guide-credit', official_evaluation='NOT_RUN')
    a.output.with_suffix('.run.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
