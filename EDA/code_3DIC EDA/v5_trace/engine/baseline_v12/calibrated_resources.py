"""Track-exact resource experiments, calibrated against saved platform outputs.

The projected PIN view is an empirical compatibility hypothesis, not an official
geometry rule. It never changes pin connectivity, TSV legality, or source files.
"""
from bisect import bisect_left, bisect_right
from collections import defaultdict
import re
from design import NUMBER, orient_xy
from routing_resources import RoutingResources, covered


class CalibratedResources(RoutingResources):
    def __init__(self, design, profile='physical', own_pin_credit=False):
        if profile not in ('physical', 'projected', 'legacy'):
            raise ValueError('Unknown resource profile')
        self.profile = profile
        self.own_pin_credit = own_pin_credit
        super().__init__(design)

    def _fixed_shapes(self):
        d = self.d
        shapes = {}
        text = re.sub(r'#[^\n]*', '', d.lef_path.read_text(encoding='utf-8'))
        for match in re.finditer(r'^MACRO\s+(\S+)\s*\n(.*?)^END\s+\1\s*$', text, re.M | re.S):
            macro = d.macros[match[1]]
            rs = []
            for pin in macro.pins.values():
                rs.extend((pin[0][0] if self.profile == 'projected' else layer, box) for layer, box in pin)
            for obs in re.finditer(r'^\s*OBS\s*\n(.*?)^\s*END\s*$', match[2], re.M | re.S):
                layer = None
                for token in re.finditer(r'\bLAYER\s+(\S+)[^;]*;|\bRECT\s+('+NUMBER+r')\s+('+NUMBER+r')\s+('+NUMBER+r')\s+('+NUMBER+r')\s*;', obs[1]):
                    if token[1]:
                        ln = re.fullmatch(r'(?:metal|m)(\d+)', token[1], re.I)
                        layer = int(ln[1]) if ln else None
                    elif layer in d.layers:
                        rs.append((layer, tuple(float(token[i]) for i in range(2, 6))))
            shapes[match[1]] = list(dict.fromkeys(rs))
        for macro_name, x, y, orientation in d.components.values():
            macro = d.macros[macro_name]
            for layer, box in shapes[macro_name]:
                if layer not in d.layers:
                    continue
                corners = [orient_xy(box[i]+macro.origin[0], box[j]+macro.origin[1], orientation, macro.width, macro.height)
                           for i, j in ((0, 1), (0, 3), (2, 1), (2, 3))]
                # Decimal LEF coordinates in these inputs are exact DBU values;
                # suppress floating multiplication noise at track boundaries.
                yield (round(x+min(p[0] for p in corners)*d.dbu, 8),
                       round(y+min(p[1] for p in corners)*d.dbu, 8),
                       round(x+max(p[0] for p in corners)*d.dbu, 8),
                       round(y+max(p[1] for p in corners)*d.dbu, 8), layer)
        for pins in d.io_geometry.values():
            for layer, box in pins:
                yield (*box, layer)

    def _seed_obstacles(self):
        if self.profile == 'legacy':
            return super()._seed_obstacles()
        masks = {}
        for x1, y1, x2, y2, layer in self._fixed_shapes():
            tracks = self.tracks[layer]
            horizontal = self.d.layers[layer]['direction'] == 'HORIZONTAL'
            main = self.y if horizontal else self.x
            first, stop = (bisect_left(tracks, y1), bisect_left(tracks, y2)) if horizontal else (bisect_left(tracks, x1), bisect_left(tracks, x2))
            for row in covered(self.y, y1, y2):
                for col in covered(self.x, x1, x2):
                    cell = row if horizontal else col
                    base = bisect_left(tracks, main[cell])
                    end = (bisect_right if cell == len(main)-2 else bisect_left)(tracks, main[cell+1])
                    a, b = max(first, base), min(stop, end)
                    key = layer*self.cells+row*self.cols+col
                    masks[key] = masks.get(key, 0) | (((1 << (b-a))-1) << (a-base) if b > a else 0)
        self.usage = {key: mask.bit_count() for key, mask in masks.items()}
        self.obstacle_layer_cells = len(self.usage)

    def pin_cells(self, name):
        if not self.own_pin_credit:
            return set()
        result = set()
        for connection in self.d.nets[name]:
            shapes = self.d.pin_shapes(*connection)
            for layer, box in shapes:
                if self.profile == 'projected' and connection[0] != 'PIN' and layer != shapes[0][0]:
                    continue
                if box[0] < box[2] and box[1] < box[3]:
                    result.update(self.footprint((*box, layer)))
        return result
