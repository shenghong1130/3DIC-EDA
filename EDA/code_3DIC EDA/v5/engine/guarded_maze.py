"""Wire-first shared-tree growth with hard per-cell resource guards.

Use actual PIN ports as alternative targets, reuse every connected tree cell,
and retain exact TSV candidates. Accept only strict wire reduction with no
per-cell risk increase. Runtime inputs never include official scoring reports.
"""
import argparse,json,time
from functools import lru_cache
from pathlib import Path
from maze_wire import MazeWireShortener
from guide_rules import tsv_candidates
from design import Design
from validate import validate
from compact_guide import compact_net
from guide_invariants import check_tsv_geometry


def access_map(d,r,grid,name,options,limit=16):
    pins=[]
    for shapes in d.net_pin_shapes(name):
        states=set()
        for layer,box in shapes:
            for key in r.footprint((*box,layer)):
                plane,index=divmod(key,r.cells);states.add((index%r.cols,index//r.cols,plane))
        pins.append(states)
    result={}
    for points in options:
        for p in points:
            col,row=grid.cell(p.x,p.y);state=(col,row,p.layer)
            owners=[states for states in pins if state in states]
            choices=set.intersection(*owners) if owners else {state}
            choices={s for s in choices if (s[2]-1)//10==(p.layer-1)//10}
            result[state]=set(sorted(choices,key=lambda s:(abs(s[0]-col)+abs(s[1]-row)+3*abs(s[2]-p.layer),s))[:limit])
    return result


class GuardedMaze(MazeWireShortener):
    def __init__(self,d,budget=2048,max_pins=128,max_states=8000):
        super().__init__(d,budget,max_pins,0,2,max_states);self.fixed=dict(self.r.usage)

    def phi(self,key,amount):
        cap=self.r.capacity(key);fixed=self.fixed.get(key,0);reserve=1 if fixed and amount>fixed else 0
        return (max(0,amount+reserve-cap)/cap)**2 if cap>0 else (10000 if amount else 0)

    def safe_counts(self,counts,old_counts):
        for key,n in counts.items():
            amount=self.r.usage.get(key,0)
            if self.phi(key,amount+n)>self.phi(key,amount+old_counts.get(key,0))+1e-12:return False
        return True

    def candidates(self,name,points,center,rects,old_counts):
        exempt=self.r.pin_cells(name)
        @lru_cache(maxsize=None)
        def marginal(key):
            if key in exempt:return 1.0
            amount=self.r.usage.get(key,0)
            if key in self.protected_tsv_keys and not old_counts.get(key):return 1e12
            if self.phi(key,amount+1)>self.phi(key,amount+old_counts.get(key,0))+1e-12:return 1e12
            return 1.0
        boxes=tsv_candidates(rects);self.maze.interface_routing=len(boxes)<=1
        self.maze.allowed_tsv_cells={self.router.grid.cell((b[0]+b[2])/2,(b[1]+b[3])/2) for b in boxes}
        self.maze.marginal_price=marginal;self.maze.exempt=exempt
        self.topology.net_name=name;self.topology.mode='msthv';alternative=self.topology.access_points(name,center)
        def canonical(access):
            return tuple(dict.fromkeys((self.router.grid.cell(p.x,p.y),p.layer) for p in access))
        options=[points]+([alternative] if canonical(alternative)!=canonical(points) else [])
        self.maze.terminal_access=access_map(self.d,self.r,self.router.grid,name,options)
        seen=set()
        try:
            for access in options:
                for order in ('prim','far'):
                    self.maze.terminal_order=order
                    try:
                        candidate=self.maze._build(access,center);candidate,_=compact_net(self.d,self.r,name,candidate,12000)
                        signature=tuple(candidate)
                        if signature in seen:continue
                        seen.add(signature);_,counts,_=self.counts(candidate,points,name)
                        if not self.safe_counts(counts,old_counts):self.stats['per_cell_risk_rejected']+=1;continue
                        yield candidate
                    except ValueError:self.stats['maze_rejected_or_exhausted']+=1
        finally:self.maze.marginal_price=None;self.maze.terminal_access=None;marginal.cache_clear()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--def',dest='def_file',type=Path,required=True)
    for k in ('lef','seed-guide','output'):p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--max-reroutes',type=int,default=2048);a=p.parse_args()
    if a.max_reroutes<1:p.error('Budget must be positive')
    if a.output.exists():p.error('Output exists')
    start=time.monotonic();d=Design(a.def_file,a.lef);validate(d,a.seed_guide);e=GuardedMaze(d,a.max_reroutes);initial=e.initialize(a.seed_guide);print('INITIAL',json.dumps(initial),flush=True)
    a.output.parent.mkdir(parents=True,exist_ok=True);temp=a.output.with_suffix('.tmp.guide');result=e.run_pass(a.seed_guide,temp,a.max_reroutes)
    report={'version':'0.16b-guarded-maze-experiment','initial':initial,'final':e.snapshot(),'stats':dict(e.stats),'passes':[result],'validation':validate(d,temp),'interface_gate':check_tsv_geometry(a.seed_guide,temp),'seconds':time.monotonic()-start,'official_score':None}
    assert report['final']['route_cost_dbu']<=initial['route_cost_dbu']+1e-6
    temp.replace(a.output);a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':main()
