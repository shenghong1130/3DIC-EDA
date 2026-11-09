#!/usr/bin/env python3
"""Deterministic two-tier Manhattan routing baseline; no reference GUIDE input."""
import route_trace as _trace
import argparse
from collections import Counter
from functools import lru_cache
import json
import math
from pathlib import Path
import statistics
import time
from design import Design, Point


@_trace.observe_topology
def tree_edges(points):
    """Exact Manhattan Prim for <=128 terminals; bounded-cost construction beyond that."""
    return list(_cached_tree_edges(tuple(points)))


@lru_cache(maxsize=64)
def _cached_tree_edges(points):
    n = len(points)
    if n <= 1:
        return ()
    if n > 128:
        from steiner_tree import sparse_mst_edges
        return tuple(sparse_mst_edges(points))
    used, distance, parent = [False]*n, [float('inf')]*n, [-1]*n
    distance[0] = 0
    edges = []
    for _ in range(n):
        u = min((i for i in range(n) if not used[i]), key=lambda i: (distance[i],i))
        used[u] = True
        if parent[u] >= 0:
            edges.append((parent[u], u))
        for v in range(n):
            d = abs(points[u].x-points[v].x)+abs(points[u].y-points[v].y)
            if not used[v] and d < distance[v]:
                distance[v], parent[v] = d, u
    return tuple(edges)


