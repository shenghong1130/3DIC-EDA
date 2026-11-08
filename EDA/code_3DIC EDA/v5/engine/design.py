"""Small, strict LEF/DEF reader for the supplied contest data (not a full standard parser)."""
from dataclasses import dataclass
from pathlib import Path
import re

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    layer: int


@dataclass
class Macro:
    width: float
    height: float
    origin: tuple
    pins: dict


def layer_number(name):
    m = re.fullmatch(r"(?:metal|m)(\d+)", name, re.I)
    if not m or not 1 <= int(m[1]) <= 20:
        raise ValueError(f"Unsupported routing layer: {name}")
    return int(m[1])


def orient_xy(x, y, orientation, width=0, height=0, component=True):
    # DEF component placement denotes the lower-left of the oriented bounding box.
    if component:
        transforms = {
            'N': (x, y), 'S': (width-x, height-y),
            'FN': (width-x, y), 'FS': (x, height-y),
            'W': (height-y, x), 'E': (y, width-x),
            'FW': (y, x), 'FE': (height-y, width-x),
        }
    else:
        transforms = {
            'N': (x, y), 'S': (-x, -y), 'FN': (-x, y), 'FS': (x, -y),
            'W': (-y, x), 'E': (y, -x), 'FW': (y, x), 'FE': (-y, -x),
        }
    if orientation not in transforms:
        raise ValueError(f"Unknown orientation: {orientation}")
    return transforms[orientation]


