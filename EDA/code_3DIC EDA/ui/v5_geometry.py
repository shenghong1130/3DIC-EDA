"""Disposable disk-backed DEF reader and conservative, same-layer Guide sketches.

No routing, resource estimation, inferred vias or cross-die edges. Geometry
transforms use the hash-checked original reader, without modifying its source.
"""
import json
import re
import sqlite3
from collections.abc import MutableMapping


class DiskMap(MutableMapping):
    def __init__(self, db, name):
        self.db, self.name = db, name
        db.execute('CREATE TABLE '+name+'(k TEXT PRIMARY KEY,v TEXT)')
    def __getitem__(self, key):
        row = self.db.execute('SELECT v FROM '+self.name+' WHERE k=?', (key,)).fetchone()
        if row is None: raise KeyError(key)
        return json.loads(row[0])
    def __setitem__(self, key, value):
        self.db.execute('INSERT INTO '+self.name+' VALUES(?,?)', (key, json.dumps(value)))
    def __delitem__(self, key):
        self.db.execute('DELETE FROM '+self.name+' WHERE k=?', (key,))
    def __iter__(self):
        return (r[0] for r in self.db.execute('SELECT k FROM '+self.name))
    def __len__(self):
        return self.db.execute('SELECT count(*) FROM '+self.name).fetchone()[0]


def read_design(module, def_path, lef_path, db_path):
    """Stream semicolon records; components, IO shapes and nets live on disk."""
    db = sqlite3.connect(db_path)
    db.execute('PRAGMA cache_size=-4096'); db.execute('PRAGMA journal_mode=OFF')
    class Reader(module.Design):
        def _read_def(self):
            self.components = DiskMap(db, 'components')
            self.nets = DiskMap(db, 'nets')
            self.io_geometry = DiskMap(db, 'io_geometry')
            section = None; pending = ''; declared = {}; parsed = {}
            placement = r'\+\s+(?:PLACED|FIXED|COVER)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\w+)'
            with self.def_path.open(encoding='utf8') as source:
                for line in source:
                    line = re.sub(r'#[^\n]*', '', line).strip()
                    if not line: continue
                    start = re.fullmatch(r'(COMPONENTS|PINS|NETS)\s+(\d+)\s*;', line)
                    if start:
                        section = start[1]; declared[section] = int(start[2]); parsed[section] = 0; continue
                    if section and line == 'END '+section:
                        if pending or parsed[section] != declared[section]: raise ValueError('Truncated DEF '+section)
                        section = None; continue
                    if section:
                        pending += ' '+line
                        if len(pending)>16*1024*1024: raise ValueError('DEF record exceeds 16 MiB UI limit')
                        while ';' in pending:
                            record, pending = pending.split(';', 1); record = record.strip().removeprefix('-').strip(); pending = pending.strip()
                            fields = record.split(); name = fields[0]; parsed[section] += 1
                            if section == 'NETS':
                                self.nets[name] = re.findall(r'\(\s*(\S+)\s+(\S+)\s*\)', record.split('+',1)[0])
                            elif section == 'COMPONENTS':
                                p = re.search(placement, record)
                                if not p: raise ValueError('Unplaced component '+name)
                                self.components[name] = [fields[1], int(p[1]), int(p[2]), p[3]]
                            else:
                                ports = re.split(r'\+\s*PORT\b', record); shapes = []
                                for port in ports[1:] if len(ports)>1 else ports:
                                    p = re.search(placement, port)
                                    geometries = re.findall(r'\+\s+LAYER\s+(\S+)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)', port)
                                    if not p or not geometries: raise ValueError('Unsupported IO PORT '+name)
                                    for layer,a,b,c,d in geometries:
                                        corners = [module.orient_xy(x,y,p[3],component=False) for x,y in ((int(a),int(b)),(int(a),int(d)),(int(c),int(b)),(int(c),int(d))) ]
                                        shapes.append([module.layer_number(layer),[int(p[1])+min(v[0] for v in corners),int(p[2])+min(v[1] for v in corners),int(p[1])+max(v[0] for v in corners),int(p[2])+max(v[1] for v in corners)]])
                                self.io_geometry[name] = shapes
                        continue
                    m = re.search(r'UNITS\s+DISTANCE\s+MICRONS\s+(\d+)',line)
                    if m: self.dbu = int(m[1])
                    m = re.search(r'DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)',line)
                    if m: self.die = tuple(map(int,m.groups()))
                    m = re.search(r'GCELLGRID\s+([XY])\s+(-?\d+)\s+DO\s+(\d+)\s+STEP\s+(\d+)',line)
                    if m: self.grid[m[1]].append(tuple(map(int,m.groups()[1:])))
            if section or set(declared)!={'COMPONENTS','PINS','NETS'} or not hasattr(self,'die'): raise ValueError('Incomplete DEF')
            db.commit()
    try:
        design = Reader(def_path, lef_path); design.ui_db = db
        db.execute('CREATE TABLE seen(name TEXT PRIMARY KEY)')
        return design
    except Exception:
        db.close(); raise


def estimate_paths(net, layers, limit=4000):
    """Sketch centerlines INSIDE individual rectangles; never join layers.

    Pin stubs end inside a positive-area RECT/Guide intersection. They are
    illustrative geometric access candidates, never Router access points.
    Disconnected rectangles remain disconnected; no full-route claim is made.
    """
    segments = []; accesses = []; checks = 0; processed = 0
    for index,g in enumerate(net.get('guides', [])):
        if len(segments)>=limit: break
        processed += 1
        x1,y1,x2,y2,layer = g; cx=(x1+x2)/2; cy=(y1+y2)/2
        direction = layers.get(layer,{}).get('direction','').upper()
        horizontal = direction.startswith('H') if direction else x2-x1>=y2-y1
        a,b = ([x1,cy],[x2,cy]) if horizontal else ([cx,y1],[cx,y2])
        segments.append({'a':a,'b':b,'layer':layer,'guide_index':index})
        for p in net.get('pins',[]):
            checks += 1
            if checks>200000 or len(accesses)>=limit: break
            if p['layer']!=layer: continue
            box=p['box']; lo=[max(x1,box[0]),max(y1,box[1])]; hi=[min(x2,box[2]),min(y2,box[3])]
            if lo[0]>=hi[0] or lo[1]>=hi[1]: continue
            access=[(lo[0]+hi[0])/2,(lo[1]+hi[1])/2]
            end=[access[0],cy] if horizontal else [cx,access[1]]
            accesses.append({'terminal':p['terminal'],'rect':p['name'],'layer':layer,'point':access,'provenance':'PIN_GUIDE_INTERSECTION_ESTIMATE'})
            if len(segments)<limit: segments.append({'a':access,'b':end,'layer':layer,'guide_index':index})
    return {'segments':segments,'access_candidates':accesses, 'truncated':processed<len(net.get('guides',[])) or checks>200000 or len(accesses)>=limit,
            'label':'推导路径 / 非 Router 真实 A*', 'scope':'仅同层 Guide 内示意；未推断 Via、TSV、跨层连接或完整连通性'}
