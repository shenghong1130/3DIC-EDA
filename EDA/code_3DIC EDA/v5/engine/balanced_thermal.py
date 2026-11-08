"""Input-only joint optimization with the observed latest congestion penalty.

Permit a wire/congestion/heat tradeoff only when the full surrogate improves;
retain hard geometric legality. A final congestion-repair pass follows.
"""
import argparse,heapq,json,math,time
from pathlib import Path
from design import Design
from joint_thermal import JointThermal
from routing_thermal import ThermalPreference
from validate import read_guide,check_net,validate


class BalancedThermal(JointThermal):
    def __init__(self,d,heat,budget=16000):
        super().__init__(d,heat,budget)
        self.candidate_limit=6

    def priority_centers(self,points,global_hot):
        return global_hot

    def initialize(self,guide):
        super().initialize(guide)
        # Coldest-first selection can spend the whole budget on nets far from
        # every hotspot. Rank the attainable heat benefit against the added
        # terminal-envelope distance instead, using only the current inputs.
        heap=[];baseline=self.thermal.score()
        derivative=45*min(self.reference,self.wire)/max(1,self.wire)**2
        hot=list(dict.fromkeys(self.router.heat.hot))[:32]
        for name,old_cell in self.cross_records:
            points=self.d.points(name);old=self.router.grid.center(*old_cell)
            groups=[[p for p in points if (p.layer-1)//10==tier] for tier in (0,1)]
            boxes=[(min(p.x for p in group),min(p.y for p in group),max(p.x for p in group),max(p.y for p in group)) for group in groups if group]
            def extension(xy):
                return sum(max(0,b[0]-xy[0],xy[0]-b[2])+max(0,b[1]-xy[1],xy[1]-b[3]) for b in boxes)
            old_length=extension(old);best=-float('inf')
            for xy in self.priority_centers(points,hot):
                cell=self.router.grid.cell(*xy)
                if cell==old_cell:continue
                covariance=self.thermal.covariance+self.thermal.value(cell)-self.thermal.value(old_cell)
                squares=self.thermal.squares+2*(self.router.tsv_usage[cell]-self.router.tsv_usage[old_cell]+1)
                benefit=.25*(self.thermal.score(covariance,squares)-baseline)
                wire=max(0,extension(xy)-old_length)/self.r.step
                best=max(best,benefit-derivative*wire)
            item=(best,name)
            if len(heap)<self.max_reroutes:heapq.heappush(heap,item)
            elif item>heap[0]:heapq.heapreplace(heap,item)
        self.targets={name for _,name in heap}

    def phi(self,key,amount):
        cap=self.r.capacity(key)
        return (max(0,amount-cap)/cap)**2 if cap>0 else (10000 if amount else 0)

    def objective(self,wire,penalty,thermal):
        return 45*min(1,self.reference/max(1,wire))+30/math.sqrt(1+max(0,penalty))+.25*thermal

    def shared_build(self,points,center):
        self.shared.net_name=self.router.net_name;best=None
        # This compares paths to the same TSV, so the heat term is constant.
        wire_price=45*min(self.reference,self.wire)/max(1,self.wire)**2
        ratio=15/(1+max(0,self.penalty))**1.5/max(wire_price,1e-12)
        for shift in (0,1):
            self.shared.layer_shift=shift
            for mode in ('msthv','mstvh','steinerhv','steinervh','htrunk','vtrunk'):
                self.shared.mode=mode
                try:
                    rs=self.shared._build(points,center);check_net(self.d,self.router.net_name,rs)
                    wire,counts,_=self.counts(self.router.net_name,rs)
                except ValueError:continue
                delta=math.fsum(self.phi(k,self.r.usage.get(k,0)+n)-self.phi(k,self.r.usage.get(k,0)) for k,n in counts.items())
                key=(wire+ratio*delta,wire,shift,mode)
                if best is None or key<best[0]:best=(key,rs)
        if best is None:raise ValueError('No legal balanced TSV topology')
        return best[1]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--def',dest='def_file',type=Path,required=True)
    for key in ('lef','thermal-report','seed-guide','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--max-reroutes',type=int,default=16000);a=p.parse_args()
    if a.output.exists():p.error('Output exists')
    start=time.monotonic();d=Design(a.def_file,a.lef);validate(d,a.seed_guide)
    heat=ThermalPreference(d,a.thermal_report,2,'region');engine=BalancedThermal(d,heat,a.max_reroutes)
    engine.initialize(a.seed_guide);before=engine.snapshot()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('w',encoding='utf-8',newline='\n') as f:
        for i,(name,rs) in enumerate(read_guide(a.seed_guide),1):
            rs=engine.reroute(name,rs);f.write(name+'\n(\n')
            for x,y,xx,yy,l in rs:f.write(f'{x} {y} {xx} {yy} {d.layers[l]["name"]}\n')
            f.write(')\n')
            if i%20000==0:print('BALANCED',i,dict(engine.stats),flush=True)
    report={'version':'0.11-balanced','initial':before,'final':engine.snapshot(),
            'stats':dict(engine.stats),'validation':validate(d,a.output),'seconds':time.monotonic()-start,
            'gain_from_input':engine.gain,'official_score':None,'model_parity':'NOT_ESTABLISHED'}
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':main()
