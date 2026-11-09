"""Input-only bounded 3D tree sharing for medium/high fanout nets."""
import argparse,json,math,time
from functools import lru_cache
from pathlib import Path
from design import Design
from wire_shortener import WireShortener
from guide_rules import tsv_candidates
from validate import validate
from guide_invariants import check_tsv_geometry


class MazeWireShortener(WireShortener):
    def __init__(self,d,budget=256,max_pins=256,congestion_slack=1.0,min_pins=9,max_states=12000):
        super().__init__(d,budget,max_pins,congestion_slack)
        self.min_pins=min_pins;self.maze.max_states=max_states
        self.maze.lower_interface_routing=False;self.maze.root_at_tsv=True
        self.maze.via_penalty=3;self.maze.heuristic_weight=1.05

    def candidates(self,name,points,center,rects,old_counts):
        exempt=self.r.pin_cells(name)
        wire_price=45*self.wire_reference/max(1,self.route_cost)**2
        ratio=(15/(1+self.non_tsv_penalty)**1.5)/max(wire_price,1e-12)
        @lru_cache(maxsize=None)
        def marginal(key):
            if key in exempt:return 1.0
            amount=self.r.usage.get(key,0)
            if key in self.protected_tsv_keys and not old_counts.get(key):return 1e10
            return 1+ratio*(self.phi(key,amount+1)-self.phi(key,amount))
        boxes=tsv_candidates(rects)
        self.maze.interface_routing=len(boxes)<=1
        self.maze.allowed_tsv_cells={self.router.grid.cell((b[0]+b[2])/2,(b[1]+b[3])/2) for b in boxes}
        self.maze.marginal_price=marginal;self.maze.exempt=exempt
        self.maze.terminal_access=None
        try:
            for order in ('prim','far'):
                self.maze.terminal_order=order
                try:yield self.maze._build(points,center)
                except ValueError:self.stats['maze_exhausted']+=1
        finally:
            self.maze.marginal_price=None;marginal.cache_clear()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--def',dest='def_file',type=Path,required=True)
    for name in ('lef','seed-guide','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--max-reroutes',type=int,default=256);p.add_argument('--max-pins',type=int,default=256)
    p.add_argument('--min-pins',type=int,default=9);p.add_argument('--congestion-slack',type=float,default=1.0)
    a=p.parse_args()
    if a.output.exists():p.error('Output exists')
    if a.max_reroutes<1 or a.min_pins<2 or not 0<=a.congestion_slack<=5:p.error('Invalid search settings')
    started=time.monotonic();d=Design(a.def_file,a.lef)
    engine=MazeWireShortener(d,a.max_reroutes,a.max_pins,a.congestion_slack,a.min_pins)
    initial=engine.initialize(a.seed_guide);print('Baseline',json.dumps(initial),flush=True)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    result=engine.run_pass(a.seed_guide,a.output,1000000)
    report=dict(version='0.8-input-only-maze-wire',initial=initial,final=engine.snapshot(),stats=dict(engine.stats),
                passes=[result],validation=validate(d,a.output),interface_gate=check_tsv_geometry(a.seed_guide,a.output),
                seconds=time.monotonic()-started,official_score=None)
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)

if __name__=='__main__':main()
