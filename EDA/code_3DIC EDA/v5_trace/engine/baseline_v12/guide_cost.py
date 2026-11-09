"""Geometry-only adapter for the published GUIDE wirelength proxy."""
from routing_resources import RoutingResources
from guide_rules import grid_for


def cost_adapter(d):
    r=object.__new__(RoutingResources);r.d=d;grid=grid_for(d)
    r.x=grid.x;r.y=grid.y;r.cols=len(r.x)-1;r.rows=len(r.y)-1;r.cells=r.cols*r.rows
    r.stepx=d.grid['X'][0][2];r.stepy=d.grid['Y'][0][2];r.step=max(r.stepx,r.stepy)
    return r


