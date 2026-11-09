"""Publish only lineage-backed, footprint-verified paths; bounded per Net RAM."""
import importlib.util
import json
import sqlite3
from pathlib import Path
from collections import Counter

from .route_trace import digest, geometry, guide_key


def read_guide(path):
    name=None;opened=False;rects=[]
    with Path(path).open(encoding='utf8') as f:
        for line in f:
            line=line.strip()
            if not line:continue
            if name is None:name=line
            elif line=='(':opened=True
            elif line==')':yield name,rects;name=None;rects=[];opened=False
            elif opened:
                x,y,xx,yy,l=line.split()
                rects.append([int(x),int(y),int(xx),int(yy),int(l.lower().removeprefix('metal').removeprefix('m'))])
            else:raise ValueError('Malformed Guide')
    if name is not None:raise ValueError('Truncated Guide')


def candidate_events(db,directory,candidate,seen=None):
    seen=set() if seen is None else seen
    if candidate in seen:return
    seen.add(candidate)
    row=db.execute('SELECT net,complete,parts FROM candidates WHERE id=?',(candidate,)).fetchone()
    if not row or not row[1]:raise ValueError('INCOMPLETE_CAPTURE')
    for file,off,length in db.execute('SELECT file,off,len FROM events WHERE candidate=? AND event NOT LIKE \'search_%\' ORDER BY seq',(candidate,)):
        with (directory/file).open('rb') as f:
            f.seek(off);raw=f.read(length)
        for line in raw.splitlines():
            item=json.loads(line)
            if item['net']!=row[0]:raise ValueError('NET_IDENTITY_MISMATCH')
            yield item
    for part in json.loads(row[2]):
        yield from candidate_events(db,directory,part,seen)


def route_record(db,directory,name,rects,layers,guide_hash,max_nodes=100000,snapshot=None):
    geom=geometry(rects,layers)
    result=dict(schema_version=1,event='final_route',net=name,guide_sha256=guide_hash,geometry_sha256=geom,accepted=False,provenance='PATH_PROVENANCE_LOST',nodes=[],edges=[],origins=[],reason='No accepted candidate with exact final Guide lineage')
    row=db.execute('SELECT candidate,geom FROM guide_nets WHERE hash=? AND net=?',(snapshot or guide_hash,name)).fetchone()
    if not row or not row[0] or row[1]!=geom:return result
    candidate=row[0];result['candidate_id']=candidate
    emitted=[];nodes={};edges={};origins=set();algorithms=set();sources=set();events_count=0
    def point(p):
        key=tuple(p)
        if key not in nodes:
            if len(nodes)>=max_nodes:raise ValueError('NET_NODE_LIMIT')
            nodes[key]=len(nodes)
        return nodes[key]
    try:
        for item in candidate_events(db,directory,candidate):
            events_count+=1
            if events_count>max_nodes*8:raise ValueError('NET_EVENT_LIMIT')
            if item['event']=='primitive':
                emitted.append(item['rectangle'])
                a,b=item['a'],item['b'];point(a);point(b)
                if a!=b:edges[tuple(sorted((point(a),point(b))))]=('WIRE',item['origin'])
            elif item['event']=='edge':
                a,b=item['a'],item['b']
                if a[2]==b[2]:
                    if a[0]!=b[0] and a[1]!=b[1]:raise ValueError('NON_MANHATTAN_EDGE')
                elif a[:2]!=b[:2] or abs(a[2]-b[2])!=1:raise ValueError('INVALID_VERTICAL_EDGE')
                edges[tuple(sorted((point(a),point(b))))]=(item['kind'],item['origin'])
                origins.add(item['origin'])
            elif item['event']=='astar_node':point(item['node']);origins.add('REAL_ASTAR')
            if item.get('algorithm'):algorithms.add(item['algorithm'])
            if item.get('source'):sources.add(item['source'])
        if geometry(emitted,layers)!=geom:raise ValueError('EMITTED_FOOTPRINT_DIFFERS_FROM_FINAL_GUIDE')
        if not nodes:raise ValueError('NO_OBSERVED_NODES')
        # Plain rect arguments are executable Manhattan wire primitives, not
        # centerlines derived from a Guide. A* point rects supply only footprint;
        # its edges above come exclusively from parents backtracking.
        if any(item[0]=='WIRE' for item in edges.values()) and any(a!=b for a,b in edges):origins.add('REAL_ROUTER_PATH')
        node_list=[dict(id=i,x=p[0],y=p[1],layer=p[2]) for p,i in nodes.items()]
        edge_list=[dict(a=a,b=b,kind=kind,origin=origin) for (a,b),(kind,origin) in edges.items()]
        result.update(accepted=True,provenance='FINAL_ACCEPTED_VERIFIED',nodes=node_list,edges=edge_list,origins=sorted(origins or {'REAL_ROUTER_PATH'}),algorithms=sorted(algorithms),sources=sorted(sources),reason='Validated final Guide hash + selected Net lineage + exact emitted rectangle union',search_replay='EXPANSIONS_AND_RELAXATIONS_IF_RECORDED_ELSE_PATH_ONLY')
    except (ValueError,OSError,KeyError) as exc:
        result['reason']=str(exc)
    return result


def finalize(directory,guide,lef_layers,max_nodes=100000,max_bytes=None,snapshot_path=None):
    directory=Path(directory);guide=Path(guide)
    directory.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(directory/'capture.sqlite')
    # A failed observer may leave no schema. Publish an explicit loss receipt.
    db.executescript('CREATE TABLE IF NOT EXISTS guide_nets(hash TEXT,net TEXT,geom TEXT,candidate TEXT);')
    final_hash=digest(guide)
    index=sqlite3.connect(directory/'route_trace.index.sqlite')
    index.executescript('CREATE TABLE IF NOT EXISTS final(net TEXT PRIMARY KEY,off INTEGER,len INTEGER,provenance TEXT,sha256 TEXT); CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT); DELETE FROM final;')
    counts=Counter();file=directory/'route_trace.jsonl'
    with file.open('wb') as f:
        for name,rects in read_guide(guide):
            snapshot=final_hash+':'+snapshot_path if snapshot_path else guide_key(final_hash,guide)
            record=route_record(db,directory,name,rects,lef_layers,final_hash,max_nodes,snapshot=snapshot)
            line=(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf8')
            if max_bytes is not None and f.tell()+len(line)>max_bytes:
                record.update(accepted=False,provenance='PATH_PROVENANCE_LOST',nodes=[],edges=[],reason='FINAL_TRACE_SIZE_LIMIT')
                line=(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf8')
                if f.tell()+len(line)>max_bytes:line=b''
            off=f.tell();f.write(line)
            index.execute('INSERT INTO final VALUES(?,?,?,?,?)',(name,off,len(line),record['provenance'],__import__('hashlib').sha256(line).hexdigest()))
            counts[record['provenance']]+=1
    receipt=dict(schema_version=1,guide_sha256=final_hash,trace_sha256=digest(file),counts=dict(counts),node_limit_per_net=max_nodes,capture_database='capture.sqlite',trace_file='route_trace.jsonl',index_file='route_trace.index.sqlite',scope='Only observed paths; GCell routing, not track assignment or manufactured metal')
    index.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('receipt',json.dumps(receipt)))
    index.commit();index.close();db.close()
    (directory/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
    return receipt