class Design:
    def __init__(self, def_path, lef_path, pin_mode='physical'):
        self.def_path, self.lef_path = Path(def_path), Path(lef_path)
        self.pin_mode = pin_mode
        self.layers, self.macros, self.components, self.io, self.nets = {}, {}, {}, {}, {}
        self.io_geometry = {}
        self.grid, self.tracks = {'X': [], 'Y': []}, {}
        self.tsv_width_um = 0.0
        self._read_lef()
        self._read_def()

    def _read_lef(self):
        text = re.sub(r'#[^\n]*', '', self.lef_path.read_text(encoding='utf-8'))
        for m in re.finditer(r'^LAYER\s+(\S+)\s*\n(.*?)^END\s+\1\s*$', text, re.M | re.S):
            name, body = m[1], m[2]
            if re.search(r'\bTYPE\s+ROUTING\s*;', body):
                n = layer_number(name)
                direction = re.search(r'\bDIRECTION\s+(HORIZONTAL|VERTICAL)\s*;', body)
                width = re.search(r'\bWIDTH\s+('+NUMBER+r')\s*;', body)
                if not direction or not width:
                    raise ValueError(f'Missing direction/width in {name}')
                self.layers[n] = {'name': name, 'direction': direction[1], 'width': float(width[1])}
        for m in re.finditer(r'^MACRO\s+(\S+)\s*\n(.*?)^END\s+\1\s*$', text, re.M | re.S):
            name, body = m[1], m[2]
            size = re.search(r'\bSIZE\s+('+NUMBER+r')\s+BY\s+('+NUMBER+r')\s*;', body)
            origin = re.search(r'\bORIGIN\s+('+NUMBER+r')\s+('+NUMBER+r')\s*;', body)
            if not size:
                raise ValueError(f'Missing SIZE: {name}')
            pins = {}
            for p in re.finditer(r'^\s*PIN\s+(\S+)\s*\n(.*?)^\s*END\s+\1\s*$', body, re.M | re.S):
                shapes, current_layer = [], None
                for token in re.finditer(r'\bLAYER\s+(\S+)\s*;|\bRECT\s+('+NUMBER+r')\s+('+NUMBER+r')\s+('+NUMBER+r')\s+('+NUMBER+r')\s*;|\bPOLYGON\b', p[2]):
                    if token[0] == 'POLYGON':
                        raise ValueError(f'POLYGON pin geometry is not yet supported: {name}/{p[1]}')
                    if token[1]:
                        current_layer = layer_number(token[1])
                    else:
                        if current_layer is None:
                            raise ValueError(f'Pin RECT without LAYER: {name}/{p[1]}')
                        shapes.append((current_layer, tuple(float(token[i]) for i in range(2, 6))))
                if not shapes:
                    raise ValueError(f'No rectangle pin geometry: {name}/{p[1]}')
                pins[p[1]] = shapes
            self.macros[name] = Macro(float(size[1]), float(size[2]),
                                     (float(origin[1]), float(origin[2])) if origin else (0., 0.), pins)
        # Use the actual cross-tier VIA metal enclosure as the TSV landing pad size.
        for m in re.finditer(r'^VIA\s+(\S+)[^\n]*\n(.*?)^END\s+\1\s*$', text, re.M | re.S):
            if re.search(r'\bLAYER\s+metal10\s*;', m[2], re.I) and re.search(r'\bLAYER\s+metal11\s*;', m[2], re.I):
                rects = re.findall(r'\bRECT\s+('+NUMBER+r')\s+('+NUMBER+r')\s+('+NUMBER+r')\s+('+NUMBER+r')\s*;', m[2])
                self.tsv_width_um = max(self.tsv_width_um, *(max(abs(float(c)-float(a)), abs(float(d)-float(b))) for a,b,c,d in rects))
        if not self.layers or not self.macros:
            raise ValueError('LEF contains no usable layers or macros')

    def _read_def(self):
        text = re.sub(r'#[^\n]*', '', self.def_path.read_text(encoding='utf-8'))
        units = re.search(r'UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;', text)
        area = re.search(r'DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*;', text)
        if not units or not area:
            raise ValueError('Missing DEF units or rectangular DIEAREA')
        self.dbu, self.die = int(units[1]), tuple(int(area[i]) for i in range(1,5))
        for m in re.finditer(r'GCELLGRID\s+([XY])\s+(-?\d+)\s+DO\s+(\d+)\s+STEP\s+(\d+)\s*;', text):
            self.grid[m[1]].append(tuple(int(m[i]) for i in range(2,5)))
        for m in re.finditer(r'TRACKS\s+([XY])\s+(-?\d+)\s+DO\s+(\d+)\s+STEP\s+(\d+)\s+LAYER\s+([^;]+);', text):
            for name in m[5].split():
                self.tracks.setdefault((layer_number(name), m[1]), []).append(tuple(int(m[i]) for i in range(2,5)))
        placement = r'\+\s+(?:PLACED|FIXED|COVER)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\w+)'
        for section in ('COMPONENTS', 'PINS', 'NETS'):
            sec = re.search(r'^\s*'+section+r'\s+(\d+)\s*;(.*?)^\s*END\s+'+section+r'\s*$', text, re.M | re.S)
            if not sec:
                raise ValueError(f'Missing DEF section {section}')
            count = 0
            for rec in re.finditer(r'(?:^|\n)\s*-\s+(.*?);', sec[2], re.S):
                record = rec[1]
                fields = record.split()
                name = fields[0]
                count += 1
                if section == 'COMPONENTS':
                    p = re.search(placement, record)
                    if not p:
                        raise ValueError(f'Unplaced component: {name}')
                    self.components[name] = (fields[1], int(p[1]), int(p[2]), p[3])
                elif section == 'PINS':
                    p = re.search(placement, record)
                    layer = re.search(r'\+\s+LAYER\s+(\S+)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)', record)
                    if not p or not layer:
                        raise ValueError(f'Unsupported IO pin: {name}')
                    self.io[name] = (int(p[1]), int(p[2]), p[3], layer_number(layer[1]), tuple(int(layer[i]) for i in range(2,6)))
                    ports=re.split(r'\+\s*PORT\b',record)
                    ports=ports[1:] if len(ports)>1 else ports
                    shapes=[]
                    for port in ports:
                        placed=re.search(placement,port)
                        geometries=re.findall(r'\+\s+LAYER\s+(\S+)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)',port)
                        if not placed or not geometries:
                            raise ValueError(f'Unsupported IO PORT: {name}')
                        px,py=int(placed[1]),int(placed[2])
                        for ln,a,b,c,d in geometries:
                            a,b,c,d=map(int,(a,b,c,d))
                            corners=[orient_xy(xx,yy,placed[3],component=False) for xx,yy in ((a,b),(a,d),(c,b),(c,d))]
                            shapes.append((layer_number(ln),(px+min(v[0] for v in corners),py+min(v[1] for v in corners),
                                                            px+max(v[0] for v in corners),py+max(v[1] for v in corners))))
                    self.io_geometry[name]=shapes
                else:
                    connections = re.findall(r'\(\s*(\S+)\s+(\S+)\s*\)', record.split('+',1)[0])
                    if not connections:
                        raise ValueError(f'Net without connections: {name}')
                    self.nets[name] = connections
            if count != int(sec[1]):
                raise ValueError(f'{section}: declared {sec[1]}, parsed {count}')

    def pin(self, instance, pin_name):
        if self.pin_mode!='legacy':
            # Q109: one actual shape suffices. Choose an accessible part, not
            # the envelope between disjoint shapes or an off-die shape center.
            a,b,c,d=self.die
            for layer,rect in self.pin_shapes(instance,pin_name):
                xl,yl,xh,yh=max(a,rect[0]),max(b,rect[1]),min(c,rect[2]),min(d,rect[3])
                if xl<=xh and yl<=yh:
                    return Point((xl+xh)/2,(yl+yh)/2,layer)
            raise ValueError(f'Pin has no access inside DIEAREA: {instance}/{pin_name}')
        if instance == 'PIN':
            x, y, orientation, layer, rect = self.io[pin_name]
            if self.pin_mode == 'legacy':
                return Point(x, y, layer)
            dx, dy = orient_xy((rect[0]+rect[2])/2, (rect[1]+rect[3])/2, orientation, component=False)
            return Point(x+dx, y+dy, layer)
        macro_name, x, y, orientation = self.components[instance]
        macro = self.macros[macro_name]
        layer, rect = macro.pins[pin_name][0]
        dx, dy = (rect[0]+rect[2])/2, (rect[1]+rect[3])/2
        if self.pin_mode != 'legacy':
            dx, dy = orient_xy(dx+macro.origin[0], dy+macro.origin[1], orientation, macro.width, macro.height)
        return Point(x+dx*self.dbu, y+dy*self.dbu, layer)

    def points(self, name):
        return list(dict.fromkeys(self.pin(*connection) for connection in self.nets[name]))

    def pin_shapes(self,instance,pin_name):
        """All actual RECTs/PORTs in world DBU, never their bounding envelope."""
        if instance=='PIN':
            return self.io_geometry[pin_name]
        macro_name,x,y,orientation=self.components[instance]
        macro=self.macros[macro_name]
        result=[]
        for layer,rect in macro.pins[pin_name]:
            corners=[orient_xy(rect[i]+macro.origin[0],rect[j]+macro.origin[1],orientation,macro.width,macro.height)
                     for i,j in ((0,1),(0,3),(2,1),(2,3))]
            result.append((layer,(x+min(p[0] for p in corners)*self.dbu,y+min(p[1] for p in corners)*self.dbu,
                                  x+max(p[0] for p in corners)*self.dbu,y+max(p[1] for p in corners)*self.dbu)))
        return result

    def net_pin_shapes(self,name):
        return [self.pin_shapes(*connection) for connection in self.nets[name]]
