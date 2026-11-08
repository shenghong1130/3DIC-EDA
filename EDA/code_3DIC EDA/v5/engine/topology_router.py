"""Input-only GCell topology candidates; no scoring feedback is required."""
import statistics
from design import Point
from grid_router import GridRouter
from router import tree_edges
from adaptive_router import merge_corridors


class TopologyRouter(GridRouter):
    def __init__(self,design):
        super().__init__(design)
        self.mode='msthv'
        self.interface_routing=False
        self.both_interfaces=False
        self.pair_override={}
        self.mode_override={}

    def pair(self,tier):
        if tier in self.pair_override:return self.pair_override[tier]
        if not self.interface_routing or (tier==0 and not self.both_interfaces):return super().pair(tier)
        available=[n for n in self.d.layers if (1<=n<=10 if tier==0 else 11<=n<=20)]
        pairs=[(h,v) for h in available for v in available if abs(h-v)==1
               and self.d.layers[h]['direction']=='HORIZONTAL' and self.d.layers[v]['direction']=='VERTICAL']
        return sorted(pairs,key=lambda p:(max(p),min(p)))[self.layer_shift%len(pairs)]

    def access_points(self,name,anchor_center=None):
        original=self.d.points(name)
        targets={}
        for tier in (0,1):
            group=[p for p in original if (p.layer-1)//10==tier]
            if group:targets[tier]=(statistics.median(p.x for p in group),statistics.median(p.y for p in group))
        result=[]
        for connection in self.d.nets[name]:
            old=self.d.pin(*connection)
            if anchor_center is not None and (old.layer-1)//10==1:
                box=self.grid.rectangle(*anchor_center,*anchor_center,11)
                if any(layer==11 and max(box[0],r[0])<min(box[2],r[2]) and max(box[1],r[1])<min(box[3],r[3]) for layer,r in self.d.pin_shapes(*connection)):
                    result.append(Point(*anchor_center,11));continue
            if (old.layer==10 and not self.both_interfaces) or (old.layer==11 and not self.interface_routing):
                result.append(old);continue
            tier=(old.layer-1)//10
            h,v=self.pair(tier)
            mode=self.mode_override.get(tier,self.mode)
            preferred=v if mode=='htrunk' else (h if mode=='vtrunk' else None)
            tx,ty=targets[tier]
            alternatives=[]
            for layer,box in self.d.pin_shapes(*connection):
                if (layer-1)//10!=tier or (layer==10 and not self.both_interfaces) or (layer==11 and not self.interface_routing):continue
                xl,yl,xh,yh=max(self.d.die[0],box[0]),max(self.d.die[1],box[1]),min(self.d.die[2],box[2]),min(self.d.die[3],box[3])
                if xl>=xh or yl>=yh:continue
                x=max(xl+min(.001,(xh-xl)/2),min(xh-min(.001,(xh-xl)/2),tx))
                y=max(yl+min(.001,(yh-yl)/2),min(yh-min(.001,(yh-yl)/2),ty))
                cx,cy=self.grid.point_center(x,y)
                vias=abs(layer-preferred) if preferred is not None else min(abs(layer-h),abs(layer-v))
                score=abs(cx-tx)+abs(cy-ty)+3*max(self.d.grid['X'][0][2],self.d.grid['Y'][0][2])*vias
                alternatives.append((score,layer,cx,cy))
            if alternatives:
                _,layer,x,y=min(alternatives);result.append(Point(x,y,layer))
            else:result.append(old)
        return list(dict.fromkeys(result))

    def _build(self,points,center):
        if self.mode.startswith('mst') and not self.mode_override:
            self.path_pattern='vh' if self.mode=='mstvh' else 'hv'
            return merge_corridors(super()._build(points,center),self.d.layers)
        rects=set()
        groups=[[p for p in points if (p.layer-1)//10==tier] for tier in (0,1)]
        if center is not None:
            for tier,layer in ((0,10),(1,11)):
                groups[tier].append(Point(*center,layer));rects.add(self.rect(*center,*center,layer))
        for tier,group in enumerate(groups):
            group=list(dict.fromkeys(Point(*self.grid.point_center(p.x,p.y),p.layer) for p in group))
            if not group:continue
            if len(group)==1:
                p=group[0];rects.add(self.rect(p.x,p.y,p.x,p.y,p.layer));continue
            h,v=self.pair(tier)
            mode=self.mode_override.get(tier,self.mode)
            if mode.startswith('mst'):
                self.path_pattern='vh' if mode=='mstvh' else 'hv'
                for i,j in tree_edges(group):self.connect(rects,group[i],group[j],(h,v))
                continue
            if mode=='htrunk':
                row=statistics.median_low(sorted(self.grid.cell(p.x,p.y)[1] for p in group))
                y=self.grid.center(0,row)[1]
                rects.add(self.rect(min(p.x for p in group),y,max(p.x for p in group),y,h))
                for p in group:
                    if p.y==y:self.stack(rects,p.x,p.y,p.layer,h)
                    else:
                        self.stack(rects,p.x,p.y,p.layer,v)
                        rects.add(self.rect(p.x,p.y,p.x,y,v));self.stack(rects,p.x,y,v,h)
            else:
                col=statistics.median_low(sorted(self.grid.cell(p.x,p.y)[0] for p in group))
                x=self.grid.center(col,0)[0]
                rects.add(self.rect(x,min(p.y for p in group),x,max(p.y for p in group),v))
                for p in group:
                    if p.x==x:self.stack(rects,p.x,p.y,p.layer,v)
                    else:
                        self.stack(rects,p.x,p.y,p.layer,h)
                        rects.add(self.rect(p.x,p.y,x,p.y,h));self.stack(rects,x,p.y,h,v)
        return merge_corridors(rects,self.d.layers)


