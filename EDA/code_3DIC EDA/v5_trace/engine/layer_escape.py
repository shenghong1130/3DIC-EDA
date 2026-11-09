"""Lift congested corridors two parallel layers, retaining every lower contact.

Uses only input geometry and current demand. TSV boxes stay unchanged and all
accepted candidates pass full single-net connectivity and landing checks.
"""
import argparse,json,time
from pathlib import Path
from congestion_repair import CongestionRepair
from adaptive_router import merge_corridors
from validate import overlaps,validate
from design import Design


class LayerEscape(CongestionRepair):
    def lift(self,name,rects,layer):
        target=layer+2
        if target not in self.d.layers or (layer-1)//10!=(target-1)//10:
            raise ValueError('Cannot lift across an interface')
        if self.d.layers[layer]['direction']!=self.d.layers[target]['direction']:
            raise ValueError('Nonparallel lift')
        old=[r for r in rects if r[4]==layer]
        result=[(*r[:4],target if r[4]==layer else r[4]) for r in rects]
        # Original upper-layer contacts already touch the lifted corridor.
        # Lower contacts need the two missing layers at the old intersection.
        for a in old:
            for b in rects:
                if b[4]!=layer-1 or not overlaps(a,b,True):continue
                box=(max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3]))
                result.extend((*box,l) for l in (layer,layer+1))
        for shapes in self.d.net_pin_shapes(name):
            if any(l!=layer and any(r[4]==l and overlaps(r,box,True) for r in result) for l,box in shapes):continue
            contact=None
            for l,box in shapes:
                if l!=layer:continue
                for r in old:
                    if overlaps(r,box,True):
                        contact=((max(r[0],box[0])+min(r[2],box[2]))/2,(max(r[1],box[1])+min(r[3],box[3]))/2)
                        break
                if contact is not None:break
            if contact is None:continue
            result.extend(self.router.grid.rectangle(*contact,*contact,l) for l in (layer,layer+1))
        return merge_corridors(result,self.d.layers)

    def candidates(self,name,points,center,rects,old_counts):
        layers={k//self.r.cells for k in old_counts if self.r.usage.get(k,0)+old_counts[k]>self.r.capacity(k)}
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
    e=LayerEscape(d,a.max_reroutes);initial=e.initialize(a.seed_guide)
    print('INITIAL',json.dumps(initial),flush=True);a.output.parent.mkdir(parents=True,exist_ok=True)
    result=e.run_pass(a.seed_guide,a.output,a.max_reroutes)
    report=dict(version='0.12-interface-bundle-escape',initial=initial,final=e.snapshot(),passes=[result],stats=dict(e.stats),validation=validate(d,a.output),seconds=time.monotonic()-start,official_score=None)
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':main()
