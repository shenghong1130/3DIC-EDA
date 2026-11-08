"""GCell corridors, directional connections and one actual TSV per cross-die net."""
import statistics
from design import Point
from router import Router,tree_edges
from guide_rules import grid_for,select_tsv


class GridRouter(Router):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.grid=grid_for(self.d)
        self.net_name=None

    def rect(self,x1,y1,x2,y2,layer,half=None):
        # Width is one actual GCell, including a shortened terminal cell (Q110).
        r=self.grid.rectangle(x1,y1,x2,y2,layer)
        horizontal=self.d.layers[layer]['direction']=='HORIZONTAL'
        c0,c1=self.grid.x_index[r[0]],self.grid.x_index[r[2]]
        r0,r1=self.grid.y_index[r[1]],self.grid.y_index[r[3]]
        if (r1-r0 if horizontal else c1-c0)>1:
            raise ValueError('Nonpreferred corridor spans more than one GCell')
        return r

    def connect(self,rects,p,q,pair):
        p=Point(*self.grid.point_center(p.x,p.y),p.layer)
        q=Point(*self.grid.point_center(q.x,q.y),q.layer)
        # The original path builder remains useful, but every terminal/bend is
        # snapped to a cell center BEFORE making rectangles, never expanded later.
        if self.path_pattern not in ('zx','zy'):
            return super().connect(rects,p,q,pair)
        h,v=pair
        pc,pr=self.grid.cell(p.x,p.y);qc,qr=self.grid.cell(q.x,q.y)
        if self.path_pattern=='zy':
            row=(pr+qr)//2+self.detour_steps
            if pr==qr and self.detour_steps==0:
                row+=2 if pr<(len(self.grid.y)-2)/2 else -2
            row=max(0,min(len(self.grid.y)-2,row))
            mid=self.grid.center(pc,row)[1]
            self.stack(rects,p.x,p.y,p.layer,v)
            rects.add(self.rect(p.x,p.y,p.x,mid,v))
            self.stack(rects,p.x,mid,v,h)
            rects.add(self.rect(p.x,mid,q.x,mid,h))
            self.stack(rects,q.x,mid,h,v)
            rects.add(self.rect(q.x,mid,q.x,q.y,v))
            self.stack(rects,q.x,q.y,v,q.layer)
        else:
            col=(pc+qc)//2+self.detour_steps
            if pc==qc and self.detour_steps==0:
                col+=2 if pc<(len(self.grid.x)-2)/2 else -2
            col=max(0,min(len(self.grid.x)-2,col))
            mid=self.grid.center(col,pr)[0]
            self.stack(rects,p.x,p.y,p.layer,h)
            rects.add(self.rect(p.x,p.y,mid,p.y,h))
            self.stack(rects,mid,p.y,h,v)
            rects.add(self.rect(mid,p.y,mid,q.y,v))
            self.stack(rects,mid,q.y,v,h)
            rects.add(self.rect(mid,q.y,q.x,q.y,h))
            self.stack(rects,q.x,q.y,h,q.layer)

    def _build(self,points,center):
        rects=set()
        groups=[[p for p in points if (p.layer-1)//10==tier] for tier in (0,1)]
        if center is not None:
            for tier,layer in ((0,10),(1,11)):
                groups[tier].append(Point(*center,layer))
                rects.add(self.rect(*center,*center,layer))
        for tier,group in enumerate(groups):
            group=list(dict.fromkeys(Point(*self.grid.point_center(p.x,p.y),p.layer) for p in group))
            if len(group)==1:
                p=group[0];rects.add(self.rect(p.x,p.y,p.x,p.y,p.layer))
            for i,j in tree_edges(group):
                self.connect(rects,group[i],group[j],self.pair(tier))
        # Canonical full corridors remove duplicate access stacks without
        # splitting cells or manipulating the evaluator's rectangle ordering.
        result=[]
        by_layer={}
        for r in rects:by_layer.setdefault(r[4],[]).append(r)
        for layer,rs in by_layer.items():
            if len(rs)>256:
                result.extend(rs);continue
            result.extend(r for r in rs if not any(r!=s and s[0]<=r[0] and s[1]<=r[1]
                          and s[2]>=r[2] and s[3]>=r[3] for s in rs))
        return sorted(result,key=lambda r:(r[4],r[0],r[1],r[2],r[3]))

    def _route_net_fixed(self,points):
        crossing=len({(p.layer-1)//10 for p in points})==2
        if not crossing:return self._build(points,None),False
        if self.net_name is not None and hasattr(self.d,'net_pin_shapes'):
            pins=[shape for pin in self.d.net_pin_shapes(self.net_name) for shape in pin]
        else:
            pins=[(p.layer,(p.x-1e-5,p.y-1e-5,p.x+1e-5,p.y+1e-5)) for p in points]
        if self.tsv_override is not None:
            center=self.grid.point_center(*self.tsv_override)
            from tsv_obstacles import obstacles_for
            col,row=self.grid.cell(*center)
            obstacles_for(self.d).check(self.net_name,(self.grid.x[col],self.grid.y[row],self.grid.x[col+1],self.grid.y[row+1]))
            rects=self._build(points,center)
            selected=select_tsv(rects,pins)
            actual=((selected[0]+selected[2])/2,(selected[1]+selected[3])/2)
            if actual!=center:raise ValueError('Frozen actual TSV changed')
            self.last_tsv_center=center
            return rects,True
        x,y=statistics.median(p.x for p in points),statistics.median(p.y for p in points)
        c,r=self.grid.cell(x,y)
        centers=[self.grid.center(max(0,min(len(self.grid.x)-2,c+dc)),
                                  max(0,min(len(self.grid.y)-2,r+dr)))
                 for dc,dr in ((0,0),(-1,0),(1,0),(0,-1),(0,1),(-2,0),(2,0),(0,-2),(0,2))]
        centers += [self.grid.point_center(p.x,p.y) for p in points if p.layer in (10,11)]
        if self.thermal_preference is not None:
            centers += [self.grid.point_center(*xy) for xy in self.thermal_preference.candidates(points)]
        centers=list(dict.fromkeys(centers))
        from tsv_obstacles import obstacles_for
        obstacles=obstacles_for(self.d)
        centers=[xy for xy in centers if not obstacles.conflicts(xy)]
        if not centers:
            for radius in range(1,max(len(self.grid.x),len(self.grid.y))):
                for dc in range(-radius,radius+1):
                    dy=radius-abs(dc)
                    for dr in ({dy,-dy} if dy else {0}):
                        nc,nr=c+dc,r+dr
                        if 0<=nc<len(self.grid.x)-1 and 0<=nr<len(self.grid.y)-1:
                            xy=self.grid.center(nc,nr)
                            if not obstacles.conflicts(xy):centers.append(xy)
                if centers:break
        # Validate actual candidate identity, not just distance to interface pins.
        if self.thermal_preference is not None:
            ordered=self.thermal_preference.rank(centers,points)
        else:
            ordered=sorted(centers,key=lambda xy:(sum(abs(p.x-xy[0])+abs(p.y-xy[1]) for p in points),xy))
        for center in ordered:
            rects=self._build(points,center)
            try:selected=select_tsv(rects,pins)
            except ValueError:continue
            actual=((selected[0]+selected[2])/2,(selected[1]+selected[3])/2)
            if actual==center:
                self.last_tsv_center=center
                return rects,True
        raise ValueError('No legal single-TSV GCell candidate')
