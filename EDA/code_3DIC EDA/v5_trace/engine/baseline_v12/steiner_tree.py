"""Input-only sparse spanning trees and bounded Hanan-point candidates.

Large-net construction is a heuristic, not an exact Manhattan MST. Including
the former x-chain guarantees its graph-MST length is no worse than that chain.
"""
import route_trace as _trace
from collections import deque
from functools import lru_cache
from itertools import combinations
from statistics import median
from design import Point


def sparse_mst_edges(points, window=8):
    n=len(points)
    if n<2:return []
    candidates=set()
    for key in (lambda p:(p.x,p.y),lambda p:(p.y,p.x),
                lambda p:(p.x+p.y,p.x),lambda p:(p.x-p.y,p.y)):
        order=sorted(range(n),key=lambda i:(key(points[i]),i))
        for pos,i in enumerate(order):
            for j in order[pos+1:pos+1+window]:
                candidates.add((min(i,j),max(i,j)))
    edges=sorted((abs(points[i].x-points[j].x)+abs(points[i].y-points[j].y),i,j) for i,j in candidates)
    parent=list(range(n));size=[1]*n;graph=[[] for _ in points]
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    count=0
    for _,i,j in edges:
        a,b=find(i),find(j)
        if a==b:continue
        if size[a]<size[b]:a,b=b,a
        parent[b]=a;size[a]+=size[b];graph[i].append(j);graph[j].append(i);count+=1
        if count==n-1:break
    if count!=n-1:raise ValueError('Sparse candidate graph is disconnected')
    # Prefix-connected ordering is needed by tree-growing maze consumers.
    result=[];seen={0};queue=deque([0])
    while queue:
        i=queue.popleft()
        for j in sorted(graph[i]):
            if j not in seen:seen.add(j);queue.append(j);result.append((i,j))
    return result


@lru_cache(maxsize=32)
@_trace.observe_topology
def steiner_tree(points, layer, limit=8):
    from router import tree_edges
    edges=tree_edges(points)
    neighbors=[[] for _ in points]
    for i,j in edges:neighbors[i].append(j);neighbors[j].append(i)
    present={(p.x,p.y) for p in points};rank={}
    for i,adj in enumerate(neighbors):
        adj=sorted(adj,key=lambda j:(abs(points[j].x-points[i].x)+abs(points[j].y-points[i].y),j))[:8]
        for j,k in combinations(adj,2):
            group=[points[t] for t in (i,j,k)]
            x,y=median(p.x for p in group),median(p.y for p in group)
            old=sum(abs(points[t].x-points[i].x)+abs(points[t].y-points[i].y) for t in (j,k))
            new=max(p.x for p in group)-min(p.x for p in group)+max(p.y for p in group)-min(p.y for p in group)
            if (x,y) not in present and old>new:
                rank[x,y]=max(rank.get((x,y),0),old-new)
    extra=[Point(x,y,layer) for x,y in sorted(rank,key=lambda p:(-rank[p],p))[:limit]]
    if not extra:return points,tuple(edges)
    combined=points+tuple(extra);new_edges=tree_edges(combined)
    graph=[set() for _ in combined]
    for i,j in new_edges:graph[i].add(j);graph[j].add(i)
    queue=deque(i for i in range(len(points),len(combined)) if len(graph[i])<=1)
    while queue:
        i=queue.popleft()
        if len(graph[i])>1:continue
        for j in list(graph[i]):
            graph[j].remove(i);graph[i].remove(j)
            if j>=len(points) and len(graph[j])<=1:queue.append(j)
    retained=tuple((i,j) for i,j in new_edges if j in graph[i])
    def length(pts,es):return sum(abs(pts[i].x-pts[j].x)+abs(pts[i].y-pts[j].y) for i,j in es)
    if length(combined,retained)>=length(points,edges):return points,tuple(edges)
    return combined,retained
