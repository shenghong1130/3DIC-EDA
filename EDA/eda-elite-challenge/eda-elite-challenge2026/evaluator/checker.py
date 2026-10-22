import common
import tse_framework_pybind_def as def_pybind
import tse_framework_pybind_lef as lef_pybind
from pathlib import Path
import logging
import heapq

logger = logging.getLogger(__name__)
layer_directions = dict()

def _parse_lef_routing_layers(lef_definition : lef_pybind.LefDefinition) -> set[str]:
    layers: set[str] = set()
    for lef_layer in lef_definition.GetLayers():
        if lef_layer.type == "ROUTING":
            layers.add(lef_layer.name)
            layer_directions[lef_layer.name] = lef_layer.layer_routing.direction

    return layers

def _rect_contains_point(rect : common.GuideRect, point) -> bool:
        x, y, layer, _ = point
        return (rect[0] <= x <= rect[2] or rect[2] <= x <= rect[0]) and \
               (rect[1] <= y <= rect[3] or rect[3] <= y <= rect[1]) and rect[4] == layer

def _rectangles_connect(rect1 : common.GuideRect, rect2 : common.GuideRect, tolerance=1e-6):
    """
    判断两个矩形是否相交（包括边重合、部分重叠、完全包含）。
    只要它们在 X 和 Y 方向上的投影有重叠（考虑容差），就判定为连接。
    """
    layer1 = rect1[4]
    layer2 = rect2[4]
    die1 = common.die_of_layer(layer1)
    die2 = common.die_of_layer(layer2)
    if die1 != die2 and not ((layer1 == common.TSV_LOWER_LAYER and layer2 == common.TSV_UPPER_LAYER) \
                             or (layer1 == common.TSV_UPPER_LAYER and layer2 == common.TSV_LOWER_LAYER)):
        return False
    
    layer_index1 = common.parse_layer_num(layer1)
    layer_index2 = common.parse_layer_num(layer2)
    if layer_index1 < common.MIN_LAYER_INDEX or layer_index1 > common.MAX_LAYER_INDEX or \
        layer_index2 < common.MIN_LAYER_INDEX or layer_index2 > common.MAX_LAYER_INDEX:
        logger.error(f"Illegal layer in guide: {layer1}/{layer_index1} or {layer2}/{layer_index2}")
        return False

    if abs(layer_index2 - layer_index1) > 1:
        return False

    # 提取矩形坐标，确保 x1 <= x2, y1 <= y2
    x1_1, y1_1, x2_1, y2_1 = rect1[0], rect1[1], rect1[2], rect1[3]
    x1_2, y1_2, x2_2, y2_2 = rect2[0], rect2[1], rect2[2], rect2[3]

    # 归一化：确保 x1 <= x2, y1 <= y2
    if x1_1 > x2_1: x1_1, x2_1 = x2_1, x1_1
    if y1_1 > y2_1: y1_1, y2_1 = y2_1, y1_1
    if x1_2 > x2_2: x1_2, x2_2 = x2_2, x1_2
    if y1_2 > y2_2: y1_2, y2_2 = y2_2, y1_2

    # 检查 X 方向是否有重叠（考虑容差）
    x_overlap = (x1_1 <= x2_2 + tolerance) and (x1_2 <= x2_1 + tolerance)
    # 检查 Y 方向是否有重叠（考虑容差）
    y_overlap = (y1_1 <= y2_2 + tolerance) and (y1_2 <= y2_1 + tolerance)

    return x_overlap and y_overlap


def build_adjacency_list(net_name : str, rectangles : list[common.GuideRect], tsv_cost: float, via_cost: float) -> dict[int, set[tuple[int, float]]]:
    adjacency = {i: set[tuple[int, float]]() for i in range(len(rectangles))}
    for i in range(len(rectangles)):
        for j in range(len(rectangles)):
            if i == j:
                continue

            if _rectangles_connect(rectangles[i], rectangles[j]):
                cost = common.edge_cost(net_name, rectangles[i], rectangles[j], tsv_cost, via_cost)
                adjacency[i].add((j, cost))
                adjacency[j].add((i, cost))

    return adjacency

def get_position(connection : def_pybind.NetConnection, lef_definition : lef_pybind.LefDefinition, def_definition, dbu):
    if connection.instance == "PIN":
        pin = def_pybind.Pin()
        exists = def_definition.GetIoPin(connection.pin, pin)
        if not exists:
            raise ValueError(f"No pin definition: {connection.pin}")
        pin_port = pin.pin_ports[0]
        return [pin_port.x, pin_port.y, pin_port.layers[0].layer, f"{connection.instance}.{connection.pin}"]
    else:
        component = next((component for component in def_definition.getComponents() if component.instance_name == connection.instance), None)
        if component is None:
            raise ValueError(f"Not found component for instance: {connection.instance}")
    
        macro = lef_definition.GetMacroByName(component.macro_name)
        pin = macro.pins[connection.pin]
        port = pin.ports[0].geometries[0].rect[0]
        x = (port.xl + port.xh) / 2
        y = (port.yl + port.yh) / 2
    
        return [component.x + x * dbu, component.y + y * dbu, pin.ports[0].geometries[0].layer, f"{connection.instance}.{connection.pin}"]


