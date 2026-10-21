from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import re

import logging

logger = logging.getLogger(__name__)

def parse_layer_num(layer_name: str) -> int:
    match = re.fullmatch(r"(?:Metal|M)(\d+)", layer_name, flags=re.IGNORECASE)
    return int(match.group(1)) if match else 0

MAX_LAYER_INDEX = 20
MIN_LAYER_INDEX = 1
TSV_LOWER_LAYER = "metal10"
TSV_UPPER_LAYER = "metal11"
TSV_LOWER_LAYER_INDEX = parse_layer_num(TSV_LOWER_LAYER)
TSV_UPPER_LAYER_INDEX = parse_layer_num(TSV_UPPER_LAYER)

@dataclass(frozen=True)
class DieArea:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

GuideRect = tuple[int, int, int, int, str]

def rect_center(rect: GuideRect) -> tuple[float, float]:
    return ((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0)

def die_of_layer(layer_name: str) -> str:
    number = parse_layer_num(layer_name)
    if number == 0:
        return "unknown"
    return "bottom" if number <= TSV_LOWER_LAYER_INDEX else "top"

def is_cross_die(layer1: str, layer2: str) -> bool:
    return die_of_layer(layer1) != die_of_layer(layer2)

def edge_cost(net_name : str, a: GuideRect, b: GuideRect, tsv_cost: float, via_cost: float) -> float:
    ax, ay = rect_center(a)
    bx, by = rect_center(b)
    cost = abs(ax - bx) + abs(ay - by)
    if a[4] != b[4]:
        if is_cross_die(a[4], b[4]):
            cost += tsv_cost
        else:
            cost += via_cost * abs(parse_layer_num(a[4]) - parse_layer_num(b[4]))
    return cost

def parse_guide(path: Path) -> dict[str, list[GuideRect]]:
    nets: dict[str, list[GuideRect]] = defaultdict(list)
    current: str | None = None
    for number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line == "(":
            continue
        if line == ")":
            current = None
            continue
        fields = line.split()
        if len(fields) == 5 and parse_layer_num(fields[4]):
            if current is None:
                raise ValueError(f"{path}:{number}: guide rectangle before net name")
            x1, y1, x2, y2 = map(int, fields[:4])
            nets[current].append(
                (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), fields[4])
            )
        else:
            current = line
    if not nets:
        raise ValueError(f"{path}: no guide nets")
    
    new_nets: dict[str, list[GuideRect]] = defaultdict(list)
    for name, rectangles in nets.items():
        new_nets[name] = list(dict.fromkeys(rectangles))

    return dict(new_nets)



