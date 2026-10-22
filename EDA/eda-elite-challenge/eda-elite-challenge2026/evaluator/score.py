#!/usr/bin/env python3

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence
import common
from checker import check
from checker import layer_directions

import tse_framework_pybind_def as def_pybind
import tse_framework_pybind_lef as lef_pybind


WIRELENGTH_WEIGHT = 0.45
CONGESTION_WEIGHT = 0.30
THERMAL_WEIGHT = 0.25
OVERFLOW_LIMIT = 20.0
DEFAULT_TSV_COST_DBU = 2000.0
DEFAULT_VIA_COST_DBU = 0.0



@dataclass(frozen=True)
class TrackPattern:
    axis: str
    start: float
    count: int
    step: float
    layer: str

    def coordinates(self) -> list[float]:
        return [self.start + index * self.step for index in range(self.count)]

@dataclass(frozen=True)
class DefData:
    path: Path
    die_name: str
    dbu_per_micron: float
    die: common.DieArea
    tracks: dict[str, dict[str, list[TrackPattern]]]
    gcell_statements: dict[str, list[tuple[float, int, float]]]


@dataclass(frozen=True)
class GCellGrid:
    x_lines: tuple[float, ...]
    y_lines: tuple[float, ...]
    die: common.DieArea
    dbu_per_micron: float

    @property
    def cols(self) -> int:
        return len(self.x_lines) - 1

    @property
    def rows(self) -> int:
        return len(self.y_lines) - 1


@dataclass(frozen=True)
class TSVPoint:
    net: str
    x_dbu: float
    y_dbu: float
    x_um_local: float
    y_um_local: float
    gcell_col: int
    gcell_row: int
    source: str
    overlap_area_dbu2: float


def canonical_layer(layer_name: str) -> str:
    number = common.parse_layer_num(layer_name)
    return f"Metal{number}" if number else layer_name.strip()


def empty_matrix(rows: int, cols: int, value: float = 0.0) -> list[list[float]]:
    return [[float(value) for _ in range(cols)] for _ in range(rows)]


def flatten(matrix: Sequence[Sequence[float]]) -> list[float]:
    return [float(value) for row in matrix for value in row]

def parse_def(path: Path, die_name: str) -> DefData:
    content = path.read_text(encoding="utf-8", errors="replace")
    units = re.search(
        r"UNITS\s+DISTANCE\s+MICRONS\s+([\d.]+)\s*;",
        content,
        re.IGNORECASE,
    )
    if not units:
        raise ValueError(f"{path}: missing UNITS DISTANCE MICRONS")
    dbu = float(units.group(1))
    if dbu <= 0:
        raise ValueError(f"{path}: invalid DBU per micron {dbu}")

    die_match = re.search(
        r"DIEAREA\s*\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)\s*"
        r"\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)\s*;",
        content,
        re.IGNORECASE,
    )
    if not die_match:
        raise ValueError(f"{path}: missing rectangular DIEAREA")
    die = common.DieArea(*map(float, die_match.groups()))
    if die.width <= 0 or die.height <= 0:
        raise ValueError(f"{path}: invalid DIEAREA {die}")

    tracks: dict[str, dict[str, list[TrackPattern]]] = defaultdict(
        lambda: {"X": [], "Y": []}
    )
    track_re = re.compile(
        r"TRACKS\s+([XY])\s+(-?[\d.]+)\s+DO\s+(\d+)\s+"
        r"STEP\s+([\d.]+)\s+LAYER\s+(\w+)\s*;",
        re.IGNORECASE,
    )
    for match in track_re.finditer(content):
        axis = match.group(1).upper()
        start = float(match.group(2))
        count = int(match.group(3))
        step = float(match.group(4))
        layer = match.group(5)
        if count <= 0 or step <= 0:
            raise ValueError(f"{path}: invalid TRACKS statement {match.group(0)}")
        tracks[layer][axis].append(TrackPattern(axis, start, count, step, layer))
    if not tracks:
        raise ValueError(f"{path}: no TRACKS statements")

    gcell: dict[str, list[tuple[float, int, float]]] = {"X": [], "Y": []}
    gcell_re = re.compile(
        r"GCELLGRID\s+([XY])\s+(-?[\d.]+)\s+DO\s+(\d+)\s+"
        r"STEP\s+([\d.]+)\s*;",
        re.IGNORECASE,
    )
    for match in gcell_re.finditer(content):
        axis = match.group(1).upper()
        item = (float(match.group(2)), int(match.group(3)), float(match.group(4)))
        if item[1] < 2 or item[2] <= 0:
            raise ValueError(f"{path}: invalid GCELLGRID statement {match.group(0)}")
        gcell[axis].append(item)
    if not gcell["X"] or not gcell["Y"]:
        raise ValueError(f"{path}: both GCELLGRID X and Y are required")

    return DefData(
        path=path,
        die_name=die_name,
        dbu_per_micron=dbu,
        die=die,
        tracks={layer: dict(axes) for layer, axes in tracks.items()},
        gcell_statements=gcell,
    )

