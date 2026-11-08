"""Bounded maze repair for crowded PIN-access neighborhoods.

Permit a preferred-direction step before changing layers, instead of forcing
every access via into the PIN GCell. Preserve TSV geometry and forbid per-cell
risk increases. A global half-percent wire budget limits conservative pricing.
"""
import argparse,json,time
from pathlib import Path
from maze_wire import MazeWireShortener
from congestion_repair import CongestionRepair
from validate import validate
from guide_invariants import check_tsv_geometry
from design import Design


class PinEscape(MazeWireShortener):
    def __init__(self,d,budget=256):
        super().__init__(d,budget,64,0,2,8000);self.fixed=dict(self.r.usage);self.initial_wire=None

    def phi(self,key,amount):
        cap=self.r.capacity(key);fixed=self.fixed.get(key,0);reserve=1 if fixed and amount>fixed else 0
        return (max(0,amount+reserve-cap)/cap)**2 if cap>0 else (10000 if amount else 0)

    def initialize(self,guide):
        result=super().initialize(guide);self.initial_wire=self.route_cost;return result

    def objective(self,cost,penalty):
        import math
        return 45*min(1,self.reference/max(1,cost))+30/math.sqrt(1+max(0,penalty))

    targets=CongestionRepair.targets
    reroute=CongestionRepair.reroute

    def allowed(self,cost,counts,old_cost,old_counts):
        if self.route_cost-old_cost+cost>self.initial_wire*1.005+1e-9:return False
        for k,n in counts.items():
            amount=self.r.usage.get(k,0)
            if self.phi(k,amount+n)>self.phi(k,amount+old_counts.get(k,0))+1e-12:return False
        return True

    def candidates(self,name,points,center,rects,old_counts):
        old_cost=self.counts(rects,points,name)[0]
        for candidate in super().candidates(name,points,center,rects,old_counts):
            try:cost,counts,_=self.counts(candidate,points,name)
            except ValueError:continue
            if self.allowed(cost,counts,old_cost,old_counts):yield candidate
            else:self.stats['per_cell_or_wire_budget_rejected']+=1


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--def',dest='def_file',type=Path,required=True)
    for key in ('lef','seed-guide','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--max-reroutes',type=int,default=256);a=p.parse_args()
    if a.output.exists():p.error('Output exists')
    start=time.monotonic();d=Design(a.def_file,a.lef);validate(d,a.seed_guide);e=PinEscape(d,a.max_reroutes);initial=e.initialize(a.seed_guide);print('INITIAL',json.dumps(initial),flush=True)
    a.output.parent.mkdir(parents=True,exist_ok=True);temp=a.output.with_suffix('.tmp.guide');result=e.run_pass(a.seed_guide,temp,a.max_reroutes)
    report=dict(version='0.14-pin-access-escape-experiment',initial=initial,final=e.snapshot(),stats=dict(e.stats),passes=[result],validation=validate(d,temp),interface_gate=check_tsv_geometry(a.seed_guide,temp),seconds=time.monotonic()-start,wire_increase_cap_fraction=.005,official_score=None)
    temp.replace(a.output);a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':main()
