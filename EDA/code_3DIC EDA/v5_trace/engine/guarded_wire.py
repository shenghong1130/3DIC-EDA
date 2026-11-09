"""Shorten routes while preventing any new per-cell resource risk.

Input-only spare-track guards preserve fixed PIN/OBS headroom. Alternative
physical PIN rectangles/ports may be selected, subject to full geometry checks.
TSV candidates and their actual landing stay fixed throughout this pass.
"""
import argparse,json,math,time
from pathlib import Path
from wire_shortener import WireShortener
from validate import validate
from guide_invariants import check_tsv_geometry
from design import Design,Point


class GuardedWireShortener(WireShortener):
    def __init__(self,d,budget=2000,max_pins=64):
        super().__init__(d,budget,max_pins,0)
        self.fixed=dict(self.r.usage)

    def phi(self,key,amount):
        cap=self.r.capacity(key);fixed=self.fixed.get(key,0)
        reserve=1 if fixed and amount>fixed else 0
        return (max(0,amount+reserve-cap)/cap)**2 if cap>0 else (10000 if amount else 0)

    def safe_counts(self,counts,old_counts):
        for key,n in counts.items():
            amount=self.r.usage.get(key,0)
            if self.phi(key,amount+n)>self.phi(key,amount+old_counts.get(key,0))+1e-12:return False
        return True

    def candidates(self,name,points,center,rects,old_counts):
        seen=set()
        canonical={Point(*self.topology.grid.point_center(p.x,p.y),p.layer) for p in points}
        for ordinary in super().candidates(name,points,center,rects,old_counts):
            proposals=[ordinary]
            # Each yielded parent candidate leaves the router configured with
            # that candidate's layer pair and topology. Reuse both when choosing
            # an actual PIN port nearer to the rest of this input net.
            access=self.topology.access_points(name,center)
            if set(access)!=canonical:
                try:proposals.append(self.topology._build(access,center))
                except ValueError:self.stats['access_geometry_rejected']+=1
            for candidate in proposals:
                signature=tuple(candidate)
                if signature in seen:continue
                seen.add(signature)
                try:_,counts,_=self.counts(candidate,points,name)
                except ValueError:continue
                if not self.safe_counts(counts,old_counts):
                    self.stats['new_cell_risk_rejected']+=1;continue
                yield candidate


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--def',dest='def_file',type=Path,required=True)
    for key in ('lef','seed-guide','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--max-reroutes',type=int,default=2000);p.add_argument('--max-pins',type=int,default=64);a=p.parse_args()
    if a.output.exists():p.error('Output exists')
    start=time.monotonic();d=Design(a.def_file,a.lef);validate(d,a.seed_guide)
    e=GuardedWireShortener(d,a.max_reroutes,a.max_pins);initial=e.initialize(a.seed_guide)
    print('INITIAL',json.dumps(initial),flush=True);a.output.parent.mkdir(parents=True,exist_ok=True)
    result=e.run_pass(a.seed_guide,a.output,a.max_reroutes)
    report=dict(version='0.13-guarded-wire',initial=initial,final=e.snapshot(),stats=dict(e.stats),passes=[result],validation=validate(d,a.output),interface_gate=check_tsv_geometry(a.seed_guide,a.output),seconds=time.monotonic()-start,official_score=None)
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':main()
