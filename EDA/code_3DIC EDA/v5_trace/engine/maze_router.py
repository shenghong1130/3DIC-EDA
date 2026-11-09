"""Bounded 3D A* on legal directional GCells, without crossing the TSV seam."""
import route_trace as _trace
import heapq
from grid_router import GridRouter
from design import Point
from router import tree_edges
from adaptive_router import merge_corridors


class MazeRouter(GridRouter):
    def __init__(self, design, resources, max_states=4000):
        super().__init__(design)
        self.resources=resources
        self.exempt=set()
        self.history={}
        self.max_states=max_states
        self.weight=20
        self.marginal_price=None
        self.interface_routing=False
        self.lower_interface_routing=True
        self.root_at_tsv=False
        self.terminal_order="prim"
        self.allowed_tsv_cells=set()
        self.forbidden_m10=set()
        self.terminal_access=None
        self.via_penalty=3.0
        self.heuristic_weight=1.0

    @_trace.observe_build
    def _build(self,points,center):
        if (not self.root_at_tsv or center is None) and self.terminal_order=='prim':return super()._build(points,center)
        rects={self.rect(*center,*center,layer) for layer in (10,11)} if center is not None else set()
        for tier,layer in ((0,10),(1,11)):
            group=([Point(*center,layer)] if center is not None else [])+[p for p in points if (p.layer-1)//10==tier]
            group=list(dict.fromkeys(Point(*self.grid.point_center(p.x,p.y),p.layer) for p in group))
            if not group:continue
            root=group[0];rects.add(self.rect(root.x,root.y,root.x,root.y,root.layer))
            if self.terminal_order=='far':
                order=sorted(group[1:],key=lambda p:(-abs(p.x-root.x)-abs(p.y-root.y),p.x,p.y,p.layer))
                for target in order:self.connect(rects,root,target,self.pair(tier))
            else:
                for i,j in tree_edges(group):self.connect(rects,group[i],group[j],self.pair(tier))
        return merge_corridors(rects,self.d.layers)

    def connect(self, rects, p, q, pair):
        grid=self.grid
        pc,pr=grid.cell(p.x,p.y);qc,qr=grid.cell(q.x,q.y)
        low,high=((1,10 if self.lower_interface_routing else 9) if p.layer<=10 else (11,20)) if self.interface_routing else ((1,9) if p.layer<=10 else (12,20))
        pl=max(low,min(high,p.layer));ql=max(low,min(high,q.layer))
        source,target=(pc,pr,pl),(qc,qr,ql)
        targets=self.terminal_access.get(target,{target}) if self.terminal_access is not None else {target}
        targets={s for s in targets if low<=s[2]<=high}
        if not targets:raise ValueError('No actual PIN access in this die')
        left,right=max(0,min([pc,qc]+[s[0] for s in targets])-5),min(self.resources.cols-1,max([pc,qc]+[s[0] for s in targets])+5)
        bottom,top=max(0,min([pr,qr]+[s[1] for s in targets])-5),min(self.resources.rows-1,max([pr,qr]+[s[1] for s in targets])+5)
        blocked_upper=set()
        if self.interface_routing and low==11:
            for rect in rects:
                if rect[4]==10:
                    for key in self.resources.footprint(rect):
                        _,cell=divmod(key,self.resources.cells);row,col=divmod(cell,self.resources.cols)
                        if (col,row) not in self.allowed_tsv_cells:blocked_upper.add((col,row))
        cache={}
        def price(state):
            if state not in cache:
                col,row,layer=state
                key=layer*self.resources.cells+row*self.resources.cols+col
                if self.interface_routing and ((layer==11 and (col,row) in blocked_upper) or (layer==10 and (col,row) in self.forbidden_m10)):
                    cache[state]=1e12;return cache[state]
                if self.marginal_price is not None:
                    cache[state]=self.marginal_price(key);return cache[state]
                if key in self.exempt:
                    value=0
                else:
                    amount,cap=self.resources.usage.get(key,0),self.resources.capacity(key)
                    if cap:
                        before,after=amount/cap,(amount+1)/cap
                        value=after**4-before**4+20*(max(0,after-1)**2-max(0,before-1)**2)
                    else:value=10000
                cache[state]=1+self.weight*value+self.history.get(key,0)
            return cache[state]
        via_lower_bound=1+self.via_penalty if self.marginal_price is not None else 3
        def heuristic(s):return self.heuristic_weight*min(abs(s[0]-t[0])+abs(s[1]-t[1])+via_lower_bound*abs(s[2]-t[2]) for t in targets)
        # Grow the existing connected tree: any already routed normal-layer
        # GCell may serve as a source, allowing genuine shared Steiner trunks.
        seeds={source};existing=set()
        for rect in rects:
            if not low<=rect[4]<=high:continue
            for key in self.resources.footprint(rect):
                layer,cell=divmod(key,self.resources.cells)
                row,col=divmod(cell,self.resources.cols)
                if self.terminal_access is not None or (left<=col<=right and bottom<=row<=top):existing.add((col,row,layer))
        reuse_existing=self.terminal_access is not None and bool(existing)
        if reuse_existing:
            seeds=existing
            left=max(0,min(left,min(s[0] for s in seeds))-1);right=min(self.resources.cols-1,max(right,max(s[0] for s in seeds))+1)
            bottom=max(0,min(bottom,min(s[1] for s in seeds))-1);top=min(self.resources.rows-1,max(top,max(s[1] for s in seeds))+1)
        else:seeds.update(existing)
        distances={state:0.0 for state in seeds}
        parents={}
        queue=[(heuristic(state),0.0,state) for state in seeds]
        heapq.heapify(queue)
        expanded=0
        while queue and expanded<self.max_states:
            _,cost,state=heapq.heappop(queue)
            if cost!=distances[state]:continue
            if state in targets:
                goal=state
                path=[state]
                while state in parents:
                    state=parents[state];path.append(state)
                _trace.astar_path(self, path)
                for col,row,layer in path:
                    x,y=grid.center(col,row)
                    rects.add(self.rect(x,y,x,y,layer))
                if not reuse_existing:self.stack(rects,*grid.center(pc,pr),p.layer,pl)
                if goal==target:self.stack(rects,*grid.center(qc,qr),q.layer,ql)
                return
            _trace.search_event(self, "search_expanded", state)
            expanded+=1
            col,row,layer=state
            horizontal=self.d.layers[layer]['direction']=='HORIZONTAL'
            neighbors=[(col-1,row,layer),(col+1,row,layer)] if horizontal else [(col,row-1,layer),(col,row+1,layer)]
            neighbors += [(col,row,layer-1),(col,row,layer+1)]
            for nxt in neighbors:
                if not (left<=nxt[0]<=right and bottom<=nxt[1]<=top and low<=nxt[2]<=high):continue
                value=cost+price(nxt)+(self.via_penalty if nxt[2]!=layer else 0)
                if value<distances.get(nxt,float('inf')):
                    distances[nxt]=value;parents[nxt]=state
                    _trace.search_event(self, "search_relaxed", nxt, state)
                    heapq.heappush(queue,(value+heuristic(nxt),value,nxt))
        raise ValueError('Bounded 3D maze search exhausted')
