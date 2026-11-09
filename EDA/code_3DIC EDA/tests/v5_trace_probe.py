"""Small real-router differential probe, isolated interpreter per mode."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from common import catalog


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['original','on','off']);p.add_argument('output',type=Path)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    engine=ROOT/('v5/engine' if a.mode=='original' else 'v5_trace/engine')
    if a.mode=='on':os.environ.update(V5_TRACE_DIR=str(a.output/'trace'),V5_TRACE_SEARCH='1')
    else:os.environ.pop('V5_TRACE_DIR',None)
    bad_io=a.mode=='on' and os.environ.get('V5_TRACE_FAIL_IO')=='1'
    if bad_io:
        blocked=a.output/'blocked';blocked.write_text('intentionally not a directory')
        os.environ['V5_TRACE_DIR']=str(blocked)
    if a.mode!='original':
        # Load the observer implementation independently of a concurrently
        # running full-flow test; no active engine file is overwritten.
        import importlib.util
        spec=importlib.util.spec_from_file_location('route_trace',ROOT/'v5_trace/route_trace.py')
        module=importlib.util.module_from_spec(spec);sys.modules['route_trace']=module;spec.loader.exec_module(module)
    sys.path.insert(0,str(engine))
    from design import Design
    from calibrated_resources import CalibratedResources
    from maze_router import MazeRouter
    from topology_router import TopologyRouter
    from wire_shortener import SharedTreeRouter
    from validate import validate
    c=catalog()['toy'];d=Design(Path(c['def']),Path(c['lef']))
    name=next(n for n in d.nets if len(d.points(n))>=2 and len({(q.layer-1)//10 for q in d.points(n)})==1)
    connections=list(d.nets[name]);tier=(d.points(name)[0].layer-1)//10
    # Toy contains only two-terminal Nets. Build a small synthetic fanout from
    # its real terminals, in memory in this isolated probe; inputs stay read-only.
    for net in d.nets.values():
        for terminal in net:
            if terminal not in connections and (d.pin(*terminal).layer-1)//10==tier:
                connections.append(terminal)
                if len(connections)>=4:break
        if len(connections)>=4:break
    name='trace_probe';d.nets={name:connections}
    points=d.points(name);results={}
    import heapq
    original_push,original_pop=heapq.heappush,heapq.heappop
    for kind in ('astar','manhattan_mst','steiner'):
        decision=hashlib.sha256();counts={'push':0,'pop':0}
        def push(heap,item):
            counts['push']+=1;decision.update(repr(('push',item)).encode());return original_push(heap,item)
        def pop(heap):
            result=original_pop(heap);counts['pop']+=1;decision.update(repr(('pop',result)).encode());return result
        heapq.heappush=push;heapq.heappop=pop
        r=CalibratedResources(d,'physical',True)
        router=MazeRouter(d,r) if kind=='astar' else TopologyRouter(d) if kind=='manhattan_mst' else SharedTreeRouter(d)
        if kind=='steiner':router.mode='steinerhv'
        router.net_name=name
        try:rects,cross=router.route_net(points)
        finally:heapq.heappush=original_push;heapq.heappop=original_pop
        guide=a.output/(kind+'.guide')
        with guide.open('w',encoding='utf8',newline='\n') as f:
            f.write(name+'\n(\n')
            for x,y,xx,yy,l in rects:f.write(f'{x} {y} {xx} {yy} {d.layers[l]["name"]}\n')
            f.write(')\n')
        validation=validate(d,guide)
        result={'guide_sha256':hashlib.sha256(guide.read_bytes()).hexdigest(),'validation':validation,'cross_die':cross,'decisions_sha256':decision.hexdigest(),'search_operations':counts,'wire_proxy':r.candidate(rects,0)[0]}
        if a.mode=='on' and not bad_io:
            sys.path.insert(0,str(ROOT))
            import route_trace
            route_trace.writer().flush()
            from v5_trace.finalize import finalize
            published=a.output/(kind+'_published');published.mkdir()
            # Finalizer reads the shared disk capture but writes independent
            # per-algorithm artifacts so no result overwrites another.
            import shutil
            route_trace.writer().db.execute('PRAGMA wal_checkpoint(PASSIVE)')
            shutil.copyfile(a.output/'trace/capture.sqlite',published/'capture.sqlite')
            for shard in (a.output/'trace').glob('capture-*.jsonl'):shutil.copyfile(shard,published/shard.name)
            result['trace']=finalize(published,guide,d.layers)
        results[kind]=result
    (a.output/'probe.json').write_text(json.dumps({'net':name,'mode':a.mode,'results':results},indent=2),encoding='utf8')
    print(json.dumps({'mode':a.mode,'net':name,'results':results}),flush=True)


if __name__=='__main__':main()
