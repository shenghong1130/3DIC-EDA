"""Read-only, bounded-memory decomposition of real result.json resources.

Does not rerun Router, change capacities or infer official scores. Reuses the
UI's incremental JSON reader; one Net/resource and a bounded hotspot heap live
in memory, rather than the entire result. Output paths may not already exist.
"""
import argparse
import heapq
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ui'))
from result_store import Cursor, fingerprint


def analyze(path, top=30):
    path = Path(path)
    signature = fingerprint(path)
    cursor = Cursor(path)
    layers = {}
    hotspots = []
    root = {}
    expected_usage = net_count = tsv_count = resources = 0
    started = time.perf_counter()
    try:
        cursor.take('{')
        while cursor.char() != '}':
            key = cursor.value()[0]
            cursor.take(':')
            if key == 'nets':
                for net, _, _ in cursor.array():
                    net_count += 1
                    expected_usage += len(net['nodes']) + 2 * int(net['cross_die'])
                    tsv_count += int(net['cross_die'])
            elif key == 'resources':
                nx, ny = len(root['x_lines']) - 1, len(root['y_lines']) - 1
                for value, _, _ in cursor.array():
                    node, capacity, usage, fixed, stored = map(int, value)
                    x, y, layer = node % nx, node // nx % ny, node // (nx * ny)
                    total = max(0, usage + fixed - capacity)
                    baseline = max(0, fixed - capacity)
                    if total != stored:
                        raise ValueError('Stored overflow does not match resource formula')
                    row = layers.setdefault(layer, dict(resources=0, capacity=0, usage=0, fixed=0,
                        total_local_overflow=0, fixed_baseline_overflow=0, routing_incremental_overflow=0,
                        hotspot_gcells=0, zero_capacity_demand_gcells=0, zero_capacity_demand=0,
                        fixed_over_capacity_gcells=0))
                    for name, number in [('resources', 1), ('capacity', capacity), ('usage', usage), ('fixed', fixed),
                        ('total_local_overflow', total), ('fixed_baseline_overflow', baseline),
                        ('routing_incremental_overflow', total - baseline), ('hotspot_gcells', int(total > 0)),
                        ('zero_capacity_demand_gcells', int(capacity == 0 and usage + fixed > 0)),
                        ('zero_capacity_demand', usage + fixed if capacity == 0 else 0),
                        ('fixed_over_capacity_gcells', int(fixed > capacity))]:
                        row[name] += number
                    if total and top:
                        item = (total, node, capacity, usage, fixed, x, y, layer)
                        if len(hotspots) < top:
                            heapq.heappush(hotspots, item)
                        elif item > hotspots[0]:
                            heapq.heapreplace(hotspots, item)
                    resources += 1
            elif key in ['x_lines', 'y_lines', 'metrics', 'layers', 'algorithm', 'dbu_per_micron', 'design']:
                root[key] = cursor.value()[0]
            else:
                cursor.skip()
            if cursor.char() == '}':
                break
            cursor.take(',')
        cursor.take('}')
        if cursor.char():
            raise ValueError('Trailing JSON data')
    finally:
        cursor.close()
    if fingerprint(path) != signature:
        raise ValueError('Source changed during analysis')
    totals = {k: sum(row[k] for row in layers.values()) for k in next(iter(layers.values()), {})}
    if totals.get('usage', 0) != expected_usage:
        raise ValueError('Occupancy does not equal per-Net unique nodes plus two TSV interface units')
    if totals.get('total_local_overflow', 0) != root.get('metrics', {}).get('overflow_local'):
        raise ValueError('Resource sum differs from result metrics')
    hot = []
    for overflow, node, cap, usage, fixed, x, y, layer in sorted(hotspots, reverse=True):
        hot.append(dict(node=node, layer=layer, gx=x, gy=y, capacity=cap, usage=usage, fixed=fixed,
            total_overflow=overflow, fixed_baseline=max(0, fixed-cap),
            bounds_dbu=[root['x_lines'][x], root['y_lines'][y], root['x_lines'][x+1], root['y_lines'][y+1]]))
    return dict(source=str(path.resolve()), source_signature=signature, design=root.get('design'),
        nets=net_count, tsv_count=tsv_count, expected_usage=expected_usage, totals=totals,
        by_layer={str(k): layers[k] for k in sorted(layers)}, hotspots=hot,
        seconds=time.perf_counter()-started, maximum_reader_characters=cursor.maximum,
        limitations=['Resource rows have no Pin/OBS provenance; their individual or overlap contributions cannot be reconstructed.',
            'Capacity totals cover emitted demanded resources, not all unused GCells.',
            'These are local node-model diagnostics, not official congestion or an official score.'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--result', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--top', type=int, default=30)
    a = p.parse_args()
    if not 0 <= a.top <= 1000:
        p.error('--top must be 0..1000')
    if a.output.exists() or a.output.resolve() == a.result.resolve():
        p.error('Output must be a new path distinct from the source')
    value = analyze(a.result, a.top)
    with a.output.open('x', encoding='utf8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({'nets': value['nets'], 'totals': value['totals'], 'seconds': value['seconds']}))


if __name__ == '__main__':
    main()