def build_mst(guide_nets : dict[str, list[common.GuideRect]], tsv_cost: float, via_cost: float) -> dict[str, tuple[float, list[tuple[int, int, float]]]]:
    net_to_mst_edges = {}
    adjacencys : dict = {}
    for net_name, rectangles in guide_nets.items():
        net_adjacency = build_adjacency_list(net_name, rectangles, tsv_cost, via_cost)
        adjacencys[net_name] = net_adjacency
        n = len(rectangles)

        visited = [False] * n
        min_heap = [(0.0, 0, -1)]  # (权重, 当前顶点, 父顶点)
        total_weight = 0.0
        net_mst_edges : list[tuple[int, int, float]] = []

        while min_heap:
            weight, u, parent = heapq.heappop(min_heap)

            if visited[u]:
                continue

            visited[u] = True
            total_weight += weight
            if parent != -1:
                net_mst_edges.append((parent, u, weight))

            for v, w in net_adjacency[u]:
                if not visited[v]:
                    heapq.heappush(min_heap, (w, v, u))

        if len(net_mst_edges) != (n - 1):
            raise ValueError(f"net: {net_name} not connected!")
        
        net_to_mst_edges[net_name] = (total_weight, net_mst_edges)

    return net_to_mst_edges


def check_net_connection(guide_nets : dict[str, list[common.GuideRect]], def_definition : def_pybind.DefDefinition, 
                         lef_definition : lef_pybind.LefDefinition, tsv_cost : float, via_cost : float):
    def_nets = def_definition.getNets()
    def_net_names = [net.name for net in def_nets]
    for net_name, _ in guide_nets.items():
        if not net_name in def_net_names:
            raise ValueError(f"There is a net in GUIDE that are not defined in DEF: {net_name}")

    net_name_not_routed = []
    dbu = def_definition.GetDatabaseUnits()
    for net in def_nets:
        if net.name not in guide_nets.keys():
            net_name_not_routed.append(net.name)

        net_rectangles = guide_nets[net.name]
        net_rectangles = list(dict.fromkeys(net_rectangles))
        if not net_rectangles:
            raise ValueError("There is a net not routed in GUIDE: {net_name}")

        for i in range(len(net.connections)):
            position = get_position(net.connections[i], lef_definition, def_definition, dbu)
            indices = [i for i, rect in enumerate(net_rectangles) if _rect_contains_point(rect, position)]
            if not indices:
                raise ValueError(f"Pin is not contained by any rectangle.: {net_name}.{position[3]}")

    if len(net_name_not_routed) > 0:
        raise ValueError("nets not routed: " + ", ".join(net_name_not_routed))
    
    return build_mst(guide_nets, tsv_cost, via_cost) #building MST is equivalent to checking connectivity.


def check_crossing_border(guide_nets : dict[str, list[common.GuideRect]], def_definition : def_pybind.DefDefinition):
    cross_nets = 0
    cross_net_name = []
    die_area = def_definition.getDieArea()
    for net_name, rectangles in guide_nets.items():
        for rect in rectangles:
            if rect[0] < die_area.xl or rect[0] > die_area.xh or rect[1] < die_area.yl or rect[1] > die_area.yh \
               or rect[2] < die_area.xl or rect[2] > die_area.xh or rect[3] < die_area.yl or rect[3] > die_area.yh:
               cross_nets += 1
               cross_net_name.append(net_name)

    if cross_nets > 0:
        raise ValueError(f"crossing border nets: " + ", ".join(cross_net_name))

def check(guide_nets : dict[str, list[common.GuideRect]], def_definition : def_pybind.DefDefinition, 
        lef_definition : lef_pybind.LefDefinition, tsv_cost : float, via_cost : float) -> dict[str, tuple[float, list[tuple[int, int, float]]]]:
    lef_layers = _parse_lef_routing_layers(lef_definition)
    guide_layers = {rect[4] for rectangles in guide_nets.values() for rect in rectangles}
    missing_lef = sorted(guide_layers - lef_layers, key=common.parse_layer_num)
    if missing_lef:
        raise ValueError("There are layers in GUIDE that are not defined in the LEF file: " + ", ".join(missing_lef))
    
    check_crossing_border(guide_nets, def_definition)
    return check_net_connection(guide_nets, def_definition, lef_definition, tsv_cost, via_cost)

if __name__ == "__main__":
    try:
        def_definition = def_pybind.DefParser().Parse("cases/sample001/sample001.input.def")
        lef_definition = lef_pybind.LefParser().Parse(["cases/sample001/sample001.input.lef"])
        guide_nets = common.parse_guide(Path("cases/sample001/sample001.output.guide"))
        check(guide_nets, def_definition, lef_definition)
    except (OSError, ValueError) as error:
        print(str(error))
    