"""Input-only thermal preference for legal TSV candidate selection (standard library)."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
import json
from pathlib import Path
from routing_resources import lines


class ThermalPreference:
    def __init__(self,design,path,weight,policy='sample'):
        self.d,self.weight=design,weight
        self.policy=policy
        payload=json.loads(Path(path).read_text(encoding='utf-8'))
        self.ambient=float(payload.get('meta',{}).get('ambientTemp',0.0))
        xy=defaultdict(lambda:[0.0,0])
        for x,y,_z,temp,*_ in payload['nodes']:
            key=(round(float(x),9),round(float(y),9))
            xy[key][0]+=float(temp)
            xy[key][1]+=1
        self.x=lines(design.grid['X'],design.die[0],design.die[2])
        self.y=lines(design.grid['Y'],design.die[1],design.die[3])
        self.cols,self.rows=len(self.x)-1,len(self.y)-1
        grouped=defaultdict(lambda:[0.0,0])
        physical=[]
        for (x,y),(total,count) in xy.items():
            px=design.die[0]+x*design.dbu
            py=design.die[1]+y*design.dbu
            if not (self.x[0]<=px<=self.x[-1] and self.y[0]<=py<=self.y[-1]):
                continue
            col=min(self.cols-1,bisect_right(self.x,px)-1)
            row=min(self.rows-1,bisect_right(self.y,py)-1)
            grouped[row,col][0]+=total/count
            grouped[row,col][1]+=1
            physical.append((px,py))
        self.temperatures={key:total/count for key,(total,count) in grouped.items()}
        self.sample_rows=sorted({r for r,c in self.temperatures})
        self.sample_cols=sorted({c for r,c in self.temperatures})
        self.sample_x=sorted({p[0] for p in physical})
        self.sample_y=sorted({p[1] for p in physical})
        self.min_temp=min(self.temperatures.values())
        self.range=max(self.temperatures.values())-self.min_temp
        self.counts=defaultdict(int)
        self.hot=sorted(physical,key=lambda p:self.temperature(*p),reverse=True)[:64]

    def cell(self,x,y):
        return (min(self.rows-1,max(0,bisect_right(self.y,y)-1)),
                min(self.cols-1,max(0,bisect_right(self.x,x)-1)))

    def temperature(self,x,y):
        row,col=self.cell(x,y)
        if (row,col) in self.temperatures:
            return self.temperatures[row,col]
        r=self.sample_rows[max(0,bisect_right(self.sample_rows,row)-1)]
        c=self.sample_cols[max(0,bisect_right(self.sample_cols,col)-1)]
        return self.temperatures.get((r,c),self.min_temp)

    def candidates(self,points):
        lowx,highx=min(p.x for p in points),max(p.x for p in points)
        lowy,highy=min(p.y for p in points),max(p.y for p in points)
        padding=2*max(self.d.grid['X'][0][2],self.d.grid['Y'][0][2])
        lowx-=padding;highx+=padding;lowy-=padding;highy+=padding
        def samples(values,low,high):
            result=set()
            for target in (low,(low+high)/2,high):
                i=bisect_left(values,target)
                for j in (i-1,i):
                    if 0<=j<len(values) and low<=values[j]<=high:
                        result.add(values[j])
            return result
        xs=samples(self.sample_x,lowx,highx)
        ys=samples(self.sample_y,lowy,highy)
        candidates=[(x,y) for x in xs for y in ys]
        candidates.extend((x,y) for x,y in self.hot if lowx<=x<=highx and lowy<=y<=highy)
        candidates=sorted(set(candidates),key=lambda p:(-self.temperature(*p),p))[:8]
        if self.policy=='sample':
            return candidates
        # A coarse temperature sample represents a whole filled GCell region.
        # Pick nearby fine-grid locations in that region, rather than forcing
        # many TSVs onto the same original thermal sample coordinate.
        centerx=(lowx+highx)/2
        centery=(lowy+highy)/2
        stepx=self.d.grid['X'][0][2]
        stepy=self.d.grid['Y'][0][2]
        result=set()
        for sx,sy in candidates[:4]:
            row,col=self.cell(sx,sy)
            ri=bisect_right(self.sample_rows,row)-1
            ci=bisect_right(self.sample_cols,col)-1
            lr=self.sample_rows[max(0,ri)]
            lc=self.sample_cols[max(0,ci)]
            hr=self.sample_rows[ri+1] if ri+1<len(self.sample_rows) else self.rows
            hc=self.sample_cols[ci+1] if ci+1<len(self.sample_cols) else self.cols
            xlo=max(self.d.die[0],self.x[lc])
            xhi=min(self.d.die[2],self.x[hc])
            ylo=max(self.d.die[1],self.y[lr])
            yhi=min(self.d.die[3],self.y[hr])
            if xhi<=xlo or yhi<=ylo:
                continue
            x=max(xlo+stepx/2,min(xhi-stepx/2,centerx))
            y=max(ylo+stepy/2,min(yhi-stepy/2,centery))
            for dx in (-stepx,0,stepx):
                for dy in (-stepy,0,stepy):
                    px,py=x+dx,y+dy
                    if xlo<px<xhi and ylo<py<yhi:
                        result.add((px,py))
        return sorted(result)

    def choose(self,centers,points):
        return self.rank(centers,points)[0]

    def rank(self,centers,points):
        """Same sequence as repeated choice, caching geometry and heat lookups."""
        geometry={p:sum(abs(q.x-p[0])+abs(q.y-p[1]) for q in points) for p in centers}
        bias={}
        for p in geometry:
            heat=(self.temperature(*p)-self.min_temp)/self.range if self.range>0 else 0
            density=self.counts[self.cell(*p)]
            bias[p]=(heat,density)
        remaining=set(geometry);result=[]
        while remaining:
            scale=max(min(geometry[p] for p in remaining),4*max(self.d.grid['X'][0][2],self.d.grid['Y'][0][2]))
            # Preserve the old left-to-right float expression for stable ties.
            def key(p):
                heat,density=bias[p]
                return (geometry[p]/scale-self.weight*heat+0.03*self.weight*density,geometry[p],p)
            best=min(remaining,key=key)
            result.append(best);remaining.remove(best)
        return result

    def commit(self,center):
        self.counts[self.cell(*center)]+=1
