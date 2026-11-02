"""Hand-computed independent geometry fixtures; no Router/reference oracle."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import audit_submission_tsv as a


def lef(pin_layer=11, pin_rect='0.1 0.1 0.2 0.2', extra='', obs=''):
    layers = '\n'.join(f'LAYER metal{i}\n TYPE ROUTING ;\nEND metal{i}' for i in range(1, 21))
    return layers + f'''
MACRO C
 SIZE 1 BY 1 ;
 ORIGIN 0 0 ;
 PIN P
 PORT
 LAYER metal{pin_layer} ;
 RECT {pin_rect} ;
 {extra}
 END
 END P
 {obs}
END C
'''


def design(x=4000, y=4000, orient='N', io=False):
    return f'''
VERSION 5.8 ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 12000 12000 ) ;
GCELLGRID X 0 DO 5 STEP 3000 ;
GCELLGRID Y 0 DO 5 STEP 3000 ;
TRACKS X 9000 DO 1 STEP 100 LAYER metal11 ;
COMPONENTS 1 ;
- U C + PLACED ( {x} {y} ) {orient} ;
END COMPONENTS
PINS {1 if io else 0} ;
{('- B + NET n + LAYER metal1 ( -50 -20 ) ( 50 20 ) + FIXED ( 1000 1000 ) W ;') if io else ''}
END PINS
NETS 1 ;
- n ( U P ) {'( PIN B )' if io else ''} ;
END NETS
END DESIGN
'''


class AuditTests(unittest.TestCase):
    def model(self, text=None, d=None):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        p = Path(td.name)
        (p / 'a.lef').write_text(text or lef(), encoding='utf8')
        (p / 'a.def').write_text(d or design(), encoding='utf8')
        m = a.Geometry(p / 'a.lef', p / 'index.sqlite'); self.addCleanup(m.close)
        m.read_def(p / 'a.def')
        return m, p

    def flags(self, m, col=1, row=1, center=(4500, 4500)):
        return a.rule_evidence(m, col, row, center)[0]

    def test_center_pin_and_same_net_no_exemption(self):
        m, _ = self.model()
        f = self.flags(m)
        self.assertEqual(f['A_C'], 1)
        self.assertEqual(f['center_PIN'], 1)
        # n owns U.P. Ownership is never consulted by rule_evidence.
        self.assertEqual(m.db.execute('SELECT inst,pin FROM terminal').fetchall(), [('U', 'P')])

    def test_center_obs(self):
        m, _ = self.model(lef(pin_layer=1, pin_rect='0.6 0.6 0.7 0.7', obs='OBS\n LAYER metal10 ;\n RECT 0.1 0.1 0.2 0.2 ;\n END'))
        self.assertEqual(self.flags(m)['center_OBS'], 1)
        self.assertEqual(self.flags(m)['A_C'], 1)

    def test_other_routing_layer_is_separate_model(self):
        m, _ = self.model(lef(pin_layer=3))
        f = self.flags(m)
        self.assertEqual(f['A_C'], 0)
        self.assertEqual(f['A_D'], 1)

    def test_footprint_neighbor_pin(self):
        m, _ = self.model(d=design(x=6100))
        f = self.flags(m)
        self.assertEqual(f['A_D'], 0)
        self.assertEqual(f['B_C'], 1)
        self.assertEqual(f['neighbor_PIN'], 1)

    def test_boundary_touch_does_not_become_positive(self):
        m, _ = self.model(d=design(x=5800))  # Pin x=5900..6000
        f, events, _, _ = a.rule_evidence(m, 2, 1, (7500, 4500))
        self.assertEqual(f['A_C'], 0)
        self.assertTrue(any(e['center_cell'] and e['geometry_cell_relation'] == 'boundary' for e in events))

    def test_all_orientation_matrices_and_origin(self):
        expected = {'N': (1, 2, 3, 4), 'S': (7, 16, 9, 18), 'E': (2, 7, 4, 9),
                    'W': (16, 1, 18, 3), 'FN': (7, 2, 9, 4), 'FS': (1, 16, 3, 18),
                    'FE': (2, 1, 4, 3), 'FW': (16, 7, 18, 9)}
        for ori, r in expected.items():
            self.assertEqual(a.transformed((1, 2, 3, 4), (10, 20), (0, 0), ori), r)
        self.assertEqual(a.transformed((-1, -2, 1, 2), (10, 20), (1, 2), 'N'), (0, 0, 2, 4))

    def test_rotated_mirrored_instance_actual_geometry(self):
        for ori, expected in [('FS', (4100, 4800, 4200, 4900)), ('E', (4100, 4800, 4200, 4900)),
                              ('FN', (4800, 4100, 4900, 4200)), ('W', (4800, 4100, 4900, 4200))]:
            m, _ = self.model(d=design(orient=ori))
            self.assertEqual(list(m.geometry((3000, 3000, 6000, 6000)))[0]['rect_dbu'], expected)

    def test_multilayer_and_no_track_pin(self):
        m, _ = self.model(lef(pin_layer=3, extra='LAYER metal11 ;\n RECT 0.3 0.3 0.4 0.4 ;'))
        f = self.flags(m)
        self.assertEqual(f['A_C'], 1); self.assertEqual(f['A_D'], 2)
        self.assertEqual(m.counts['cross_die_nets'], 1)

    def test_def_io_is_in_dbu_and_rotates_about_origin(self):
        m, _ = self.model(d=design(io=True).replace('+ LAYER metal1', '+ PORT + LAYER metal1'))
        pin = [r for r in m.geometry((900, 900, 1100, 1100)) if r['instance'] == '@IO:B'][0]
        self.assertEqual(pin['rect_dbu'], (980, 950, 1020, 1050))

    def test_inverted_vendor_corners_preserved_but_envelope_indexed(self):
        m, _ = self.model(lef(pin_rect='0.2 0.2 0.1 0.1'))
        pin = list(m.geometry((3000, 3000, 6000, 6000)))[0]
        self.assertEqual(pin['rect_dbu'], (4100, 4100, 4200, 4200))
        self.assertEqual(pin['original_rect'], [0.2, 0.2, 0.1, 0.1])

    def test_empty_resource_macro_is_not_an_obstacle(self):
        m, _ = self.model(lef() + '\nMACRO EMPTY\n SIZE 1 BY 1 ;\nEND EMPTY\n')
        m.instance('EMPTY0', 'EMPTY', 'N', 4000, 4000)
        self.assertEqual(len(list(m.geometry((3000, 3000, 6000, 6000)))), 1)

    def test_extra_overlap_is_ambiguous_and_not_selected_away(self):
        m, p = self.model(lef(pin_layer=1, extra='LAYER metal11 ;\n RECT 0.3 0.3 0.4 0.4 ;'))
        g = p / 'a.guide'
        g.write_text('n\n(\n3000 3000 6000 6000 metal10\n3000 3000 6000 6000 metal11\n6000 6000 9000 9000 metal10\n6000 6000 9000 9000 metal11\n)\n')
        r = a.audit(m, g, 'fixture', 'v', p)
        self.assertEqual(r['counts']['ambiguous_nets'], 1)
        self.assertEqual(r['counts']['tsv_candidates'], 2)
        self.assertFalse(r['strict_submission_ready'])

    def test_missing_cross_die_tsv_is_violation_not_clean(self):
        m, p = self.model(lef(pin_layer=1, extra='LAYER metal11 ;\n RECT 0.3 0.3 0.4 0.4 ;'))
        g = p / 'missing.guide'; g.write_text('n\n(\n3000 3000 6000 6000 metal1\n)\n')
        r = a.audit(m, g, 'fixture', 'v', p)
        self.assertEqual(r['classification'], 'CONFIRMED_VIOLATION')
        self.assertFalse(r['strict_submission_ready'])

    def test_outermost_clipped_grid_and_touch(self):
        g = a.Grid({'X': [0, 3, 6, 9], 'Y': [0, 3, 6, 9]}, (0, 0, 8, 8))
        self.assertEqual(g.cell(2, 2), (6, 6, 8, 8))
        self.assertEqual(list(g.cells((6, 6, 8, 8))), [(2, 2, 'positive')])
        self.assertIn((1, 2, 'boundary'), list(g.cells((6, 6, 8, 8), touch=True)))

    def test_cell_enumeration_has_explicit_budget(self):
        g = a.Grid({'X': list(range(1001)), 'Y': list(range(1001))}, (0, 0, 1000, 1000))
        with self.assertRaises(a.Incomplete):
            list(g.cells((0, 0, 1000, 1000)))

    def test_official_coordinate_regressions_not_rule_exceptions(self):
        for case, (col, row, _) in a.OFFICIAL.items():
            raw = {'X': [-2172 + 3000 * n for n in range(1400)], 'Y': [-2172 + 3000 * n for n in range(1400)]}
            g = a.Grid(raw, (-2172, -2172, raw['X'][-1] - 100, raw['Y'][-1] - 200))
            c = g.cell(col, row)
            self.assertEqual(c[:2], (-2172 + col * 3000, -2172 + row * 3000), case)
            self.assertEqual(list(g.cells(c)), [(col, row, 'positive')], case)
            self.assertNotEqual(g.cell(col - 1, row - 1), c)

    def test_truncated_or_unsupported_inputs_are_incomplete(self):
        with self.assertRaises(a.Incomplete):
            self.model(lef().replace('RECT 0.1 0.1 0.2 0.2', 'POLYGON 0 0 1 0 1 1'))
        m, p = self.model()
        f = p / 'bad.guide'; f.write_text('n\n(\n0 0 3000 3000 metal10\n')
        with self.assertRaises(a.Incomplete):
            list(a.guide_nets(f))


if __name__ == '__main__':
    unittest.main()