def expand_grid_lines(
    statements: list[tuple[float, int, float]],
    die_lower: float,
    die_upper: float,
    axis: str,
) -> tuple[tuple[float, ...], list[str]]:
    values = [
        start + index * step
        for start, count, step in statements
        for index in range(count)
    ]
    lines = sorted(set(values))
    warnings: list[str] = []
    if len(lines) < 2:
        raise ValueError(f"GCELLGRID {axis} cannot form an interval")
    if lines[0] > die_lower:
        lines.insert(0, die_lower)
        warnings.append(f"GCELLGRID {axis} was extended to DIEAREA lower boundary")
    if lines[-1] < die_upper:
        lines.append(die_upper)
        warnings.append(
            f"GCELLGRID {axis} ended before DIEAREA; a clipped edge GCell was appended"
        )
    return tuple(lines), warnings


def build_gcell_grid(
    dbu: float,
    die: common.DieArea,
    statements: dict[str, list[tuple[float, int, float]]],
) -> tuple[GCellGrid, list[str]]:
    xs, x_warnings = expand_grid_lines(statements["X"], die.x1, die.x2, "X")
    ys, y_warnings = expand_grid_lines(statements["Y"], die.y1, die.y2, "Y")
    return GCellGrid(xs, ys, die, dbu), x_warnings + y_warnings


def locate_interval(lines: tuple[float, ...], coordinate: float) -> int | None:
    if coordinate < lines[0] or coordinate > lines[-1]:
        return None
    index = bisect.bisect_right(lines, coordinate) - 1
    if index == len(lines) - 1:
        index -= 1
    return index if 0 <= index < len(lines) - 1 else None


def rectangle_cell_indices(lower: float, upper: float, lines: tuple[float, ...]) -> range:
    if upper < lower:
        lower, upper = upper, lower
    if math.isclose(lower, upper):
        index = locate_interval(lines, lower)
        return range(0, 0) if index is None else range(index, index + 1)
    start = max(0, bisect.bisect_right(lines, lower) - 1)
    stop = min(len(lines) - 1, bisect.bisect_left(lines, upper))
    return range(start, stop)


def guide_hpwl(rectangles: Sequence[common.GuideRect], tsv_cost: float) -> float:
    if not rectangles:
        return 0.0
    # Guide rectangles are routing corridors, not pin boxes.  Using their full
    # extents inflates the lower bound by one GCell on every net.  Centers give
    # a stable proxy for the terminal span when only guide/segment artifacts
    # are available.
    centers = [common.rect_center(rect) for rect in rectangles]
    xs = [point[0] for point in centers]
    ys = [point[1] for point in centers]
    dies = {common.die_of_layer(rect[4]) for rect in rectangles}
    return max(xs) - min(xs) + max(ys) - min(ys) + (tsv_cost if len(dies) > 1 else 0.0)

def compute_wire_length(nets_guide: dict[str, list[common.GuideRect]], net_to_mst_edges : dict[str, tuple[float, list[tuple[int, int, float]]]], tsv_cost: float, via_cost : float):
    net_wirelengths : dict[str, float] = dict()
    total_wire_length = 0.0
    for net_name, mst_edges in net_to_mst_edges.items():
        rectangles = nets_guide[net_name]
        for i1, i2, cost in mst_edges[1]:
            rect1 = rectangles[i1]
            rect2 = rectangles[i2]
            layer1 = rect1[4]
            layer2 = rect2[4]
            if layer1 != layer2:
                total_wire_length += cost
                net_wirelengths[net_name] = net_wirelengths.get(net_name, 0) + cost
            else:
                if layer_directions[rect1[4]] == "HORIZONTAL":
                    wl = max(rect1[0], rect1[2], rect2[0], rect2[2]) - min(rect1[0], rect1[2], rect2[0], rect2[2])
                else:
                    wl = max(rect1[1], rect1[3], rect2[1], rect2[3]) - min(rect1[1], rect1[3], rect2[1], rect2[3])
                total_wire_length += wl
                net_wirelengths[net_name] = net_wirelengths.get(net_name, 0) + wl

    total_hpwl = 0.0
    net_hpwls: dict[str, float] = {}
    for net_name, rectangles in nets_guide.items():
        net_hpwl = guide_hpwl(rectangles, tsv_cost)
        total_hpwl += net_hpwl
        net_hpwls[net_name] = net_hpwl

    norm_wire_length = total_wire_length / total_hpwl if total_hpwl > 0 else 100.0

    return norm_wire_length, total_wire_length, total_hpwl


