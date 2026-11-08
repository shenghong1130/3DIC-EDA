"""Conservative interface bundle model for the updated practice scorer.

Reserve the landing cell's usable tracks on both interface layers. The old
M11 limit inferred from M10 pitch understated current TSV demand. This remains
an input-only heuristic, particularly for GCells larger than the physical TSV.
Own-net credit is demand accounting only; hard PIN/OBS landing bans are separate.
"""
from collections import Counter
from guide_rules import select_tsv

def slot_capacity(resources,key):
    return resources.capacity(key)

def native_counts(resources,grid,rects,points,name):
    cost,used=resources.candidate(rects,0)
    pins=resources.pin_cells(name)
    counts=Counter(dict.fromkeys(used-pins,1));center=None
    if len({(p.layer-1)//10 for p in points})==2:
        box=select_tsv(rects,[shape for pin in resources.d.net_pin_shapes(name) for shape in pin])
        center=((box[0]+box[2])/2,(box[1]+box[3])/2)
        col,row=grid.cell(*center)
        lower=10*resources.cells+row*resources.cols+col
        upper=11*resources.cells+row*resources.cols+col
        for key in (lower,upper):
            if key not in pins:
                # Replace this net's ordinary one-track landing demand with a
                # whole bundle, without charging the guide twice.
                counts[key]+=max(0,slot_capacity(resources,key)-counts[key])
    return cost,counts,center
