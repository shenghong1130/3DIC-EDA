"""Experimental input-only spare-track margin around fixed PIN/OBS occupancy.

Keep one track spare only when movable demand shares a cell with fixed demand.
Fixed-only cells never acquire fictitious overflow. This is a conservative
search objective; its numerical value is not an official congestion score.
"""
import argparse,json,time
from pathlib import Path
from layer_escape import LayerEscape
from design import Design
from validate import validate


class BufferedEscape(LayerEscape):
    def __init__(self,d,budget=100000,reserve=1):
        super().__init__(d,budget);self.fixed=dict(self.r.usage);self.reserve=reserve

    def phi(self,key,amount):
        cap=self.r.capacity(key);fixed=self.fixed.get(key,0)
        margin=self.reserve if fixed and amount>fixed else 0
        return (max(0,amount+margin-cap)/cap)**2 if cap>0 else (10000 if amount else 0)

    def candidates(self,name,points,center,rects,old_counts):
        layers={k//self.r.cells for k,n in old_counts.items() if self.phi(k,self.r.usage.get(k,0)+n)>self.phi(k,self.r.usage.get(k,0))}
        layers=sorted(l for l in layers if l+2 in self.d.layers and (l-1)//10==(l+1)//10 and l not in (9,10))
        for layer in layers:
            try:yield self.lift(name,rects,layer)
            except ValueError:continue
        if len(layers)>1:
            try:
                candidate=rects
                for layer in layers:candidate=self.lift(name,candidate,layer)
                yield candidate
            except ValueError:return


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--def',dest='def_file',type=Path,required=True)
    for key in ('lef','seed-guide','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--max-reroutes',type=int,default=100000);a=p.parse_args()
    if a.output.exists():p.error('Output exists')
    start=time.monotonic();d=Design(a.def_file,a.lef);validate(d,a.seed_guide)
    e=BufferedEscape(d,a.max_reroutes);initial=e.initialize(a.seed_guide)
    print('INITIAL',json.dumps(initial),flush=True);a.output.parent.mkdir(parents=True,exist_ok=True)
    result=e.run_pass(a.seed_guide,a.output,a.max_reroutes)
    report=dict(version='0.12-buffered-experiment',initial=initial,final=e.snapshot(),passes=[result],stats=dict(e.stats),validation=validate(d,a.output),seconds=time.monotonic()-start,official_score=None,reserve_tracks=1)
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':main()
