"""Input-only joint TSV/Steiner refinement after wire sharing.

Thermal normalization uses a hot-cell reference sized by the number of TSVs,
never a case filename. It is an optimization heuristic, not an upper bound.
"""
import argparse,json,math,time
from pathlib import Path
from design import Design
from relocate import Relocator,ThermalMoments
from routing_thermal import ThermalPreference
from native_resources import native_counts
from wire_shortener import SharedTreeRouter
from validate import read_guide,check_net,validate


def normalized_gain(moments):
    heat=moments.heat
    cells=heat.rows*heat.cols
    count=min(moments.count,cells-1)
    if count<=0 or moments.norm<=1e-12:return 1.0
    bins=[]
    for i,row in enumerate(heat.sample_rows):
        height=(heat.sample_rows[i+1] if i+1<len(heat.sample_rows) else heat.rows)-(row if i else 0)
        for j,col in enumerate(heat.sample_cols):
            width=(heat.sample_cols[j+1] if j+1<len(heat.sample_cols) else heat.cols)-(col if j else 0)
            value=max(0.0,heat.temperatures.get((row,col),heat.min_temp)-moments.ambient)-moments.mean
            bins.append((value,width*height))
    remaining=count;numerator=0.0
    for value,area in sorted(bins,reverse=True):
        take=min(remaining,area);numerator+=take*value;remaining-=take
        if not remaining:break
    reference=numerator/(moments.norm*math.sqrt(count-count*count/cells))
    return math.atanh(0.9)/reference if reference>1e-12 else 1.0


class JointThermal(Relocator):
    def __init__(self,d,heat,budget=4000):
        super().__init__(d,heat,12,4,True,budget,False)
        self.shared=SharedTreeRouter(d);self.shared.interface_routing=False
        self.router._build=self.shared_build

    def counts(self,name,rects):
        wire,counts,center=native_counts(self.r,self.router.grid,rects,self.d.points(name),name)
        return wire,counts,self.router.grid.cell(*center) if center is not None else None

    def initialize(self,guide):
        super().initialize(guide)
        self.gain=normalized_gain(self.thermal)
        self.thermal.gain=self.gain

    def objective(self,wire,penalty,thermal):
        if penalty>self.penalty+1e-10:return -float('inf')
        return super().objective(wire,penalty,thermal)

    def shared_build(self,points,center):
        self.shared.net_name=self.router.net_name;best=None
        for shift in (0,1):
            self.shared.layer_shift=shift
            for mode in ('msthv','mstvh','steinerhv','steinervh','htrunk','vtrunk'):
                self.shared.mode=mode
                try:
                    rs=self.shared._build(points,center);check_net(self.d,self.router.net_name,rs)
                    wire,counts,_=self.counts(self.router.net_name,rs)
                except ValueError:continue
                delta=math.fsum(self.phi(k,self.r.usage.get(k,0)+n)-self.phi(k,self.r.usage.get(k,0)) for k,n in counts.items())
                # Demand is only priced here; exact global acceptance follows
                # in Relocator.reroute with wire, heat and connectivity checks.
                key=(wire+max(1,self.wire/3)*delta,wire,shift,mode)
                if best is None or key<best[0]:best=key,rs
        if best is None:raise ValueError('No valid shared TSV topology')
        return best[1]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--def',dest='def_file',type=Path,required=True)
    for name in ('lef','thermal-report','seed-guide','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--max-reroutes',type=int,default=4000);a=p.parse_args()
    if a.output.exists():p.error('Output exists')
    start=time.monotonic();d=Design(a.def_file,a.lef);validate(d,a.seed_guide)
    heat=ThermalPreference(d,a.thermal_report,2,'region');engine=JointThermal(d,heat,a.max_reroutes)
    engine.initialize(a.seed_guide);before=engine.snapshot()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('w',encoding='utf-8',newline='\n') as stream:
        for name,rs in read_guide(a.seed_guide):
            candidate=engine.reroute(name,rs)
            stream.write(name+'\n(\n')
            for x,y,xx,yy,layer in candidate:stream.write(f'{x} {y} {xx} {yy} {d.layers[layer]["name"]}\n')
            stream.write(')\n')
    report={'version':'0.9-joint-input-only','initial':before,'final':engine.snapshot(),
            'gain_from_input':engine.gain,'stats':dict(engine.stats),'validation':validate(d,a.output),
            'seconds':time.monotonic()-start,'official_score':None,'thermal_objective':'INPUT_NORMALIZED_HEURISTIC'}
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':main()
