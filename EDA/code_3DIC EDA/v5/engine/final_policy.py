"""Input-derived bounded wire allowance for spare-track congestion repair."""
import math


def shared_maze_budget(net_count):
    return min(8000,max(2048,math.ceil(net_count*.04)))


def buffered_wire_allowance(penalty):
    severity=1-1/math.sqrt(1+max(0,penalty))
    return min(.08,max(.01,.08*severity))


def accept_buffered(report):
    a,b=report['initial'],report['final'];allowance=buffered_wire_allowance(a['congestion_penalty_model'])
    return (a['congestion_penalty_model']>1 and b['route_cost_dbu']<=a['route_cost_dbu']*(1+allowance)
            and b['congestion_penalty_model']<a['congestion_penalty_model']-1e-9
            and b['wire_congestion_objective_PROXY']>a['wire_congestion_objective_PROXY']+1e-9)
