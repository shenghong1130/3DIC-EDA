"""Experimental local TSV bridges with cell-level resource guards and pruning.

All candidates derive from the physical inputs and self-generated routing.
Retain shared trees, use near-interface bridges, and prune abandoned branches
before comparing wire cost. No case-name constants or saved scoring data.
"""
import route_trace as _trace
import argparse,json,math,time
from pathlib import Path
from balanced_thermal import BalancedThermal
from thermal_patch import move_patch
from compact_guide import compact_net
from validate import validate,read_guide,check_net
from design import Design
from routing_thermal import ThermalPreference
from tsv_obstacles import obstacles_for


class GuardedThermal(BalancedThermal):
    def __init__(self,d,heat,budget=1000):
        super().__init__(d,heat,budget)
        self.fixed=dict(self.r.usage);self.original=None;self.active_name=None;self.old_counts={}
        self._base_centers=self.router.candidate_centers
        self.router.candidate_centers=self.local_centers
        self.candidate_limit=8

    def priority_centers(self,points,global_hot):
        # A distant global peak can hide a useful warm region inside this net's
        # own terminal envelope. Rank attainable local heat gains as well.
        return list(dict.fromkeys(self.router.heat.candidates(points)+global_hot))

    def phi(self,key,amount):
        cap=self.r.capacity(key);fixed=self.fixed.get(key,0)
        reserve=1 if fixed and amount>fixed else 0
        return (max(0,amount+reserve-cap)/cap)**2 if cap>0 else (10000 if amount else 0)

    def objective(self,wire,penalty,thermal):
        if penalty>self.penalty+1e-10:return -float('inf')
        return super().objective(wire,penalty,thermal)

    def safe_counts(self,counts):
        for key,n in counts.items():
            amount=self.r.usage.get(key,0)
            if self.phi(key,amount+n)>self.phi(key,amount+self.old_counts.get(key,0))+1e-12:return False
        return True

    def local_centers(self,points):
        candidates=set(self._base_centers(points));grid=self.router.grid
        old=self.old_cell;rows,cols=self.r.rows,self.r.cols
        for radius in (1,2,4,8,16,32,64,128):
            for dx,dy in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
                col,row=old[0]+dx*radius,old[1]+dy*radius
                if 0<=col<cols and 0<=row<rows:candidates.add(grid.center(col,row))
        result=[];obstacles=obstacles_for(self.d)
        for xy in sorted(candidates):
            cell=grid.cell(*xy)
            if obstacles.conflicts(xy):continue
            keys=[layer*self.r.cells+cell[1]*cols+cell[0] for layer in (10,11)]
            # A full TSV bundle can only be placed without new risk when both
            # interface cells are free after this net has been removed.
            if any(self.r.usage.get(k,0)>0 or self.r.capacity(k)<=0 for k in keys):continue
            if cell!=old and self.router.tsv_usage[cell]:continue
            result.append(xy)
        return result

    def shared_build(self,points,center):
        best=None
        for pattern in ('hv','vh'):
            try:
                rs=move_patch(self.d,self.active_name,self.original,center,pattern)
                check_net(self.d,self.active_name,rs)
                rs,_=compact_net(self.d,self.r,self.active_name,rs,12000)
                wire,counts,_=self.counts(self.active_name,rs)
                if not self.safe_counts(counts):continue
            except ValueError:continue
            delta=math.fsum(self.phi(k,self.r.usage.get(k,0)+n)-self.phi(k,self.r.usage.get(k,0)) for k,n in counts.items())
            wire_price=45*min(self.reference,self.wire)/max(1,self.wire)**2
            ratio=15/(1+max(0,self.penalty))**1.5/max(wire_price,1e-12)
            key=(wire+ratio*delta,wire,pattern)
            if best is None or key<best[0]:best=key,rs
        if best is None:raise ValueError('No risk-preserving thermal bridge')
        return best[1]

    @_trace.observe_choice
    def reroute(self,name,rects):
        if name not in self.targets:return rects
        self.original=rects;self.active_name=name
        _,self.old_counts,self.old_cell=self.counts(name,rects)
        return super().reroute(name,rects)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--def',dest='def_file',type=Path,required=True)
    for key in ('lef','thermal-report','seed-guide','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--max-reroutes',type=int,default=1000);a=p.parse_args()
    if a.output.exists():p.error('Output exists')
    start=time.monotonic();d=Design(a.def_file,a.lef);validate(d,a.seed_guide)
    e=GuardedThermal(d,ThermalPreference(d,a.thermal_report,2,'region'),a.max_reroutes);e.initialize(a.seed_guide);initial=e.snapshot()
    print('INITIAL',json.dumps(initial),flush=True);a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('w',encoding='utf8',newline='\n') as f:
        for i,(name,rs) in enumerate(read_guide(a.seed_guide),1):
            rs=e.reroute(name,rs);f.write(name+'\n(\n')
            for x,y,xx,yy,l in rs:f.write(f'{x} {y} {xx} {yy} {d.layers[l]["name"]}\n')
            f.write(')\n')
            if i%20000==0:print('THERMAL_BRIDGE',i,dict(e.stats),flush=True)
    report=dict(version='0.13-guarded-thermal-experiment',initial=initial,final=e.snapshot(),stats=dict(e.stats),validation=validate(d,a.output),seconds=time.monotonic()-start,official_score=None)
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':main()
