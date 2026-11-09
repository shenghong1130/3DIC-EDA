"""Geometry required by official Q100-Q110; no inferred resource policies."""
from bisect import bisect_right

QA_SOURCE='https://docs.qq.com/doc/DQlZueVFKeXhTbkVZ'
QA_RULES='official-Q62-Q100-Q101-Q102-Q103-Q108-Q109-Q110'


class GCellGrid:
    def __init__(self,design):
        self.x=self.axis(design.grid['X'],design.die[0],design.die[2])
        self.y=self.axis(design.grid['Y'],design.die[1],design.die[3])
        self.x_index={v:i for i,v in enumerate(self.x)}
        self.y_index={v:i for i,v in enumerate(self.y)}

    @staticmethod
    def axis(statements,lower,upper):
        if not statements or lower>=upper:
            raise ValueError('Missing or invalid GCell grid')
        if any(count<2 or step<=0 for start,count,step in statements):
            raise ValueError('Invalid GCell declaration')
        return sorted({lower,upper}|{start+i*step for start,count,step in statements
                                    for i in range(count) if lower<start+i*step<upper})

    def cell(self,x,y):
        if not self.x[0]<=x<=self.x[-1] or not self.y[0]<=y<=self.y[-1]:
            raise ValueError('Access point outside DIEAREA')
        return (min(len(self.x)-2,bisect_right(self.x,x)-1),
                min(len(self.y)-2,bisect_right(self.y,y)-1))

    def center(self,col,row):
        return ((self.x[col]+self.x[col+1])/2,(self.y[row]+self.y[row+1])/2)

    def point_center(self,x,y):
        return self.center(*self.cell(x,y))

    def rectangle(self,x1,y1,x2,y2,layer):
        c0,r0=self.cell(x1,y1);c1,r1=self.cell(x2,y2)
        return (self.x[min(c0,c1)],self.y[min(r0,r1)],
                self.x[max(c0,c1)+1],self.y[max(r0,r1)+1],layer)


def grid_for(design):
    if not hasattr(design,'_guide_grid'):
        design._guide_grid=GCellGrid(design)
    return design._guide_grid


def positive_overlap(a,b):
    return max(a[0],b[0])<min(a[2],b[2]) and max(a[1],b[1])<min(a[3],b[3])


def tsv_candidates(rects):
    return sorted({(max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3]))
                   for a in rects if a[4]==10 for b in rects if b[4]==11 and positive_overlap(a,b)})


def select_tsv(rects,pin_shapes):
    candidates=tsv_candidates(rects)
    if not 1<=len(candidates)<=2:
        raise ValueError(f'Expected one or two TSV candidates, got {len(candidates)}')
    if len(candidates)==2:
        interface=[box for layer,box in pin_shapes if layer in (10,11)]
        free=[box for box in candidates if not any(positive_overlap(box,pin) for pin in interface)]
        if len(free)!=1:
            raise ValueError('Two TSV candidates must leave one non-PIN actual TSV (Q103)')
        return free[0]
    return candidates[0]
