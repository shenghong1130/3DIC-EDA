#!/usr/bin/env python3
"""
3D IC 全局布线器 - 简单实现
功能：读取输入文件，生成合法的布线结果

注意：这是一个最简单的示例实现，仅用于演示提交格式。
实际参赛需要实现完整的布线算法。
"""

import sys
import argparse
import re
from pathlib import Path


class SimpleRouter:
    """简单的全局布线器"""

    def __init__(self):
        self.nets = []
        self.die_area = None
        self.layers = []

    def parse_def(self, def_file):
        """解析 DEF 文件，提取网络和芯片区域信息"""
        print(f"正在解析 DEF 文件: {def_file}")

        with open(def_file, 'r') as f:
            content = f.read()

        # 提取 DIEAREA
        die_match = re.search(r'DIEAREA\s+\(\s*(\d+)\s+(\d+)\s*\)\s+\(\s*(\d+)\s+(\d+)\s*\)', content)
        if die_match:
            x1, y1, x2, y2 = map(int, die_match.groups())
            self.die_area = (x1, y1, x2, y2)
            print(f"  Die Area: ({x1}, {y1}) - ({x2}, {y2})")

        # 提取网络
        nets_section = re.search(r'NETS\s+(\d+)\s*;(.*?)END NETS', content, re.DOTALL)
        if nets_section:
            nets_count = int(nets_section.group(1))
            nets_content = nets_section.group(2)

            # 简单提取网络名
            for line in nets_content.split('\n'):
                line = line.strip()
                if line.startswith('- '):
                    parts = line.split()
                    if len(parts) > 1:
                        net_name = parts[1]
                        self.nets.append(net_name)

            print(f"  找到 {len(self.nets)} 个网络")

    def parse_lef(self, lef_file):
        """解析 LEF 文件，提取布线层信息"""
        print(f"正在解析 LEF 文件: {lef_file}")

        with open(lef_file, 'r') as f:
            content = f.read()

        # 提取布线层
        layer_pattern = re.compile(r'LAYER\s+(\w+)\s+TYPE\s+ROUTING', re.IGNORECASE)
        self.layers = layer_pattern.findall(content)

        print(f"  找到 {len(self.layers)} 个布线层: {', '.join(self.layers[:5])}...")

    def generate_simple_routing(self):
        """
        生成简单的布线结果

        策略：为每个网络在芯片中心生成一个小矩形
        注意：这只是示例，实际需要实现真正的布线算法
        """
        print("正在生成布线结果...")

        if not self.die_area or not self.nets or not self.layers:
            raise ValueError("缺少必要的输入信息")

        x1, y1, x2, y2 = self.die_area
        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2

        routing_result = []

        # 为每个网络生成一个简单的布线矩形
        for i, net in enumerate(self.nets):
            # 在芯片中心区域生成小矩形
            offset = (i % 10) * 5000  # 简单的偏移避免重叠

            # 使用 metal1 层
            layer = 'metal1' if 'metal1' in self.layers else self.layers[0]

            rect_x1 = center_x + offset - 2500
            rect_y1 = center_y + offset - 2500
            rect_x2 = center_x + offset + 2500
            rect_y2 = center_y + offset + 2500

            # 确保在芯片范围内
            rect_x1 = max(x1, min(x2, rect_x1))
            rect_y1 = max(y1, min(y2, rect_y1))
            rect_x2 = max(x1, min(x2, rect_x2))
            rect_y2 = max(y1, min(y2, rect_y2))

            routing_result.append({
                'net': net,
                'rectangles': [
                    (rect_x1, rect_y1, rect_x2, rect_y2, layer)
                ]
            })

        print(f"  生成了 {len(routing_result)} 个网络的布线结果")
        return routing_result

    def write_guide(self, output_file, routing_result):
        """写入 guide 文件"""
        print(f"正在写入 guide 文件: {output_file}")

        with open(output_file, 'w') as f:
            for net_routing in routing_result:
                f.write(f"{net_routing['net']}\n")
                f.write("(\n")
                for rect in net_routing['rectangles']:
                    x1, y1, x2, y2, layer = rect
                    f.write(f"{x1} {y1} {x2} {y2} {layer}\n")
                f.write(")\n")

        print(f"  完成！共写入 {len(routing_result)} 个网络")

    def run(self, def_file, lef_file, output_file):
        """执行完整的布线流程"""
        print("=" * 70)
        print("3D IC 全局布线器 - 简单实现")
        print("=" * 70)

        # 1. 解析输入
        self.parse_def(def_file)
        self.parse_lef(lef_file)

        # 2. 生成布线
        routing_result = self.generate_simple_routing()

        # 3. 输出结果
        self.write_guide(output_file, routing_result)

        print("=" * 70)
        print("布线完成！")
        print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description='3D IC 全局布线器 - 简单实现',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  python router.py --def input.def --lef input.lef --thermal-report thermal.json --output output.guide

注意:
  这是一个简化的示例实现，仅用于演示提交格式。
  实际参赛需要实现完整的布线算法，包括：
  - 多目标优化（线长、拥塞、热优化）
  - 合法性检查
  - 性能优化
        """
    )

    parser.add_argument('--def', dest='def_file', required=True,
                        help='输入的 DEF 文件路径')
    parser.add_argument('--lef', dest='lef_file', required=True,
                        help='输入的 LEF 文件路径')
    parser.add_argument('--thermal-report', dest='thermal_file', required=True,
                        help='输入的热分布 JSON 文件路径')
    parser.add_argument('--output', dest='output_file', required=True,
                        help='输出的 guide 文件路径')

    args = parser.parse_args()

    # 验证输入文件
    if not Path(args.def_file).exists():
        print(f"错误: DEF 文件不存在: {args.def_file}")
        return 1

    if not Path(args.lef_file).exists():
        print(f"错误: LEF 文件不存在: {args.lef_file}")
        return 1

    if not Path(args.thermal_file).exists():
        print(f"错误: 热分布文件不存在: {args.thermal_file}")
        return 1

    try:
        # 创建布线器并运行
        router = SimpleRouter()
        router.run(args.def_file, args.lef_file, args.output_file)
        return 0

    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