def track_count_per_interval(coordinates: Iterable[float], lines: tuple[float, ...]) -> list[int]:
    counts = [0] * (len(lines) - 1)
    for coordinate in coordinates:
        index = locate_interval(lines, coordinate)
        if index is not None:
            counts[index] += 1
    return counts


def build_capacity(
    tracks: dict[str, dict[str, list[TrackPattern]]], grid: GCellGrid
) -> dict[tuple[int, int, str, str], int]:
    capacity: dict[tuple[int, int, str], int] = {}
    for layer, axes in tracks.items():
        xs = [point for pattern in axes.get("X", []) for point in pattern.coordinates()]
        ys = [point for pattern in axes.get("Y", []) for point in pattern.coordinates()]
        x_counts = track_count_per_interval(xs, grid.x_lines)
        y_counts = track_count_per_interval(ys, grid.y_lines)
        for row in range(grid.rows):
            for col in range(grid.cols):
                capacity[(col, row, layer, 'HORIZONTAL')] = x_counts[col]
                capacity[(col, row, layer, 'VERTICAL')] = y_counts[row]
    return capacity


def compute_congestion(
    nets: dict[str, list[common.GuideRect]],
    tracks: dict[str, dict[str, list[TrackPattern]]],
    grid: GCellGrid,
    layer_to_direction : dict[str, str]
):
    demand: dict[tuple[int, int, str], int] = defaultdict(int)
    for rectangles in nets.values():
        for x1, y1, x2, y2, layer in rectangles:
            x1 = max(float(x1), grid.die.x1)
            y1 = max(float(y1), grid.die.y1)
            x2 = min(float(x2), grid.die.x2)
            y2 = min(float(y2), grid.die.y2)
            if x2 < x1 or y2 < y1:
                continue
            for col in rectangle_cell_indices(x1, x2, grid.x_lines):
                for row in rectangle_cell_indices(y1, y2, grid.y_lines):
                    demand[(col, row, layer)] += 1
    capacity = build_capacity(tracks, grid)
    overflow = []
    for key, amount in demand.items():
        new_key = key + (layer_to_direction[key[2]], )
        cap = capacity.get(new_key, 0)
        if amount > cap:
            overflow.append((new_key, amount, cap, amount - cap))
    max_overflow = max((item[3] for item in overflow), default=0)
    total_overflow = sum(item[3] for item in overflow)
    average = total_overflow / len(demand) if demand else 0.0
    return max_overflow, average, total_overflow, overflow, dict(demand), capacity


