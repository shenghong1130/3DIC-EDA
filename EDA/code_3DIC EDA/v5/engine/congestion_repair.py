"""Input-only late congestion repair using the October-31 observed penalty.

The official CSVs reproduce sum((overflow/capacity)**2), with no 0.1 factor.
Resource demand is still a local estimate. Never read official artifacts here.
"""
import argparse,heapq,json,math,time
from pathlib import Path
from design import Design
from maze_wire import MazeWireShortener
from validate import read_guide,check_net,validate
from guide_rules import tsv_candidates
from guide_invariants import check_tsv_geometry


class CongestionRepair(MazeWireShortener):
    def __init__(self,d,budget=12000):
        super().__init__(d,budget,256,0,2)

    def phi(self,key,amount):
        cap=self.r.capacity(key)
        return (max(0,amount-cap)/cap)**2 if cap>0 else (10000 if amount else 0)

    def objective(self,cost,penalty):
        return 45*min(1,self.wire_reference/max(1,cost))+30/math.sqrt(1+max(0,penalty))

    def targets(self,guide,limit):
        heap=[]
        for name,rs in read_guide(guide):
            pts=self.d.points(name)
            if len(pts)<2 or len(pts)>self.max_pins:continue
            _,counts,_=self.counts(rs,pts,name)
            risk=math.fsum(self.phi(k,self.r.usage.get(k,0))-self.phi(k,max(0,self.r.usage.get(k,0)-n)) for k,n in counts.items())
            if risk<=1e-15:continue
            item=(risk,name)
            if len(heap)<self.budget:heapq.heappush(heap,item)
            elif item>heap[0]:heapq.heapreplace(heap,item)
        return {name for _,name in heap}

    def candidates(self,name,points,center,rects,old_counts):
        # Include ordinary layer/topology options as well as bounded maze paths.
        from wire_shortener import WireShortener
        yield from WireShortener.candidates(self,name,points,center,rects,old_counts)
        yield from super().candidates(name,points,center,rects,old_counts)

    def reroute(self,name,rects):
        pts=self.d.points(name);old_cost,old_counts,center=self.counts(rects,pts,name)
        self.update(old_counts,-1);self.increment_cache={}
        base=max(0,self.penalty-self.increment(old_counts))
        best=(rects,old_cost,old_counts,self.penalty)
        objective=self.objective(self.route_cost,self.penalty)
        original=tsv_candidates(rects);seen=set()
        for rs in self.candidates(name,pts,center,rects,old_counts):
            signature=tuple(rs)
            if signature in seen:continue
            seen.add(signature)
            if tsv_candidates(rs)!=original:continue
            try:cost,counts,actual=self.counts(rs,pts,name)
            except ValueError:continue
            if actual!=center:continue
            penalty=base+self.increment(counts)
            # Never trade away congestion in a congestion-repair pass.
            if penalty>self.penalty+1e-10:continue
            trial=self.objective(self.route_cost-old_cost+cost,penalty)
            if trial<=objective+1e-10:continue
            try:check_net(self.d,name,rs)
            except ValueError:continue
            best=rs,cost,counts,penalty;objective=trial
        chosen,cost,counts,penalty=best
        self.update(counts,1);self.increment_cache=None
        self.route_cost+=cost-old_cost;self.penalty=penalty;self.non_tsv_penalty=penalty
        self.stats['attempted']+=1;self.stats['accepted']+=chosen!=rects
        return chosen


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--def',dest='def_file',type=Path,required=True)
    for key in ('lef','seed-guide','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--max-reroutes',type=int,default=12000);a=p.parse_args()
    if a.output.exists():p.error('Output exists')
    started=time.monotonic();d=Design(a.def_file,a.lef);validate(d,a.seed_guide)
    engine=CongestionRepair(d,a.max_reroutes);initial=engine.initialize(a.seed_guide)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    result=engine.run_pass(a.seed_guide,a.output,a.max_reroutes)
    report={'version':'0.10-congestion-repair','initial':initial,'final':engine.snapshot(),
            'passes':[result],'stats':dict(engine.stats),'validation':validate(d,a.output),
            'interface_gate':check_tsv_geometry(a.seed_guide,a.output),'seconds':time.monotonic()-started,
            'penalty_formula':'SUM((overflow/capacity)**2), observed official CSV 2026-10-31',
            'official_score':None,'resource_model_parity':'NOT_ESTABLISHED'}
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':main()
