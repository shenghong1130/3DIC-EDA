from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from analyze_congestion import analyze


class CongestionDecomposition(unittest.TestCase):
    def fixture(self):
        return dict(x_lines=[0, 10, 30], y_lines=[0, 10], nets=[dict(net='a', nodes=[2, 3], cross_die=False)],
            resources=[[2, 3, 1, 6, 4], [3, 0, 1, 0, 1]], metrics=dict(overflow_local=5))

    def test_exact_baseline_increment_and_hotspots(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'result.json'
            raw = json.dumps(self.fixture()).encode()
            p.write_bytes(raw)
            result = analyze(p, 1)
            self.assertEqual(result['totals']['total_local_overflow'], 5)
            self.assertEqual(result['totals']['fixed_baseline_overflow'], 3)
            self.assertEqual(result['totals']['routing_incremental_overflow'], 2)
            self.assertEqual(result['totals']['zero_capacity_demand'], 1)
            self.assertEqual(result['hotspots'][0]['node'], 2)
            self.assertEqual(result['hotspots'][0]['bounds_dbu'], [0, 0, 10, 10])
            self.assertEqual(p.read_bytes(), raw)

    def test_detect_inconsistent_resources(self):
        for field in ['usage', 'overflow']:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / 'result.json'
                data = self.fixture()
                if field == 'usage':
                    data['nets'][0]['nodes'].append(4)
                else:
                    data['resources'][0][-1] = 99
                p.write_text(json.dumps(data), encoding='utf8')
                with self.assertRaises(ValueError):
                    analyze(p)

    def test_tsv_extra_two_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'result.json'
            data = self.fixture()
            data['nets'][0]['cross_die'] = True
            data['resources'][0][2] = 2
            data['resources'][0][-1] = 5
            data['resources'][1][2] = data['resources'][1][-1] = 2
            data['metrics']['overflow_local'] = 7
            p.write_text(json.dumps(data), encoding='utf8')
            self.assertEqual(analyze(p)['expected_usage'], 4)