def load_thermal_json(path: Path) -> tuple[dict[tuple[float, float], float], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError(f"{path}: nodes must be a non-empty list")
    grouped: dict[tuple[float, float], list[float]] = defaultdict(list)
    for index, node in enumerate(nodes):
        if not isinstance(node, list) or len(node) < 4:
            raise ValueError(f"{path}: invalid nodes[{index}]")
        x, y, _z, temperature = map(float, node[:4])
        if not all(math.isfinite(value) for value in (x, y, temperature)):
            raise ValueError(f"{path}: NaN/Inf in nodes[{index}]")
        grouped[(round(x, 9), round(y, 9))].append(temperature)
    projected = {point: sum(values) / len(values) for point, values in grouped.items()}
    return projected, payload


def aggregate_temperature(
    projected: dict[tuple[float, float], float], grid: GCellGrid
) -> tuple[list[list[float]], list[list[float]], int]:
    sums = empty_matrix(grid.rows, grid.cols)
    counts = empty_matrix(grid.rows, grid.cols)
    mapped = 0
    for (x_um, y_um), temperature in projected.items():
        x_dbu = grid.die.x1 + x_um * grid.dbu_per_micron
        y_dbu = grid.die.y1 + y_um * grid.dbu_per_micron
        col = locate_interval(grid.x_lines, x_dbu)
        row = locate_interval(grid.y_lines, y_dbu)
        if col is not None and row is not None:
            sums[row][col] += temperature
            counts[row][col] += 1
            mapped += 1
    heat = empty_matrix(grid.rows, grid.cols)
    missing = []
    for row in range(grid.rows):
        for col in range(grid.cols):
            if counts[row][col] == 0:
                missing.append((col, row))
            else:
                heat[row][col] = sums[row][col] / counts[row][col]
    if missing:
        preview = ", ".join(f"({col},{row})" for col, row in missing[:12])
        raise ValueError(f"thermal JSON leaves {len(missing)} GCells empty; first: {preview}")
    return heat, counts, mapped

def extract_tsv_points(nets: dict[str, list[common.GuideRect]], grid: GCellGrid) -> list[TSVPoint]:
    points: list[TSVPoint] = []
    for net, rectangles in nets.items():
        layers = {common.parse_layer_num(rect[4]) for rect in rectangles}
        if net.startswith("BOTTOM__"):
            if any(layer > common.TSV_LOWER_LAYER_INDEX for layer in layers):
                raise ValueError(f"Inner net: {net} cross die")

        if net.startswith("TOP__"):
            if any(layer < common.TSV_UPPER_LAYER_INDEX for layer in layers):
                raise ValueError(f"Inner net: {net} cross die")

        lower = [rect for rect in rectangles if rect[4] == common.TSV_LOWER_LAYER]
        upper = [rect for rect in rectangles if rect[4] == common.TSV_UPPER_LAYER]
        candidates = []
        for a in lower:
            for b in upper:
                x1, y1 = max(a[0], b[0]), max(a[1], b[1])
                x2, y2 = min(a[2], b[2]), min(a[3], b[3])
                if x1 <= x2 and y1 <= y2 and ((x2 - x1) * (y2 - y1)) > 0:
                    candidates.append(((x2 - x1) * (y2 - y1), x1, y1, x2, y2))
        if not candidates:
            continue

        for area, x1, y1, x2, y2 in candidates:    
            x_dbu, y_dbu = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            col = locate_interval(grid.x_lines, x_dbu)
            row = locate_interval(grid.y_lines, y_dbu)
            if col is not None and row is not None:
                points.append(
                    TSVPoint(
                        net=net,
                        x_dbu=x_dbu,
                        y_dbu=y_dbu,
                        x_um_local=(x_dbu - grid.die.x1) / grid.dbu_per_micron,
                        y_um_local=(y_dbu - grid.die.y1) / grid.dbu_per_micron,
                        gcell_col=col,
                        gcell_row=row,
                        source="M10_M11_guide_overlap",
                        overlap_area_dbu2=float(area),
                    )
                )

    return points


def tsv_matrix(points: Sequence[TSVPoint], grid: GCellGrid) -> list[list[float]]:
    matrix = empty_matrix(grid.rows, grid.cols)
    for point in points:
        matrix[point.gcell_row][point.gcell_col] += 1.0
    return matrix


def pearson_heat_tsv(
    heat: Sequence[Sequence[float]],
    tsvs: Sequence[Sequence[float]],
    ambient: float,
) -> float:
    heat_values = [max(value - ambient, 0.0) for value in flatten(heat)]
    tsv_values = flatten(tsvs)
    if sum(tsv_values) <= 0:
        raise ValueError("no TSVs were available for thermal correlation")
    if sum(heat_values) <= 0:
        raise ValueError("thermal map contains no temperature rise above ambient")
    heat_values = [value / sum(heat_values) for value in heat_values]
    tsv_values = [value / sum(tsv_values) for value in tsv_values]
    heat_mean = sum(heat_values) / len(heat_values)
    tsv_mean = sum(tsv_values) / len(tsv_values)
    numerator = sum(
        (h - heat_mean) * (t - tsv_mean) for h, t in zip(heat_values, tsv_values)
    )
    denominator = math.sqrt(sum((h - heat_mean) ** 2 for h in heat_values)) * math.sqrt(
        sum((t - tsv_mean) ** 2 for t in tsv_values)
    )
    if math.isclose(denominator, 0.0, abs_tol=1e-15):
        return 0.0
    return max(-1.0, min(1.0, numerator / denominator))


def compute_score(norm_wire_length: float, max_overflow: float, correlation: float) -> dict:
    q_wl = min(1.0, 1.0 / max(norm_wire_length, 1.0))
    q_cong = max(0.0, 1.0 - max_overflow / OVERFLOW_LIMIT)
    q_thermal = (correlation + 1.0) / 2.0
    wl_score = 100.0 * q_wl
    congestion_score = 100.0 * q_cong
    thermal_score = 100.0 * q_thermal
    
    return {
        "wirelength_score": wl_score,
        "congestion_score": congestion_score,
        "thermal_score": thermal_score,
        "wirelength_quality": q_wl,
        "congestion_quality": q_cong,
        "thermal_quality": q_thermal,
        "weights": {"wirelength": 0.45, "congestion": 0.30, "thermal_tsv": 0.25},
    }


def write_matrix(path: Path, matrix: Sequence[Sequence[float]], fmt: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        for row in matrix:
            writer.writerow([format(float(value), fmt) for value in row])


def write_tsv_points(path: Path, points: Sequence[TSVPoint]) -> None:
    if not points:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(asdict(points[0])))
        writer.writeheader()
        for point in points:
            writer.writerow(asdict(point))


def write_overflow(path: Path, overflow) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["gcell_col", "gcell_row", "layer", "direction", "demand", "capacity", "overflow"])
        for (col, row, layer, direction), demand, capacity, amount in sorted(
            overflow, key=lambda item: (-item[3], common.parse_layer_num(item[0][2]), item[0][1], item[0][0])
        ):
            writer.writerow([col, row, layer, direction, demand, capacity, amount])


