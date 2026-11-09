"""Resolve colliding TSV landing bundles without case-specific parameters."""
import argparse,json,time
from pathlib import Path
from balanced_thermal import BalancedThermal
from joint_thermal import JointThermal
from design import Design
from routing_thermal import ThermalPreference
from validate import validate,read_guide


class LandingRepair(BalancedThermal):
    def __init__(self,d,heat,budget=2000):
        super().__init__(d,heat,budget)
        self.full_tsv_price=True;self.router.full_tsv_price=True
        self.candidate_limit=12

    def initialize(self,guide):
        JointThermal.initialize(self,guide)
        collisions=sorted(((-self.router.tsv_usage[cell],name) for name,cell in self.cross_records if self.router.tsv_usage[cell]>1))
        self.targets={name for _,name in collisions[:self.max_reroutes]}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--def',dest='def_file',type=Path,required=True)
    for key in ('lef','thermal-report','seed-guide','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():p.error('Output exists')
    start=time.monotonic();d=Design(a.def_file,a.lef);validate(d,a.seed_guide)
    e=LandingRepair(d,ThermalPreference(d,a.thermal_report,2,'region'));e.initialize(a.seed_guide);initial=e.snapshot()
    print('INITIAL',json.dumps(initial),'targets',len(e.targets),flush=True);a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('w',encoding='utf8',newline='\n') as f:
        for name,rs in read_guide(a.seed_guide):
            rs=e.reroute(name,rs);f.write(name+'\n(\n')
            for x,y,xx,yy,l in rs:f.write(f'{x} {y} {xx} {yy} {d.layers[l]["name"]}\n')
            f.write(')\n')
    report=dict(version='0.12-landing-repair',initial=initial,final=e.snapshot(),stats=dict(e.stats),validation=validate(d,a.output),seconds=time.monotonic()-start,official_score=None)
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':main()
