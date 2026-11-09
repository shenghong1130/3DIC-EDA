"""Reproducible observer-only copy. Never writes into v5/engine."""
import ast
import hashlib
import json
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def instrument(source):
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    insertions = {}
    def add(line, text):
        insertions.setdefault(line, []).append(text)
    # Decorators return the original callable when disabled. Wrappers execute
    # the original function exactly once and never replace its result.
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='replace' and len(node.args)==1 and ast.unparse(node.args[0]) in ('a.output','args.output'):
            line=lines[node.lineno-1];indent=line[:len(line)-len(line.lstrip())]
            add(node.end_lineno+1,indent+'_trace.alias_guide('+ast.unparse(node.func.value)+', '+ast.unparse(node.args[0])+')\n')
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        indent = ' ' * node.col_offset
        if node.name == '_build' or node.name == '_route_net_fixed' and 'class Router:' in source:
            add(node.lineno, indent + '@_trace.observe_build\n')
        elif node.name in ('route_net', 'reroute'):
            add(node.lineno, indent + '@_trace.observe_choice\n')
        elif node.name == 'rect':
            add(node.lineno, indent + '@_trace.observe_rect\n')
        elif node.name == 'stack':
            add(node.lineno, indent + '@_trace.observe_stack\n')
        elif node.name == 'validate':
            add(node.lineno, indent + '@_trace.observe_validation\n')
        elif node.name in ('tree_edges', 'steiner_tree'):
            add(node.lineno, indent + '@_trace.observe_topology\n')
        elif node.name == 'compact_net':
            add(node.lineno, indent + '@_trace.observe_compaction\n')
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        indent = line[:len(line)-len(line.lstrip())]
        if stripped == 'for col,row,layer in path:':
            add(number, indent + '_trace.astar_path(self, path)\n')
        if stripped == 'expanded+=1':
            add(number, indent + '_trace.search_event(self, "search_expanded", state)\n')
        if stripped == 'tree.update(path);todo.difference_update(tree)':
            add(number, indent + '_trace.compact_path(r, path, cur)\n')
        if stripped == 'return result,len(used)-len(alive)':
            add(number, indent + '_trace.compact_result(d, r, name, result, alive, neighbors)\n')
        if stripped == 'distances[nxt]=value;parents[nxt]=state':
            add(number+1, indent + '_trace.search_event(self, "search_relaxed", nxt, state)\n')
        if stripped == 'candidate=set()' and 'class AdaptiveRouter' in source:
            add(number+1, indent + '_trace.option_begin(self)\n')
        if stripped == 'candidate=merge_corridors(candidate,self.d.layers)' and 'class AdaptiveRouter' in source:
            add(number+1, indent + '_trace.option_end(self, candidate)\n')
        if stripped == 'rects.update(best[1])' and 'class AdaptiveRouter' in source:
            add(number+1, indent + '_trace.option_selected(self, best[1])\n')
        if stripped == "reports.append(record);print('STAGE',kind,'accepted',record['accepted'],flush=True)":
            add(number+1, indent + '_trace.stage_decision(record, current, output)\n')
        if 'temporary.write_bytes(current.read_bytes())' in stripped:
            add(number+1, indent + '_trace.alias_guide(current, a.output)\n')
    if not insertions:
        return source
    # Place after module docstring, preserving future-import position.
    pos = tree.body[0].end_lineno if isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str) else 0
    while any(isinstance(n, ast.ImportFrom) and n.module == '__future__' and n.lineno == pos+1 for n in tree.body):
        pos = next(n.end_lineno for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == '__future__' and n.lineno == pos+1)
    add(pos+1, 'import route_trace as _trace\n')
    return ''.join(''.join(insertions.get(i, []))+line for i,line in enumerate(lines,1))+''.join(insertions.get(len(lines)+1,[]))


def main():
    original = PROJECT/'v5/engine'
    target = PROJECT/'v5_trace/engine'
    refresh = '--refresh' in sys.argv
    if target.exists() and not refresh:
        raise SystemExit('Trace engine already exists; refusing to overwrite')
    files = {p.relative_to(original).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in original.rglob('*') if p.is_file()}
    assert len(files) == 79, len(files)
    target.parent.mkdir(parents=True, exist_ok=True)
    (target.parent/'original_sha256.json').write_text(json.dumps({'file_count':len(files),'files':files},indent=2),encoding='utf8')
    if not target.exists():shutil.copytree(original, target)
    changes = []
    for relative in files:
        if not relative.endswith('.py'):
            continue
        path = target/relative
        before = (original/relative).read_text(encoding='utf8')
        after = instrument(before)
        if before != after:
            path.write_text(after,encoding='utf8',newline='\n')
            changes.append(relative)
    observer = PROJECT/'v5_trace/route_trace.py'
    for folder in (target, target/'baseline_v12'):
        shutil.copyfile(observer,folder/'route_trace.py')
    (target.parent/'instrumentation.json').write_text(json.dumps({'modified_copies':changes,'method':'observer-only decorators and calls; original AST retained'},indent=2),encoding='utf8')
    print(json.dumps({'original_files':len(files),'instrumented_copies':len(changes)}))


if __name__ == '__main__':
    main()