def _create_parser() ->  argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score M1-M20 GRT guides or a guide-derived mock-DRT result."
    )
    parser.add_argument("guide_file", type=Path)
    parser.add_argument("--lefs", action="append", type=Path, required=True)
    parser.add_argument("--input-def", type=Path, required=True)
    parser.add_argument("--thermal-json", type=Path, required=True)
    parser.add_argument("--tsv-cost-dbu", type=float, default=DEFAULT_TSV_COST_DBU)
    parser.add_argument("--via-cost-dbu", type=float, default=DEFAULT_VIA_COST_DBU)
    parser.add_argument("--output-dir", type=Path, required=True)

    return parser

def _evaluate(args, parser : argparse.ArgumentParser) -> str:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        def_definition = def_pybind.DefParser().Parse(args.input_def.as_posix())
        lef_definition = lef_pybind.LefParser().Parse([lef_path.as_posix() for lef_path in args.lefs])

        warnings: list[str] = []

        def_data = parse_def(args.input_def, "def")
        dbu = def_data.dbu_per_micron
        die = def_data.die
        tracks = def_data.tracks
        gcell_statements = def_data.gcell_statements

        grid, grid_warnings = build_gcell_grid(dbu, die, gcell_statements)
        warnings.extend(grid_warnings)
        nets = common.parse_guide(args.guide_file)

        net_to_mst_edges = check(nets, def_definition, lef_definition, tsv_cost = args.tsv_cost_dbu, via_cost=args.via_cost_dbu)        
        guide_layers = {rect[4] for rectangles in nets.values() for rect in rectangles}
        missing_tracks = sorted(guide_layers - set(tracks), key=common.parse_layer_num)
        if missing_tracks:
            warnings.append("There are layers in GUIDE that do not have defined tracks: " + ", ".join(missing_tracks))

        tsv_points = extract_tsv_points(nets, grid)
        if not tsv_points:
            raise ValueError("no TSV points found")

        route_fidelity = "guide-rectangle Manhattan MST proxy"
        norm_wire_length, total_wire_length, total_hpwl = compute_wire_length(nets_guide=nets, net_to_mst_edges = net_to_mst_edges, tsv_cost = args.tsv_cost_dbu, via_cost=args.via_cost_dbu)

        max_overflow, avg_overflow, total_overflow, overflow, demand, capacity = compute_congestion(
            nets, tracks, grid, layer_directions
        )

        thermal_path = args.thermal_json
        projected, thermal_payload = load_thermal_json(thermal_path)

        heat, node_counts, mapped_nodes = aggregate_temperature(projected, grid)
        ambient = float(thermal_payload.get("meta", {}).get("ambientTemp", 25.0))
        tsvs = tsv_matrix(tsv_points, grid)
        correlation = pearson_heat_tsv(heat, tsvs, ambient)
        score = compute_score(norm_wire_length, total_overflow, correlation)

        write_matrix(args.output_dir / "heat_gcell.csv", heat, ".8f")
        write_matrix(args.output_dir / "tsv_gcell.csv", tsvs, ".0f")
        write_matrix(args.output_dir / "thermal_node_count_per_gcell.csv", node_counts, ".0f")
        write_tsv_points(args.output_dir / "tsv_points.csv", tsv_points)
        write_overflow(args.output_dir / "overflow_gcells.csv", overflow)

        synthetic = bool(thermal_payload.get("meta", {}).get("synthetic", False))
        synthetic_warning = "thermal score uses a deterministic synthetic placement-density map"
        if synthetic and synthetic_warning not in warnings:
            warnings.append(synthetic_warning)

        report_data = {
            "status": "PASS",
            "fidelity": {
                "wirelength": route_fidelity,
                "congestion": "GRT guide demand / merged DEF track capacity",
                "drc": "not available; overflow entries are only a mock-DRT risk proxy",
                "thermal": "synthetic" if synthetic else "external",
            },
            "inputs": {
                "guide": str(args.guide_file),
                "lefs": [str(lef_file) for lef_file in args.lefs],
                "defs": [args.input_def.as_posix()],
                "thermal_json": str(thermal_path),
            },
            "grid": {
                "dbu_per_micron": dbu,
                "rows": grid.rows,
                "cols": grid.cols,
                "mapped_thermal_xy_nodes": mapped_nodes,
            },
            "wirelength": {
                "total_dbu": total_wire_length,
                "total_um": total_wire_length / dbu,
                "guide_mst_proxy_dbu": total_wire_length,
                "hpwl_reference_dbu": total_hpwl,
                "normalized": norm_wire_length,
                "tsv_count": len(tsv_points),
                "tsv_cost_dbu_each": args.tsv_cost_dbu,
            },
            "congestion": {
                "max_overflow": max_overflow,
                "average_overflow": avg_overflow,
                "total_overflow": total_overflow,
                "used_layer_gcells": len(demand),
                "overflow_entries": len(overflow),
                "mock_drt_risk_hotspots": len(overflow),
            },
            "thermal_tsv": {
                "synthetic": synthetic,
                "ambient_temperature": ambient,
                "tsv_points": len(tsv_points),
                "correlation_above_ambient": correlation,
            },
            "score": score,
            "warnings": warnings,
        }
        json_path = args.output_dir / "evaluation_report.json"
        text_to_output = json.dumps(report_data, indent=2)
        json_path.write_text(text_to_output + "\n", encoding="utf-8")

        lines = [
            "=" * 78,
            "Global Routing Evaluation Report",
            "=" * 78,
            f"Wirelength fidelity:           {route_fidelity}",
            f"Thermal source:                {'SYNTHETIC (not signoff)' if synthetic else 'external JSON'}",
            f"GCell grid:                    {grid.rows} rows x {grid.cols} cols",
            f"Guide nets / rectangles:       {len(nets)} / {sum(map(len, nets.values()))}",
            f"TSV points:                    {len(tsv_points)}",
            "",
            "--- Wirelength ---",
            f"Total:                         {total_wire_length / dbu:.4f} um",
            f"HPWL reference:                {total_hpwl / dbu:.4f} um",
            f"Normalized length:             {norm_wire_length:.8f}",
            "",
            "--- Guide Congestion / Mock-DRT Risk ---",
            f"Max overflow:                  {max_overflow}",
            f"Average overflow:              {avg_overflow:.8f}",
            f"Total overflow:                {total_overflow}",
            f"Overflow GCell-layer entries:  {len(overflow)}",
            "Real detailed-route DRC:       NOT AVAILABLE FROM GUIDE",
            "",
            "--- Thermal / TSV ---",
            f"Correlation above ambient:     {correlation:.10f}",
            "",
            "--- Score (original 45/30/25 weights) ---",
            f"Wirelength:                    {score['wirelength_score']:.4f}",
            f"Congestion:                    {score['congestion_score']:.4f}",
            f"Thermal/TSV:                   {score['thermal_score']:.4f}",
            "",
            "IMPORTANT: mock-drt is a guide/segment proxy, not a replacement for TritonRoute DRC.",
        ]
        if warnings:
            lines.extend(["", "--- Warnings ---", *[f"- {warning}" for warning in warnings]])
        text = "\n".join(lines) + "\n"
        (args.output_dir / "evaluation_report.txt").write_text(text, encoding="utf-8")
        print(text, end="")
        print(f"JSON report: {json_path}")
        print(f"Thermal JSON: {thermal_path}")

        return json_path
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
        return ""


def run(argvs : list) -> None:
    parser = _create_parser()
    args = parser.parse_args(argvs)
    return _evaluate(args, parser)

def main() -> None:
    parser = _create_parser()
    args = parser.parse_args()
    _evaluate(args, parser)

if __name__ == "__main__":
    main()