class Router:
    def __init__(self, design, layer_shift=0, layer_policy='fixed', congestion_weight=12.0, thermal_preference=None, path_pattern='hv'):
        self.d = design
        self.layer_shift = layer_shift
        self.layer_policy = layer_policy
        self.congestion_weight = congestion_weight
        self.thermal_preference = thermal_preference
        self.path_pattern = path_pattern
        self.tsv_override = None
        self.detour_steps = 0
        self.pair_cache = {}
        self.selected_layers = Counter()
        self.resources = None
        if layer_policy == 'balanced':
            from routing_resources import RoutingResources
            self.resources = RoutingResources(design)
        self.tsv_half = max(1, math.ceil(design.tsv_width_um*design.dbu/2))

    def pair(self, tier):
        key = (tier, self.layer_shift)
        if key in self.pair_cache:
            return self.pair_cache[key]
        # Keep normal signal routes away from the M10/M11 TSV interface.
        available = [n for n in self.d.layers if (1 <= n <= 9 if tier == 0 else 12 <= n <= 20)]
        pairs = [(h,v) for h in available for v in available
                 if self.d.layers[h]['direction'] == 'HORIZONTAL'
                 and self.d.layers[v]['direction'] == 'VERTICAL' and abs(h-v)==1]
        if not pairs:
            raise ValueError(f'No adjacent H/V routing pair in tier {tier}')
        result = sorted(pairs, key=lambda p:(max(p), min(p)))[self.layer_shift % len(pairs)]
        self.pair_cache[key] = result
        return result

    @_trace.observe_rect
    def rect(self, x1,y1,x2,y2,layer,half=None):
        if layer not in self.d.layers:
            raise ValueError(f'Undefined routing layer {layer}')
        if half is None:
            half = max(1, math.ceil(self.d.layers[layer]['width']*self.d.dbu/2))
        a,b,c,d = self.d.die
        r = (max(a,math.floor(min(x1,x2)-half)), max(b,math.floor(min(y1,y2)-half)),
             min(c,math.ceil(max(x1,x2)+half)), min(d,math.ceil(max(y1,y2)+half)),layer)
        if r[0] >= r[2] or r[1] >= r[3]:
            raise ValueError(f'Route lies outside die: {r}')
        return r

    @_trace.observe_stack
    def stack(self, rects,x,y,lo,hi):
        for layer in range(min(lo,hi),max(lo,hi)+1):
            rects.add(self.rect(x,y,x,y,layer))

    def connect(self, rects, p, q, pair):
        if self.path_pattern == 'vh':
            p, q = q, p
        h,v = pair
        if self.path_pattern in ('zx','zy'):
            if self.path_pattern == 'zy':
                mid=(p.y+q.y)/2+self.detour_steps*self.d.grid['Y'][0][2]
                if p.y==q.y and not self.detour_steps:
                    mid+=2*self.d.grid['Y'][0][2]*(1 if p.y<(self.d.die[1]+self.d.die[3])/2 else -1)
                mid=max(self.d.die[1]+1,min(self.d.die[3]-1,mid))
                self.stack(rects,p.x,p.y,p.layer,v)
                rects.add(self.rect(p.x,p.y,p.x,mid,v))
                self.stack(rects,p.x,mid,v,h)
                rects.add(self.rect(p.x,mid,q.x,mid,h))
                self.stack(rects,q.x,mid,h,v)
                rects.add(self.rect(q.x,mid,q.x,q.y,v))
                self.stack(rects,q.x,q.y,v,q.layer)
            else:
                mid=(p.x+q.x)/2+self.detour_steps*self.d.grid['X'][0][2]
                if p.x==q.x and not self.detour_steps:
                    mid+=2*self.d.grid['X'][0][2]*(1 if p.x<(self.d.die[0]+self.d.die[2])/2 else -1)
                mid=max(self.d.die[0]+1,min(self.d.die[2]-1,mid))
                self.stack(rects,p.x,p.y,p.layer,h)
                rects.add(self.rect(p.x,p.y,mid,p.y,h))
                self.stack(rects,mid,p.y,h,v)
                rects.add(self.rect(mid,p.y,mid,q.y,v))
                self.stack(rects,mid,q.y,v,h)
                rects.add(self.rect(mid,q.y,q.x,q.y,h))
                self.stack(rects,q.x,q.y,h,q.layer)
            return
        if abs(p.y-q.y) < 1e-8:
            self.stack(rects,p.x,p.y,p.layer,h)
            rects.add(self.rect(p.x,p.y,q.x,q.y,h))
            self.stack(rects,q.x,q.y,h,q.layer)
        elif abs(p.x-q.x) < 1e-8:
            self.stack(rects,p.x,p.y,p.layer,v)
            rects.add(self.rect(p.x,p.y,q.x,q.y,v))
            self.stack(rects,q.x,q.y,v,q.layer)
        else:
            self.stack(rects,p.x,p.y,p.layer,h)
            rects.add(self.rect(p.x,p.y,q.x,p.y,h))
            self.stack(rects,q.x,p.y,h,v)
            rects.add(self.rect(q.x,p.y,q.x,q.y,v))
            self.stack(rects,q.x,q.y,v,q.layer)

    @_trace.observe_choice
    def route_net(self, points):
        if self.layer_policy == 'fixed':
            result = self._route_net_fixed(points)
            if result[1] and self.thermal_preference is not None:
                self.thermal_preference.commit(self.last_tsv_center)
            return result
        original_shift = self.layer_shift
        best = None
        try:
            for offset in (0, 2, 4):
                self.layer_shift = original_shift + offset
                rects, crossing = self._route_net_fixed(points)
                cost, used = self.resources.candidate(rects, self.congestion_weight)
                if best is None or cost < best[0]:
                    best = (cost, rects, crossing, used, self.layer_shift)
        finally:
            self.layer_shift = original_shift
        _, rects, crossing, used, shift = best
        self.resources.commit(used, rects, crossing)
        if crossing and self.thermal_preference is not None:
            self.thermal_preference.commit(self.last_tsv_center)
        self.selected_layers[shift] += 1
        return rects, crossing

    @_trace.observe_build
    def _route_net_fixed(self, points):
        rects = set()
        groups = [[p for p in points if (p.layer-1)//10 == tier] for tier in (0,1)]
        crossing = bool(groups[0] and groups[1])
        if crossing:
            # Prefer vertically aligned interface pins when available, otherwise median XY.
            lower = {(p.x,p.y) for p in points if p.layer == 10}
            upper = {(p.x,p.y) for p in points if p.layer == 11}
            candidates = sorted(lower & upper)
            if candidates:
                x,y = min(candidates, key=lambda xy:sum(abs(p.x-xy[0])+abs(p.y-xy[1]) for p in points))
            else:
                x,y = statistics.median(p.x for p in points), statistics.median(p.y for p in points)
            a,b,c,d = self.d.die
            x,y = max(a+self.tsv_half,min(c-self.tsv_half,x)), max(b+self.tsv_half,min(d-self.tsv_half,y))
            if not (a <= x-self.tsv_half < x+self.tsv_half <= c and b <= y-self.tsv_half < y+self.tsv_half <= d):
                raise ValueError('TSV enclosure does not fit inside DIEAREA')
            # A landing pad partly overlapping another M10/M11 pin-access box can
            # create a second, ambiguous TSV candidate. Try nearby legal centers.
            interface = [p for p in points if p.layer in (10,11)]
            pin_boxes = [self.rect(p.x,p.y,p.x,p.y,p.layer) for p in interface]
            centers = [(x,y)] + [(p.x,p.y) for p in interface[:32]]
            centers += [(x+dx*self.tsv_half*2,y+dy*self.tsv_half*2)
                        for dx in (-2,-1,0,1,2) for dy in (-2,-1,0,1,2)]
            if self.thermal_preference is not None:
                centers += self.thermal_preference.candidates(points)
            legal_centers = []
            for cx,cy in dict.fromkeys(centers):
                if not (a <= cx-self.tsv_half and cx+self.tsv_half <= c and b <= cy-self.tsv_half and cy+self.tsv_half <= d):
                    continue
                box = self.rect(cx,cy,cx,cy,10,self.tsv_half)
                partial = any(max(box[0],r[0])<min(box[2],r[2]) and max(box[1],r[1])<min(box[3],r[3])
                              and not (box[0]<=r[0] and box[1]<=r[1] and box[2]>=r[2] and box[3]>=r[3])
                              for r in pin_boxes)
                if not partial:
                    legal_centers.append((cx,cy))
            if not legal_centers:
                raise ValueError('No unambiguous TSV center among local candidates')
            if self.tsv_override is not None:
                x,y = self.tsv_override
            elif self.thermal_preference is None:
                x,y = min(legal_centers,key=lambda xy:sum(abs(p.x-xy[0])+abs(p.y-xy[1]) for p in points))
            else:
                x,y = self.thermal_preference.choose(legal_centers,points)
            self.last_tsv_center = (x,y)
            for tier,layer in ((0,10),(1,11)):
                groups[tier].append(Point(x,y,layer))
                rects.add(self.rect(x,y,x,y,layer,self.tsv_half))
        for tier,group in enumerate(groups):
            group = list(dict.fromkeys(group))
            if not group:
                continue
            if len(group) == 1:
                p = group[0]
                rects.add(self.rect(p.x,p.y,p.x,p.y,p.layer))
            for i,j in tree_edges(group):
                self.connect(rects,group[i],group[j],self.pair(tier))
        # Remove redundant landing pads contained by a larger rectangle on the same layer.
        # Especially important at TSVs: duplicates must not create spurious overlap candidates.
        by_layer = {}
        for r in rects:
            by_layer.setdefault(r[4],[]).append(r)
        result = []
        for layer,rs in by_layer.items():
            if len(rs) > 256:
                result.extend(rs)
                continue
            for r in rs:
                if not any(r != s and s[0]<=r[0] and s[1]<=r[1] and s[2]>=r[2] and s[3]>=r[3] for s in rs):
                    result.append(r)
        return sorted(result, key=lambda r:(r[4],r[0],r[1],r[2],r[3])), crossing


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--def',dest='def_file',type=Path,required=True)
    p.add_argument('--lef',dest='lef_file',type=Path,required=True)
    p.add_argument('--thermal-report',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--pin-mode',choices=['physical','legacy'],default='physical')
    p.add_argument('--layer-shift',type=int,default=0)
    p.add_argument('--layer-policy',choices=['fixed','balanced'],default='fixed')
    p.add_argument('--congestion-weight',type=float,default=12.0)
    p.add_argument('--thermal-weight',type=float,default=1.0)
    p.add_argument('--thermal-policy',choices=['sample','region'],default='region')
    p.add_argument('--path-pattern',choices=['hv','vh','zx','zy'],default='hv')
    args = p.parse_args()
    if not args.thermal_report.is_file():
        p.error('thermal input is missing')
    start = time.perf_counter()
    design = Design(args.def_file,args.lef_file,args.pin_mode)
    if args.congestion_weight < 0 or args.thermal_weight < 0:
        p.error('optimization weights must be nonnegative')
    thermal_preference = None
    if args.thermal_weight > 0:
        from routing_thermal import ThermalPreference
        thermal_preference = ThermalPreference(design,args.thermal_report,args.thermal_weight,args.thermal_policy)
    from grid_router import GridRouter
    router = GridRouter(design,args.layer_shift,args.layer_policy,args.congestion_weight,thermal_preference,args.path_pattern)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix+'.tmp')
    counts = Counter()
    with temp.open('w',encoding='utf-8',newline='\n') as out:
        for name in design.nets:
            router.net_name=name
            rectangles, crossing = router.route_net(design.points(name))
            out.write(name+'\n(\n')
            for a,b,c,d,layer in rectangles:
                out.write(f'{a} {b} {c} {d} {design.layers[layer]["name"]}\n')
            out.write(')\n')
            counts['nets'] += 1
            counts['rectangles'] += len(rectangles)
            counts['cross_die_nets'] += crossing
    temp.replace(args.output)
    _trace.alias_guide(temp, args.output)
    report = dict(counts, seconds=round(time.perf_counter()-start,3),
                  pin_mode=args.pin_mode, layer_shift=args.layer_shift,
                  layer_policy=args.layer_policy, congestion_weight=args.congestion_weight,
                  selected_layer_shifts=dict(router.selected_layers),
                  thermal_weight=args.thermal_weight,
                  thermal_policy=args.thermal_policy,
                  path_pattern=args.path_pattern,
                  thermal_optimization=thermal_preference is not None, guide_rules='official-Q100-Q110',
                  version='0.4', official_evaluation='NOT_RUN')
    args.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
