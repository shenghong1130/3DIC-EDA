"""Standard-library GCell occupancy model for cost-aware layer allocation.

Counts PIN/OBS track occupancy and per-net guide demand. This remains a global
routing heuristic, not detailed obstacle avoidance or an official DRC checker.
"""
from bisect import bisect_left, bisect_right
from collections import defaultdict
import math
import re
from design import NUMBER, orient_xy


def lines(statements,lower,upper):
    # Q110: terminal cells end at DIEAREA, even if declarations under/overshoot.
    return sorted({lower,upper}|{start+i*step for start,count,step in statements
                                for i in range(count) if lower<start+i*step<upper})


def covered(coordinates,lo,hi):
    if math.isclose(lo,hi):
        if lo<coordinates[0] or lo>coordinates[-1]:
            return range(0)
        i=min(len(coordinates)-2,bisect_right(coordinates,lo)-1)
        return range(i,i+1)
    return range(max(0,bisect_right(coordinates,lo)-1),min(len(coordinates)-1,bisect_left(coordinates,hi)))


def obstacle_rectangles(design):
    """Yield transformed macro PIN/OBS boxes without changing original inputs."""
    shapes={}
    text=re.sub(r'#[^\n]*','',design.lef_path.read_text(encoding='utf-8'))
    for m in re.finditer(r'^MACRO\s+(\S+)\s*\n(.*?)^END\s+\1\s*$',text,re.M|re.S):
        rs=[]
        macro=design.macros[m[1]]
        for pin_shapes in macro.pins.values():
            rs.extend(pin_shapes)
        for obs in re.finditer(r'^\s*OBS\s*\n(.*?)^\s*END\s*$',m[2],re.M|re.S):
            layer=None
            for token in re.finditer(r'\bLAYER\s+(\S+)[^;]*;|\bRECT\s+('+NUMBER+r')\s+('+NUMBER+r')\s+('+NUMBER+r')\s+('+NUMBER+r')\s*;',obs[1]):
                if token[1]:
                    match=re.fullmatch(r'(?:metal|m)(\d+)',token[1],re.I)
                    layer=int(match[1]) if match else None
                elif layer in design.layers:
                    rs.append((layer,tuple(float(token[i]) for i in range(2,6))))
        shapes[m[1]]=list(dict.fromkeys(rs))
    for macro_name,x,y,orientation in design.components.values():
        macro=design.macros[macro_name]
        for layer,r in shapes[macro_name]:
            if layer not in design.layers:
                continue
            corners=[orient_xy(r[i]+macro.origin[0],r[j]+macro.origin[1],orientation,macro.width,macro.height)
                     for i,j in ((0,1),(0,3),(2,1),(2,3))]
            yield (x+min(p[0] for p in corners)*design.dbu,
                   y+min(p[1] for p in corners)*design.dbu,
                   x+max(p[0] for p in corners)*design.dbu,
                   y+max(p[1] for p in corners)*design.dbu,layer)


class RoutingResources:
    def __init__(self,design):
        self.d=design
        a,b,c,d=design.die
        self.x=lines(design.grid['X'],a,c)
        self.y=lines(design.grid['Y'],b,d)
        self.cols,self.rows=len(self.x)-1,len(self.y)-1
        self.cells=self.cols*self.rows
        self.stepx,self.stepy=design.grid['X'][0][2],design.grid['Y'][0][2]
        self.step=max(self.stepx,self.stepy)
        self.capacities,self.tracks={},{}
        for layer,info in design.layers.items():
            horizontal=info['direction']=='HORIZONTAL'
            coordinates=self.y if horizontal else self.x
            axis='Y' if horizontal else 'X'
            tracks=sorted({start+i*step for start,count,step in design.tracks.get((layer,axis),[]) for i in range(count)})
            counts=[0]*(len(coordinates)-1)
            for value in tracks:
                if coordinates[0]<=value<=coordinates[-1]:
                    counts[min(len(counts)-1,bisect_right(coordinates,value)-1)]+=1
            self.capacities[layer]=counts
            self.tracks[layer]=tracks
        self.usage={}
        self._seed_obstacles()

    def _seed_obstacles(self):
        for x1,y1,x2,y2,layer in obstacle_rectangles(self.d):
            tracks=self.tracks[layer]
            if len(tracks)<2:
                continue
            horizontal=self.d.layers[layer]['direction']=='HORIZONTAL'
            for row in covered(self.y,y1,y2):
                for col in covered(self.x,x1,x2):
                    lo,hi=(max(y1,self.y[row]),min(y2,self.y[row+1])) if horizontal else (max(x1,self.x[col]),min(x2,self.x[col+1]))
                    indices=covered(tracks,lo,hi)
                    if not indices:
                        continue
                    base=max(0,bisect_right(tracks,self.y[row] if horizontal else self.x[col])-1)
                    key=layer*self.cells+row*self.cols+col
                    mask=((1<<len(indices))-1)<<(indices.start-base)
                    self.usage[key]=self.usage.get(key,0)|mask
        for key in self.usage:
            self.usage[key]=self.usage[key].bit_count()
        self.obstacle_layer_cells=len(self.usage)

    def footprint(self,rect):
        offset=rect[4]*self.cells
        return {offset+row*self.cols+col for row in covered(self.y,rect[1],rect[3])
                for col in covered(self.x,rect[0],rect[2])}

    def capacity(self,key):
        layer,cell=divmod(key,self.cells)
        row,col=divmod(cell,self.cols)
        return self.capacities[layer][row if self.d.layers[layer]['direction']=='HORIZONTAL' else col]

    def candidate(self,rects,weight):
        used=set()
        retained=defaultdict(list)
        length=0.0
        for r in rects:
            cells=self.footprint(r)
            new=cells-used
            if new:
                retained[r[4]].append(r)
                length+=len(new)*(self.stepx if self.d.layers[r[4]]['direction']=='HORIZONTAL' else self.stepy)/self.step
                used.update(new)
        for layer,rs in retained.items():
            for a in rs:
                if any(max(a[0],b[0])<min(a[2],b[2]) and max(a[1],b[1])<min(a[3],b[3]) for b in retained.get(layer+1,[])):
                    length+=6 if layer==10 else 3
        if weight == 0:
            return length,used
        pressure=0.0
        for key in used:
            amount=self.usage.get(key,0)
            cap=self.capacity(key)
            if cap<=0:
                pressure+=10000.0
                continue
            before,after=amount/cap,(amount+1)/cap
            # Smooth pressure before saturation; a steeper price beyond capacity.
            pressure+=after**4-before**4+20*(max(0,after-1)**2-max(0,before-1)**2)
        return length+weight*pressure,used

    def commit(self,used,rects,crossing):
        for key in used:
            self.usage[key]=self.usage.get(key,0)+1
        if crossing:
            candidates=[]
            for a in (r for r in rects if r[4]==10):
                for b in (r for r in rects if r[4]==11):
                    x1,y1,x2,y2=max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])
                    if x1<x2 and y1<y2:
                        candidates.append((x1,y1,x2,y2))
            if candidates:
                box=max(candidates,key=lambda r:(r[2]-r[0])*(r[3]-r[1]))
                col=min(self.cols-1,bisect_right(self.x,(box[0]+box[2])/2)-1)
                row=min(self.rows-1,bisect_right(self.y,(box[1]+box[3])/2)-1)
                for layer in (10,11):
                    key=layer*self.cells+row*self.cols+col
                    self.usage[key]=self.usage.get(key,0)+1
