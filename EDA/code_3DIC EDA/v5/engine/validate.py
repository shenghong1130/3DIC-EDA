#!/usr/bin/env python3
"""Independent GUIDE graph checks. LOCAL_PASS is not an official score or DRC proof."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import time
from design import Design, layer_number


def read_guide(path):
    state, name, rects = 'name', None, None
    with Path(path).open(encoding='utf-8') as stream:
        for lineno, line in enumerate(stream,1):
            line = line.strip()
            if not line:
                continue
            if state == 'name':
                if line in ('(',')'):
                    raise ValueError(f'{lineno}: expected net name')
                name, state = line,'open'
            elif state == 'open':
                if line != '(':
                    raise ValueError(f'{lineno}: expected opening parenthesis')
                rects, state = [],'rect'
            elif line == ')':
                if not rects:
                    raise ValueError(f'{lineno}: empty net {name}')
                yield name, list(dict.fromkeys(rects))
                state = 'name'
            else:
                f = line.split()
                if len(f) != 5:
                    raise ValueError(f'{lineno}: expected four integer coordinates and a layer')
                rects.append(tuple(map(int,f[:4]))+(layer_number(f[4]),))
    if state != 'name':
        raise ValueError('Truncated GUIDE')


def overlaps(a,b,positive=False):
    x1,y1,x2,y2 = max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])
    return (x1<x2 and y1<y2) if positive else (x1<=x2 and y1<=y2 and (x1<x2 or y1<y2))


def check_net_legacy(design,name,rects):
    """Historical v0.3 geometry only; never used to certify new submissions."""
    points = design.points(name)
    tiers = {(p.layer-1)//10 for p in points}
    a,b,c,d = design.die
    by_layer = defaultdict(list)
    for i,r in enumerate(rects):
        if r[4] not in design.layers:
            raise ValueError(f'{name}: undefined layer {r[4]}')
        if not (a <= r[0] < r[2] <= c and b <= r[1] < r[3] <= d):
            raise ValueError(f'{name}: invalid rectangle or outside DIEAREA {r}')
        if (r[4]-1)//10 not in tiers:
            raise ValueError(f'{name}: internal net used the other die')
        by_layer[r[4]].append(i)
    for p in points:
        if not any(rects[i][0]<=p.x<=rects[i][2] and rects[i][1]<=p.y<=rects[i][3] for i in by_layer[p.layer]):
            raise ValueError(f'{name}: uncovered pin {p}')
    parent = list(range(len(rects)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    def union(i,j):
        parent[find(i)] = find(j)
    # Sweep X; same-layer edges may touch, inter-layer edges need positive area.
    # This avoids a full all-pairs scan for spatially separated guide corridors.
    for layer,indices in by_layer.items():
        candidates = sorted(indices+by_layer.get(layer+1,[]),key=lambda i:rects[i][0])
        active = []
        for i in candidates:
            r = rects[i]
            active = [j for j in active if rects[j][2]>=r[0]]
            for j in active:
                s = rects[j]
                if s[4] == r[4] and s[4] != layer:
                    continue
                if overlaps(r,s,positive=r[4]!=s[4]):
                    union(i,j)
            active.append(i)
    if len({find(i) for i in range(len(rects))}) != 1:
        raise ValueError(f'{name}: disconnected route graph')
    tsvs = []
    for i in by_layer[10]:
        for j in by_layer[11]:
            r,s = rects[i],rects[j]
            if overlaps(r,s,positive=True):
                tsvs.append((max(r[0],s[0]),max(r[1],s[1]),min(r[2],s[2]),min(r[3],s[3])))
    tsvs = list(dict.fromkeys(tsvs))
    if len(tiers)==2:
        if not 1 <= len(tsvs) <= 2:
            raise ValueError(f'{name}: expected 1 or 2 TSV candidates, got {len(tsvs)}')
        if len(tsvs)==2:
            interface = [p for p in points if p.layer in (10,11)]
            contains = [bool(interface) and all(r[0]<=p.x<=r[2] and r[1]<=p.y<=r[3] for p in interface) for r in tsvs]
            if not any(contains):
                raise ValueError(f'{name}: two ambiguous TSV candidates')
    elif tsvs:
        raise ValueError(f'{name}: same-tier net has TSV')
    return len(tiers)==2


def check_net(design,name,rects):
    """Official Q62/Q100-Q110. Independent checks, not a router repair step."""
    from guide_rules import grid_for,select_tsv
    grid=grid_for(design)
    points=design.points(name)
    tiers={(p.layer-1)//10 for p in points}
    a,b,c,d=design.die
    clipped=[]
    for r in rects:
        if r[0]>=r[2] or r[1]>=r[3] or r[2]<=a or r[0]>=c or r[3]<=b or r[1]>=d:
            raise ValueError(f'{name}: invalid rectangle or wholly outside DIEAREA {r}')
        clipped.append((max(a,r[0]),max(b,r[1]),min(c,r[2]),min(d,r[3]),r[4]))
    rects=clipped  # Q108: check every raw rectangle after DIE clipping, before dedup.
    by_layer=defaultdict(list)
    for i,r in enumerate(rects):
        if r[4] not in design.layers:
            raise ValueError(f'{name}: undefined layer {r[4]}')
        if not (a<=r[0]<r[2]<=c and b<=r[1]<r[3]<=d):
            raise ValueError(f'{name}: invalid rectangle or outside DIEAREA {r}')
        if r[0] not in grid.x_index or r[2] not in grid.x_index or r[1] not in grid.y_index or r[3] not in grid.y_index:
            raise ValueError(f'{name}: GUIDE not aligned to GCell (Q102/Q108)')
        horizontal=design.layers[r[4]]['direction']=='HORIZONTAL'
        span=(grid.y_index[r[3]]-grid.y_index[r[1]]) if horizontal else (grid.x_index[r[2]]-grid.x_index[r[0]])
        if span>1:
            raise ValueError(f'{name}: nonpreferred span exceeds one GCell (Q101/Q108)')
        if (r[4]-1)//10 not in tiers:
            raise ValueError(f'{name}: internal net used the other die')
        by_layer[r[4]].append(i)
    pins=design.net_pin_shapes(name) if hasattr(design,'net_pin_shapes') else [
        [(p.layer,(p.x,p.y,p.x,p.y))] for p in points]
    for alternatives in pins:
        covered=False
        for layer,box in alternatives:
            for i in by_layer[layer]:
                r=rects[i]
                if (overlaps(r,box,True) if box[0]<box[2] and box[1]<box[3] else
                    r[0]<=box[0]<=r[2] and r[1]<=box[1]<=r[3]):
                    covered=True;break
            if covered:break
        if not covered:
            raise ValueError(f'{name}: uncovered pin (all RECT/PORT alternatives checked, Q109)')
    selected=select_tsv(rects,[shape for pin in pins for shape in pin]) if len(tiers)==2 else None
    if selected is not None:
        from tsv_obstacles import obstacles_for
        obstacles_for(design).check(name,selected)
    parent=list(range(len(rects)))
    def find(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    for layer,indices in by_layer.items():
        candidates=sorted(indices+by_layer.get(layer+1,[]),key=lambda i:rects[i][0])
        active=[]
        for i in candidates:
            r=rects[i]
            active=[j for j in active if rects[j][2]>=r[0]]
            for j in active:
                s=rects[j]
                if s[4]==r[4] and s[4]!=layer:continue
                ox=min(r[2],s[2])-max(r[0],s[0]);oy=min(r[3],s[3])-max(r[1],s[1])
                if r[4]==s[4]:
                    connected=(ox>=0 and oy>0) if design.layers[r[4]]['direction']=='HORIZONTAL' else (ox>0 and oy>=0)
                else:
                    connected=ox>0 and oy>0
                    if connected and {r[4],s[4]}=={10,11}:
                        if selected is None:connected=False
                        else:
                            x,y=(selected[0]+selected[2])/2,(selected[1]+selected[3])/2
                            connected=max(r[0],s[0])<x<min(r[2],s[2]) and max(r[1],s[1])<y<min(r[3],s[3])
                if connected:parent[find(i)]=find(j)
            active.append(i)
    if not rects or len({find(i) for i in range(len(rects))})!=1:
        raise ValueError(f'{name}: disconnected directional route graph through selected TSV (Q100/Q103)')
    return len(tiers)==2


def validate(design,path,legacy=False):
    seen, rectangles, crossing = set(),0,0
    for name,rs in read_guide(path):
        if name not in design.nets:
            raise ValueError(f'Extra net not in DEF: {name}')
        if name in seen:
            raise ValueError(f'Duplicate net block: {name}')
        seen.add(name)
        crossing += (check_net_legacy if legacy else check_net)(design,name,rs)
        rectangles += len(rs)
    missing = design.nets.keys()-seen
    if missing:
        raise ValueError(f'Missing {len(missing)} nets: {sorted(missing)[:5]}')
    return {'status':'LOCAL_PASS', 'nets':len(seen),'rectangles':rectangles,
            'cross_die_nets':crossing,'official_evaluation':'NOT_RUN','rules':'legacy-v0.3' if legacy else 'official-Q62-Q100-Q110',
            'scope':['net_set','pin_coverage','positive_area','die_bounds','routing_layers',
                     'adjacent_layer_connectivity','same_die_restriction','tsv_candidates']+([] if legacy else
                     ['gcell_alignment','preferred_direction_span','directional_same_layer_connectivity',
                      'selected_tsv_connectivity','any_actual_pin_RECT_PORT','tsv_landing_PIN_OBS_exclusion']),
            'not_checked':['track_assignment','obstacle_DRC','via_enclosure_DRC','official_quality_score']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('guide',type=Path)
    p.add_argument('--def',dest='def_file',type=Path,required=True)
    p.add_argument('--lef',dest='lef_file',type=Path,required=True)
    p.add_argument('--pin-mode',choices=['physical','legacy'],default='physical')
    p.add_argument('--report',type=Path,required=True)
    args = p.parse_args()
    start = time.perf_counter()
    try:
        report = validate(Design(args.def_file,args.lef_file,args.pin_mode),args.guide)
    except (ValueError, KeyError, OSError) as exc:
        report = {'status':'LOCAL_FAIL','error':str(exc),'official_evaluation':'NOT_RUN'}
    report['seconds'] = round(time.perf_counter()-start,3)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    raise SystemExit(0 if report['status']=='LOCAL_PASS' else 1)


if __name__ == '__main__':
    main()
