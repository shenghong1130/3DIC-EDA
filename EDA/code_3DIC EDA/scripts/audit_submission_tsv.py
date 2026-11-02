"""Independent, read-only DEF/LEF/Guide TSV geometry audit (standard library).

No Router, result.json, resource mask or reference-scorer implementation is used.
SQLite R-trees index instance envelopes and reusable oriented LEF templates in a
TemporaryDirectory. DEF/Guide are streamed, with explicit per-record budgets.
Unsupported input is INCOMPLETE, never silently accepted. This is NOT a scorer.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import csv
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time

TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[();+]|[^\s();+]+')
LAYER = re.compile(r'(?:metal|m)(\d+)$', re.I)
ORIENT = {'N': (1, 0, 0, 1), 'S': (-1, 0, 0, -1),
          'E': (0, 1, -1, 0), 'W': (0, -1, 1, 0),
          'FN': (-1, 0, 0, 1), 'FS': (1, 0, 0, -1),
          'FE': (0, 1, 1, 0), 'FW': (0, -1, -1, 0)}
PROJECT = Path(__file__).resolve().parents[1]
OFFICIAL = {'open_3d_case001': (77, 132, 6),
            'open_3d_case003': (33, 559, 5),
            'open_3d_case005': (907, 919, 11)}


class Incomplete(ValueError):
    pass


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def dump(path, value):
    with Path(path).open('x', encoding='utf8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')


def number(s):
    v = float(s)
    if not math.isfinite(v):
        raise Incomplete('Nonfinite coordinate')
    # Decimal input * DBU can be a few ulps off an exact grid line. Snap ONLY
    # within numerical roundoff (not manufacturing tolerance).
    if abs(v - round(v)) < 1e-8:
        return float(round(v))
    return v


def rectangle(v):
    r = tuple(number(x) for x in v)
    if len(r) != 4 or r[0] >= r[2] or r[1] >= r[3]:
        raise Incomplete('Nonpositive rectangle: ' + str(v))
    return r


def relation(a, b):
    dx, dy = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    return 'positive' if dx > 0 and dy > 0 else 'boundary' if dx >= 0 and dy >= 0 else None


def intersect(a, b):
    return max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])


def transformed(r, size, origin, orient, scale=1):
    """DEF placement anchors the oriented nominal macro bounding box.

    LEF ORIGIN shifts library geometry before orientation. IO uses size/origin
    zero, so it is rotated about its placement origin without bbox compensation.
    All eight transforms use a matrix and four corners, not Router functions.
    """
    a, b, c, d = ORIENT[orient]
    w, h = size
    dx = -min(0, a * w, b * h, a * w + b * h)
    dy = -min(0, c * w, d * h, c * w + d * h)
    points = [(a * (x + origin[0]) + b * (y + origin[1]) + dx,
               c * (x + origin[0]) + d * (y + origin[1]) + dy)
              for x in (r[0], r[2]) for y in (r[1], r[3])]
    return tuple(number(str(x * scale)) for x in
                 (min(p[0] for p in points), min(p[1] for p in points),
                  max(p[0] for p in points), max(p[1] for p in points)))


def lef_macros(path):
    """Independent line/token parser; all actual inputs use rectangular ports."""
    if Path(path).stat().st_size > 32 * 1024 * 1024:
        raise Incomplete('LEF exceeds explicit 32MiB template-source budget')
    macros, routing = {}, set()
    macro = pin = layer = block = top_layer = None
    shapes, size, origin = [], None, (0, 0)
    with Path(path).open(encoding='utf8') as f:
        for ln, line in enumerate(f, 1):
            t = TOKEN.findall(line.split('#', 1)[0])
            if not t:
                continue
            k = t[0]
            if macro is None:
                if k == 'MACRO':
                    macro, shapes, size, origin = t[1], [], None, (0, 0)
                elif k == 'LAYER':
                    top_layer = t[1]
                elif k == 'TYPE' and t[1] == 'ROUTING':
                    m = LAYER.fullmatch(top_layer or '')
                    if not m:
                        raise Incomplete('Unsupported routing layer')
                    routing.add(int(m[1]))
                continue
            if k == 'SIZE':
                size = number(t[1]), number(t[3])
            elif k == 'ORIGIN':
                origin = number(t[1]), number(t[2])
            elif k == 'PIN':
                pin, block, layer = t[1], 'PIN', None
            elif k == 'OBS':
                pin, block, layer = '', 'OBS', None
            elif k == 'LAYER' and block:
                m = LAYER.fullmatch(t[1])
                layer = int(m[1]) if m else None
            elif k in {'POLYGON', 'PATH', 'VIA', 'ITERATE', 'RECT'} and block:
                if k != 'RECT' or len(t) != 6:
                    raise Incomplete(f'Unsupported LEF geometry {path}:{ln}: {line.strip()}')
                if layer in routing:
                    # Preserve both source corners, including inverted vendor
                    # rectangles. The placement transform below computes their
                    # envelope explicitly; anomalies are reported, not hidden.
                    shapes.append((layer, block, pin, tuple(number(v) for v in t[1:5])))
            elif k == 'END':
                if len(t) > 1 and t[1] == macro:
                    if block or size is None or min(size) <= 0:
                        raise Incomplete('Malformed LEF macro ' + macro)
                    macros[macro] = {'size': size, 'origin': origin, 'shapes': shapes}
                    macro = None
                elif block == 'PIN' and len(t) > 1 and t[1] == pin:
                    pin = block = layer = None
                elif block == 'OBS':
                    block = layer = None
    if macro or not macros or routing != set(range(1, 21)):
        raise Incomplete('Incomplete LEF or expected metal1..metal20 missing')
    return macros


def def_records(path):
    """One semicolon record or END per yield; no whole-DEF token array."""
    pending = ''
    with Path(path).open(encoding='utf8') as f:
        for line in f:
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            if not pending and line.startswith('END '):
                yield TOKEN.findall(line)
                continue
            pending += ' ' + line
            if len(pending) > 16 * 1024 * 1024:
                raise Incomplete('DEF record exceeds explicit 16MiB budget')
            while ';' in pending:
                record, pending = pending.split(';', 1)
                yield TOKEN.findall(record)
    if pending.strip():
        raise Incomplete('Unterminated DEF record')


class Grid:
    def __init__(self, raw, die):
        self.raw = {a: sorted(set(v)) for a, v in raw.items()}
        self.die = die
        self.x = sorted({die[0], die[2], *(v for v in self.raw['X'] if die[0] < v < die[2])})
        self.y = sorted({die[1], die[3], *(v for v in self.raw['Y'] if die[1] < v < die[3])})
        if (len(self.x) - 1) * (len(self.y) - 1) > 10000000:
            raise Incomplete('Grid exceeds explicit 10M-cell domain budget')

    def cell(self, col, row, raw=False):
        x, y = (self.raw['X'], self.raw['Y']) if raw else (self.x, self.y)
        if not (0 <= col < len(x) - 1 and 0 <= row < len(y) - 1):
            return None
        return x[col], y[row], x[col + 1], y[row + 1]

    def cells(self, r, touch=False):
        def indices(a, lo, hi):
            # Include BOTH sides only for the explicit boundary-contact model.
            left = (bisect.bisect_left(a, lo) if touch else bisect.bisect_right(a, lo)) - 1
            right = (bisect.bisect_right(a, hi) if touch else bisect.bisect_left(a, hi)) - 1
            return range(max(0, left), min(len(a) - 2, right) + 1)
        rows, cols = indices(self.y, r[1], r[3]), indices(self.x, r[0], r[2])
        if len(rows) * len(cols) > 100000:
            raise Incomplete('One rectangle exceeds explicit 100k-cell enumeration budget')
        for row in rows:
            for col in cols:
                c = self.cell(col, row)
                rel = relation(c, r)
                if rel == 'positive' or touch and rel == 'boundary':
                    yield col, row, rel


class Geometry:
    """Disk-backed original-instance and reusable local-shape spatial indices."""
    def __init__(self, lef, path):
        self.macros = lef_macros(lef)
        self.db = sqlite3.connect(path)
        self.db.executescript('''PRAGMA cache_size=-24576; PRAGMA temp_store=FILE;
            PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
            CREATE TABLE inst(id INTEGER PRIMARY KEY,name TEXT UNIQUE,macro TEXT,ori TEXT,x REAL,y REAL,mid INT);
            CREATE VIRTUAL TABLE boxes USING rtree(id,x1,x2,y1,y2);
            CREATE TABLE shape(id INTEGER PRIMARY KEY,mid INT,layer INT,kind TEXT,pin TEXT,
                x1 REAL,y1 REAL,x2 REAL,y2 REAL,original TEXT);
            CREATE VIRTUAL TABLE local USING rtree(id,x1,x2,y1,y2,m1,m2);
            CREATE TABLE net(name TEXT PRIMARY KEY,cross_die INT,seen INT DEFAULT 0);
            CREATE TABLE terminal(net TEXT,inst TEXT,pin TEXT);
        ''')
        self.templates, self.masks, self.counts = {}, {}, collections.Counter()
        self.raw, self.die, self.dbu = {'X': [], 'Y': []}, None, None
        self.section_counts = {}

    def close(self):
        self.db.close()

    def template(self, macro, ori):
        key = macro, ori
        if key in self.templates:
            return self.templates[key]
        m, mid = self.macros[macro], len(self.templates) + 1
        bounds, mask = [], {}
        for layer, kind, pin, original in m['shapes']:
            r = transformed(original, m['size'], m['origin'], ori, 1 if macro.startswith('@IO:') else self.dbu)
            bounds.append(r)
            cur = self.db.execute('INSERT INTO shape(mid,layer,kind,pin,x1,y1,x2,y2,original) VALUES(?,?,?,?,?,?,?,?,?)',
                                  (mid, layer, kind, pin, *r, json.dumps(original)))
            self.db.execute('INSERT INTO local VALUES(?,?,?,?,?,?,?)',
                            (cur.lastrowid, r[0], r[2], r[1], r[3], mid, mid))
            if kind == 'PIN':
                mask[pin] = mask.get(pin, 0) | (1 if layer <= 10 else 2)
        # Empty resource/filler macros have no Pin/OBS. Index a zero envelope
        # for the instance lookup; its empty shape table can never block a TSV.
        envelope = ((min(r[0] for r in bounds), min(r[1] for r in bounds),
                     max(r[2] for r in bounds), max(r[3] for r in bounds)) if bounds else (0, 0, 0, 0))
        self.templates[key] = mid, envelope
        self.masks[macro] = mask
        return mid, envelope

    def instance(self, name, macro, ori, x, y):
        mid, r = self.template(macro, ori)
        cur = self.db.execute('INSERT INTO inst(name,macro,ori,x,y,mid) VALUES(?,?,?,?,?,?)', (name, macro, ori, x, y, mid))
        self.db.execute('INSERT INTO boxes VALUES(?,?,?,?,?)', (cur.lastrowid, r[0] + x, r[2] + x, r[1] + y, r[3] + y))
        self.counts['instances'] += 1
        self.counts['physical_rectangles'] += len(self.macros[macro]['shapes'])

    def read_def(self, path):
        section = None
        observed = collections.Counter()
        for t in def_records(path):
            k = t[0]
            if k == 'UNITS':
                self.dbu = number(t[3])
            elif k == 'DIEAREA':
                self.die = rectangle([t[2], t[3], t[6], t[7]])
            elif k == 'GCELLGRID':
                axis, start, count, step = t[1], number(t[2]), int(t[4]), number(t[6])
                if count > 100000 or step <= 0:
                    raise Incomplete('Grid pattern outside audit budget')
                self.raw[axis].extend(start + j * step for j in range(count))
            elif k in {'COMPONENTS', 'PINS', 'NETS'}:
                section = k
                self.section_counts[k] = int(t[1])
            elif k in {'BLOCKAGES', 'SPECIALNETS'} and int(t[1]):
                raise Incomplete('Nonempty unsupported DEF section ' + k)
            elif k == 'END':
                section = None
            elif k == '-' and section:
                observed[section] += 1
                if section in {'COMPONENTS', 'PINS'}:
                    placements = [i for i, v in enumerate(t) if v in {'PLACED', 'FIXED', 'COVER'}]
                    if len(placements) != 1 or t.count('PORT') > 1:
                        raise Incomplete('Unsupported multi-port/unplaced DEF geometry ' + t[1])
                    j = placements[0]
                    x, y, ori = number(t[j + 2]), number(t[j + 3]), t[j + 5]
                    if section == 'COMPONENTS':
                        self.instance(t[1], t[2], ori, x, y)
                    else:
                        shapes = []
                        for j, v in enumerate(t):
                            if v == 'LAYER':
                                match = LAYER.fullmatch(t[j + 1])
                                if not match:
                                    raise Incomplete('Unknown DEF pin layer')
                                shapes.append((int(match[1]), 'PIN', t[1], rectangle([t[j + 3], t[j + 4], t[j + 7], t[j + 8]])))
                        if not shapes:
                            raise Incomplete('IO pin without supported rectangle')
                        macro = '@IO:' + t[1]
                        self.macros[macro] = {'size': (0, 0), 'origin': (0, 0), 'shapes': shapes}
                        self.instance('@IO:' + t[1], macro, ori, x, y)
                else:
                    name, mask, j = t[1], 0, 2
                    while j < len(t) and t[j] != '+':
                        if t[j] != '(' or t[j + 3] != ')':
                            raise Incomplete('Unsupported net connection ' + name)
                        inst, pin = t[j + 1:j + 3]
                        if inst == 'PIN':
                            inst = '@IO:' + pin
                        row = self.db.execute('SELECT macro FROM inst WHERE name=?', (inst,)).fetchone()
                        if row is None or pin not in self.masks[row[0]]:
                            raise Incomplete('Undefined connected pin ' + inst + '/' + pin)
                        mask |= self.masks[row[0]][pin]
                        self.db.execute('INSERT INTO terminal VALUES(?,?,?)', (name, inst, pin))
                        j += 4
                    self.db.execute('INSERT INTO net(name,cross_die) VALUES(?,?)', (name, int(mask == 3)))
                    if mask == 3:
                        self.counts['cross_die_nets'] += 1
                if observed[section] % 10000 == 0:
                    self.db.commit()
            # Other DEF records (rows/tracks/property declarations) do not
            # define obstacle rectangles and are deliberately not expanded.
        if any(observed[k] != v for k, v in self.section_counts.items()):
            raise Incomplete('DEF section count mismatch')
        if self.dbu is None or self.die is None or not all(self.raw.values()):
            raise Incomplete('Missing DEF grid/units/die')
        self.grid = Grid(self.raw, self.die)
        self.db.execute('CREATE INDEX terminals_by_net ON terminal(net)')
        self.db.commit()

    def geometry(self, box):
        # SQLite R-tree uses float32 OUTWARD-rounded bounds. Stored float64
        # rectangles below are the final exact predicates, not R-tree bounds.
        eps = 1e-6
        instances = self.db.execute('''SELECT i.name,i.macro,i.ori,i.x,i.y,i.mid FROM boxes b
            JOIN inst i ON i.id=b.id WHERE b.x1<=? AND b.x2>=? AND b.y1<=? AND b.y2>=?''',
                                    (box[2] + eps, box[0] - eps, box[3] + eps, box[1] - eps))
        for name, macro, ori, x, y, mid in instances:
            local = box[0] - x, box[1] - y, box[2] - x, box[3] - y
            rows = self.db.execute('''SELECT s.layer,s.kind,s.pin,s.x1,s.y1,s.x2,s.y2,s.original
                FROM local r JOIN shape s ON s.id=r.id WHERE r.m1<=? AND r.m2>=?
                AND r.x1<=? AND r.x2>=? AND r.y1<=? AND r.y2>=?''',
                                   (mid, mid, local[2] + eps, local[0] - eps, local[3] + eps, local[1] - eps))
            for layer, kind, pin, x1, y1, x2, y2, original in rows:
                r = x1 + x, y1 + y, x2 + x, y2 + y
                rel = relation(box, r)
                if rel:
                    yield {'instance': name, 'macro': macro, 'orientation': ori, 'placement_dbu': [x, y],
                           'macro_origin_um': self.macros[macro]['origin'], 'layer': layer, 'kind': kind,
                           'pin': pin, 'original_rect': json.loads(original),
                           'original_units': 'DBU' if name.startswith('@IO:') else 'um',
                           'rect_dbu': r, 'relation': rel}


def guide_nets(path):
    name, state, rectangles = None, 'name', []
    with Path(path).open(encoding='utf8') as f:
        for ln, line in enumerate(f, 1):
            t = line.split()
            if not t:
                continue
            if state == 'name':
                if len(t) != 1:
                    raise Incomplete('Guide Net syntax at line ' + str(ln))
                name, state, rectangles = t[0], 'open', []
            elif state == 'open':
                if t != ['(']:
                    raise Incomplete('Guide missing open parenthesis')
                state = 'rect'
            elif t == [')']:
                yield name, rectangles
                state = 'name'
            else:
                if len(t) != 5 or not LAYER.fullmatch(t[4]):
                    raise Incomplete('Invalid Guide row at line ' + str(ln))
                rectangles.append((int(LAYER.fullmatch(t[4])[1]), rectangle(t[:4])))
                if len(rectangles) > 100000:
                    raise Incomplete('Per-Net Guide exceeds explicit 100000-rectangle budget')
    if state != 'name':
        raise Incomplete('Truncated Guide')


def overlaps(rectangles):
    lower = sorted({r for layer, r in rectangles if layer == 10})
    upper = sorted({r for layer, r in rectangles if layer == 11})
    # Interface rectangles are per-cell in V3.5. Sorted X bounds avoid testing
    # unrelated pairs; all positive overlap regions are retained and deduped.
    starts, regions = [r[0] for r in upper], set()
    for a in lower:
        for b in upper[:bisect.bisect_left(starts, a[2])]:
            if b[2] > a[0] and relation(a, b) == 'positive':
                regions.add(intersect(a, b))
                if len(regions) > 100000:
                    raise Incomplete('Interface overlap workspace exceeds 100000 regions')
    return sorted(regions)


def rule_evidence(model, col, row, center):
    cell = model.grid.cell(col, row)
    half = 2.5 * model.dbu
    footprint = center[0] - half, center[1] - half, center[0] + half, center[1] + half
    cells = list(model.grid.cells(footprint, touch=True))
    boxes = [(x, y, rel, model.grid.cell(x, y)) for x, y, rel in cells]
    bounds = (min(c[3][0] for c in boxes), min(c[3][1] for c in boxes),
              max(c[3][2] for c in boxes), max(c[3][3] for c in boxes))
    flags = collections.Counter()
    evidence = []
    for geo in model.geometry(bounds):
        for x, y, footprint_rel, r in boxes:
            geometry_rel = relation(r, geo['rect_dbu'])
            if geometry_rel is None:
                continue
            central = (x, y) == (col, row)
            event = dict(geo, col=x, row=y, center_cell=central,
                         footprint_cell_relation=footprint_rel, geometry_cell_relation=geometry_rel)
            evidence.append(event)
            # The four models use positive-area geometry/cell and footprint/cell
            # intersection. Boundary-only findings remain a separate evidence set.
            if geometry_rel == 'positive':
                if central:
                    flags['A_D'] += 1
                    flags['center_' + geo['kind']] += 1
                    if geo['layer'] in {10, 11}:
                        flags['A_C'] += 1
                if footprint_rel == 'positive':
                    flags['B_D'] += 1
                    if not central:
                        flags['neighbor_' + geo['kind']] += 1
                    if geo['layer'] in {10, 11}:
                        flags['B_C'] += 1
            else:
                flags['boundary_geometry'] += 1
            if footprint_rel == 'boundary':
                flags['boundary_footprint'] += 1
    return flags, evidence, footprint, cells


def audit(model, guide, case, label, output, official=None):
    began = time.perf_counter()
    counts = collections.Counter()
    matrix = {k: 0 for k in ['A_C', 'A_D', 'B_C', 'B_D']}
    output = Path(output)
    candidate_fields = ['case', 'label', 'net', 'cross_die', 'region_count', 'cell_candidate_count',
                        'region_dbu', 'col', 'row', 'region_center_dbu', 'cell_dbu', 'footprint_dbu',
                        'classification', 'A_C', 'A_D', 'B_C', 'B_D', 'boundary_geometry', 'boundary_footprint']
    conflict_fields = ['case', 'label', 'net', 'candidate_col', 'candidate_row', 'classification',
                       'same_net_pin', 'evidence_json']
    sites = []
    if official:
        for base in (0, 1):
            col, row = official[0] - base, official[1] - base
            for raw in (False, True):
                box = model.grid.cell(col, row, raw=raw)
                sites.append({'reported_col': official[0], 'reported_row': official[1],
                              'reported_used_uninterpreted': official[2], 'index_base': base,
                              'grid': 'raw' if raw else 'DIEAREA_clipped', 'col0': col, 'row0': row,
                              'rect_dbu': box, 'rect_um': [x / model.dbu for x in box] if box else None,
                              'geometry': list(model.geometry(box)) if box else [],
                              'nets': [], 'tsv_candidates': []})
    with (output / 'tsv_candidates.csv').open('x', newline='', encoding='utf8') as cf, \
            (output / 'tsv_conflicts.csv').open('x', newline='', encoding='utf8') as ef, \
            (output / 'net_anomalies.csv').open('x', newline='', encoding='utf8') as nf:
        cw, ew = csv.DictWriter(cf, candidate_fields), csv.DictWriter(ef, conflict_fields)
        nw = csv.writer(nf)
        cw.writeheader(); ew.writeheader(); nw.writerow(['case', 'net', 'classification', 'reason'])
        for name, rects in guide_nets(guide):
            expected = model.db.execute('SELECT cross_die,seen FROM net WHERE name=?', (name,)).fetchone()
            if expected is None or expected[1]:
                raise Incomplete('Undefined/duplicate Guide Net ' + name)
            cross = expected[0]
            model.db.execute('UPDATE net SET seen=1 WHERE name=?', (name,))
            counts['guide_nets'] += 1
            counts['guide_rectangles'] += len(rects)
            for layer, r in rects:
                if not 1 <= layer <= 20 or relation(r, model.die) != 'positive':
                    counts['guide_geometry_errors'] += 1
                    nw.writerow([case, name, 'CONFIRMED_VIOLATION', 'Illegal layer or no positive DIEAREA/GCell overlap: ' + str((layer, r))])
            regions = overlaps(rects)
            cell_set = set()
            for r in regions:
                for x, y, _ in model.grid.cells(r):
                    cell_set.add((x, y))
                    if len(cell_set) > 100000:
                        raise Incomplete('Per-Net overlap cell set exceeds explicit 100k-cell budget')
            if cross:
                counts['cross_die_nets'] += 1
                if not regions:
                    counts['missing_tsv_nets'] += 1
                    nw.writerow([case, name, 'CONFIRMED_VIOLATION', 'Cross-Die net has no M10/M11 positive overlap'])
            elif regions:
                counts['intra_die_overlap_nets'] += 1
                nw.writerow([case, name, 'CONFIRMED_VIOLATION', 'Intra-Die Net has M10/M11 positive overlap'])
            ambiguous = len(regions) != 1 or len(cell_set) != 1
            if regions and ambiguous:
                counts['ambiguous_nets'] += 1
                nw.writerow([case, name, 'AMBIGUOUS', f'{len(regions)} positive regions / {len(cell_set)} possible GCells; no undocumented candidate waiver'])
            own = set(model.db.execute('SELECT inst,pin FROM terminal WHERE net=?', (name,)))
            for site in sites:
                box = site['rect_dbu']
                if not box:
                    continue
                relevant = [(layer, r) for layer, r in rects if layer in {10, 11} and relation(r, box) == 'positive']
                if relevant:
                    site['nets'].append({'net': name, 'rectangles': relevant,
                                         'positive_overlap_regions': [r for r in regions if relation(r, box) == 'positive']})
            for region in regions:
                center = (region[0] + region[2]) / 2, (region[1] + region[3]) / 2
                for col, row, _ in model.grid.cells(region):
                    # Unique one-cell region -> documented overlap midpoint.
                    # Multi-cell overlap -> location is unresolved; evaluate
                    # every potential cell's midpoint as an EXPLICIT model.
                    cell = model.grid.cell(col, row)
                    actual = center if len(cell_set) == 1 else ((cell[0] + cell[2]) / 2, (cell[1] + cell[3]) / 2)
                    flags, events, footprint, cells = rule_evidence(model, col, row, actual)
                    counts['tsv_candidates'] += 1
                    classification = ('AMBIGUOUS' if ambiguous else 'CONFIRMED_VIOLATION' if flags['A_C'] else
                                      'MODEL_DEPENDENT' if flags['B_D'] or flags['boundary_geometry'] or flags['boundary_footprint'] else 'NO_VIOLATION_FOUND')
                    if flags['A_D'] and not flags['A_C']:
                        classification = 'AMBIGUOUS' if ambiguous else 'MODEL_DEPENDENT'
                    for key in matrix:
                        matrix[key] += bool(flags[key])
                    for key in ['center_PIN', 'center_OBS', 'neighbor_PIN', 'neighbor_OBS', 'boundary_geometry', 'boundary_footprint']:
                        counts[key + '_candidates'] += bool(flags[key])
                    counts[classification] += 1
                    if not (model.die[0] <= footprint[0] and model.die[1] <= footprint[1] and footprint[2] <= model.die[2] and footprint[3] <= model.die[3]):
                        counts['footprint_outside_die'] += 1
                        nw.writerow([case, name, 'MODEL_DEPENDENT', '5um footprint extends outside DIEAREA'])
                    rowdata = dict(case=case, label=label, net=name, cross_die=cross, region_count=len(regions),
                                   cell_candidate_count=len(cell_set), region_dbu=json.dumps(region), col=col, row=row,
                                   region_center_dbu=json.dumps(actual), cell_dbu=json.dumps(cell), footprint_dbu=json.dumps(footprint),
                                   classification=classification, **{k: int(bool(flags[k])) for k in matrix},
                                   boundary_geometry=flags['boundary_geometry'], boundary_footprint=flags['boundary_footprint'])
                    cw.writerow(rowdata)
                    for event in events:
                        ew.writerow(dict(case=case, label=label, net=name, candidate_col=col, candidate_row=row,
                                         classification=classification, same_net_pin=int((event['instance'], event['pin']) in own),
                                         evidence_json=json.dumps(event, ensure_ascii=False)))
                    for site in sites:
                        if site['rect_dbu'] and relation(region, site['rect_dbu']) == 'positive':
                            site['tsv_candidates'].append(rowdata)
            if counts['guide_nets'] % 10000 == 0:
                model.db.commit(); cf.flush(); ef.flush(); nf.flush()
                print(f'{case}: audited {counts["guide_nets"]} Nets, {counts["tsv_candidates"]} TSV candidates', flush=True)
    missing = model.db.execute('SELECT COUNT(*) FROM net WHERE seen=0').fetchone()[0]
    counts['missing_guide_nets'] = missing
    if missing:
        raise Incomplete(f'Guide missing {missing} DEF Nets')
    confirmed = any(counts[k] for k in ['CONFIRMED_VIOLATION', 'guide_geometry_errors', 'missing_tsv_nets', 'intra_die_overlap_nets'])
    classification = ('CONFIRMED_VIOLATION' if confirmed else 'AMBIGUOUS' if counts['ambiguous_nets'] else
                      'MODEL_DEPENDENT' if counts['MODEL_DEPENDENT'] or counts['footprint_outside_die'] else 'NO_VIOLATION_FOUND')
    return {'case': case, 'label': label, 'classification': classification, 'counts': dict(counts), 'rule_matrix_conflicting_candidates': matrix,
            'geometry': dict(model.counts), 'grid_cols': len(model.grid.x) - 1, 'grid_rows': len(model.grid.y) - 1,
            'raw_x_ends': [model.grid.raw['X'][0], model.grid.raw['X'][-1]],
            'raw_y_ends': [model.grid.raw['Y'][0], model.grid.raw['Y'][-1]], 'die_dbu': model.die,
            'dbu_per_micron': model.dbu, 'official_sites': sites, 'runtime_seconds': time.perf_counter() - began,
            'strict_submission_ready': not any(counts[k] for k in ['AMBIGUOUS', 'CONFIRMED_VIOLATION', 'MODEL_DEPENDENT',
                'guide_geometry_errors', 'missing_tsv_nets', 'intra_die_overlap_nets', 'footprint_outside_die'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--def', dest='def_file', required=True, type=Path)
    parser.add_argument('--lef', required=True, type=Path)
    parser.add_argument('--guide', required=True, type=Path)
    parser.add_argument('--case', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    began = time.perf_counter()
    identity = {k: {'path': str(p.resolve()), 'sha256': sha(p), 'bytes': p.stat().st_size}
                for k, p in [('def', args.def_file), ('lef', args.lef), ('guide', args.guide)]}
    dump(args.output / 'input_identity.json', identity)
    result = {'case': args.case, 'classification': 'INCOMPLETE'}
    try:
        with tempfile.TemporaryDirectory(prefix='independent_tsv_') as temp:
            model = Geometry(args.lef, Path(temp) / 'geometry.sqlite')
            try:
                model.read_def(args.def_file)
                print(args.case + ': independent geometry index complete', flush=True)
                result = audit(model, args.guide, args.case, args.label, args.output, OFFICIAL.get(args.case))
                result['temporary_index_bytes'] = (Path(temp) / 'geometry.sqlite').stat().st_size
                result['lef_source_geometry_anomalies'] = [dict(macro=n, layer=l, kind=k, pin=p, original_rect=r,
                    interpretation='four-corner placement envelope; source corners not rewritten')
                    for n, m in model.macros.items() for l, k, p, r in m['shapes'] if r[0] >= r[2] or r[1] >= r[3]]
            finally:
                model.close()
        result['inputs_unchanged'] = all(sha(row['path']) == row['sha256'] for row in identity.values())
        if not result['inputs_unchanged']:
            result.update(classification='INCOMPLETE', strict_submission_ready=False)
    except Exception as e:
        result.update(classification='INCOMPLETE', strict_submission_ready=False, error=repr(e))
    result['total_runtime_seconds'] = time.perf_counter() - began
    result['memory_policy'] = 'SQLite 24MiB cache; streaming DEF/Guide; 16MiB DEF record / 100k per-Net rectangles or overlap regions; no result.json parsed'
    if os.name == 'nt':
        class Memory(ctypes.Structure):
            _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong), *[(k, ctypes.c_size_t) for k in
                ['PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage',
                 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage']]]
        m = Memory(); m.cb = ctypes.sizeof(m)
        kernel = ctypes.WinDLL('kernel32'); kernel.GetCurrentProcess.restype = ctypes.c_void_p
        psapi = ctypes.WinDLL('psapi'); psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(m), m.cb):
            result['process_peak_rss_bytes'] = m.PeakWorkingSetSize
            result['process_peak_commit_bytes'] = m.PeakPagefileUsage
    dump(args.output / 'summary.json', result)
    print(json.dumps({k: result.get(k) for k in ['case', 'classification', 'counts', 'error', 'total_runtime_seconds', 'process_peak_rss_bytes']}, ensure_ascii=False), flush=True)
    return int(result['classification'] == 'INCOMPLETE')


if __name__ == '__main__':
    raise SystemExit(main())
