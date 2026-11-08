"""Hard TSV landing exclusion from physical LEF/DEF PIN and OBS geometry.

This is deliberately independent of track-demand estimates and own-net PIN
credits. A partially occupied interface GCell is not an empty TSV landing.
"""
from calibrated_resources import CalibratedResources
from guide_rules import grid_for
from routing_resources import covered


class TSVObstacles:
    def __init__(self, design, shapes):
        self.grid = grid_for(design)
        self.blocked = set()
        for x1, y1, x2, y2, layer in shapes:
            if layer not in (10, 11) or x1 >= x2 or y1 >= y2:
                continue
            for row in covered(self.grid.y, y1, y2):
                for col in covered(self.grid.x, x1, x2):
                    self.blocked.add((layer, col, row))

    def conflicts(self, center):
        col, row = self.grid.cell(*center)
        return [(layer, col, row) for layer in (10, 11)
                if (layer, col, row) in self.blocked]

    def check(self, name, box):
        center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        conflicts = self.conflicts(center)
        if conflicts:
            raise ValueError(f'{name}: TSV landing GCell contains PIN/OBS: {conflicts}; '
                             'own-net PIN credit does not exempt a TSV landing')


def obstacles_for(design):
    if not hasattr(design, '_tsv_obstacles'):
        # Synthetic geometry-only test designs have no physical library.
        if hasattr(design, 'lef_path'):
            physical = object.__new__(CalibratedResources)
            physical.d = design
            physical.profile = 'physical'
            shapes = physical._fixed_shapes()
        else:
            shapes = ()
        design._tsv_obstacles = TSVObstacles(design, shapes)
    return design._tsv_obstacles
