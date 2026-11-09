"""Input-only TSV relocation preserving the existing shared routing tree.

Move the vertical connection and add short same-die bridges. Unlike rebuilding
the entire net, this retains wire sharing obtained by the maze optimizer.
Every accepted proposal passes hard geometry/PIN/TSV obstacle validation.
"""
import route_trace as _trace
import argparse,json,math,time
from pathlib import Path
from design import Design,Point
from joint_thermal import JointThermal
from guide_rules import select_tsv,grid_for
from routing_thermal import ThermalPreference
from validate import read_guide,validate,check_net
from grid_router import GridRouter
from adaptive_router import merge_corridors
from guide_cost import cost_adapter
from native_resources import slot_capacity


def bridge_pair(d,tier,access):
    # Bridge beside the interface, avoiding long via stacks down to the lowest
    # routing pair. Ordinary bridges must not run along M10/M11 themselves.
    allowed=[l for l in d.layers if (l-1)//10==tier and l not in (10,11)]
    pairs=[(h,v) for h in allowed for v in allowed if abs(h-v)==1
           and d.layers[h]['direction']=='HORIZONTAL' and d.layers[v]['direction']=='VERTICAL']
    if not pairs:raise ValueError('No same-die bridge layer pair')
    return min(pairs,key=lambda p:(abs(p[0]-access)+abs(p[1]-access),p))


def move_patch(d,name,rects,center,pattern='hv'):
    grid=grid_for(d);pins=[s for pin in d.net_pin_shapes(name) for s in pin]
    box=select_tsv(rects,pins)
    old=((box[0]+box[2])/2,(box[1]+box[3])/2)
    if old==center:return rects
    r=cost_adapter(d);col,row=grid.cell(*old)
    # One actual TSV cell, excluded from lower interface. Recompress the
    # remaining portions of any M10 corridor instead of deleting PIN accesses.
    result=[]
    for rs in rects:
        if rs[4]!=10 or not (rs[0]<=old[0]<rs[2] and rs[1]<=old[1]<rs[3]):
            result.append(rs);continue
        horizontal=d.layers[10]['direction']=='HORIZONTAL'
        if horizontal:
            if rs[0]<grid.x[col]:result.append((rs[0],rs[1],grid.x[col],rs[3],10))
            if grid.x[col+1]<rs[2]:result.append((grid.x[col+1],rs[1],rs[2],rs[3],10))
        else:
            if rs[1]<grid.y[row]:result.append((rs[0],rs[1],rs[2],grid.y[row],10))
            if grid.y[row+1]<rs[3]:result.append((rs[0],grid.y[row+1],rs[2],rs[3],10))
    router=GridRouter(d);router.path_pattern=pattern
    additions=set()
    for tier,access in ((0,9),(1,12)):
        pair=bridge_pair(d,tier,access)
        router.connect(additions,Point(*old,access),Point(*center,access),pair)
    router.stack(additions,*center,9,10)
    router.stack(additions,*center,11,12)
    router.stack(additions,*old,11,12)
    return merge_corridors(result+list(additions),d.layers)


class ThermalPatch(JointThermal):
    def __init__(self,d,heat,budget=16000):
        super().__init__(d,heat,budget)
        self.original=None;self.active_name=None

    def objective(self,wire,penalty,thermal):
        # Normalized thermal objective, legacy demand remains an estimate.
        if penalty>self.penalty+1e-10:return -float('inf')
        return 45*min(1,self.reference/max(wire,1))+30/math.sqrt(1+max(0,penalty))+0.25*thermal

    def shared_build(self,points,center):
        best=None
        for pattern in ('hv','vh'):
            try:
                rs=move_patch(self.d,self.active_name,self.original,center,pattern)
                check_net(self.d,self.active_name,rs)
                wire,counts,_=self.counts(self.active_name,rs)
            except ValueError:continue
            delta=math.fsum(self.phi(k,self.r.usage.get(k,0)+n)-self.phi(k,self.r.usage.get(k,0)) for k,n in counts.items())
            score=wire+max(1,self.wire/3)*delta
            if best is None or score<best[0]:best=(score,rs)
        if best is None:raise ValueError('No valid TSV bridge')
        return best[1]

    @_trace.observe_choice
    def reroute(self,name,rects):
        self.original=rects;self.active_name=name
        return super().reroute(name,rects)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--def',dest='def_file',type=Path,required=True)
    for key in ('lef','thermal-report','seed-guide','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--max-reroutes',type=int,default=16000);a=p.parse_args()
    if a.output.exists():p.error('Output exists')
    started=time.monotonic();d=Design(a.def_file,a.lef);validate(d,a.seed_guide)
    heat=ThermalPreference(d,a.thermal_report,2,'region');engine=ThermalPatch(d,heat,a.max_reroutes)
    engine.initialize(a.seed_guide);before=engine.snapshot()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('w',encoding='utf-8',newline='\n') as f:
        for index,(name,rs) in enumerate(read_guide(a.seed_guide),1):
            rs=engine.reroute(name,rs)
            f.write(name+'\n(\n')
            for x,y,xx,yy,l in rs:f.write(f'{x} {y} {xx} {yy} {d.layers[l]["name"]}\n')
            f.write(')\n')
            if index%20000==0:print('PATCH',index,dict(engine.stats),flush=True)
    report={'version':'0.11-thermal-patch','initial':before,'final':engine.snapshot(),
            'stats':dict(engine.stats),'validation':validate(d,a.output),'seconds':time.monotonic()-started,
            'gain_from_input':engine.gain,'official_score':None,'model_parity':'NOT_ESTABLISHED'}
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':main()
