"""Read-only evidence collection; never launches Router or an evaluator."""
import argparse
import collections
import json
import math
import re
import dis
import marshal
import struct
import sys
import types
import zlib
from pathlib import Path
from common import PROJECT, EDA, OUTPUTS, load_run, sha256
from evaluator_diagnostics import decode_mixed


def read(path):
    try:
        return json.loads(Path(path).read_bytes())
    except (OSError, ValueError):
        return {}


def inspect_elf(path):
    """Inspect supplied Python bytecode without executing or patching it.

    Marshal code objects require the bundled interpreter version. This ELF
    declares Python 3.10; run this mode with Ubuntu's python3.10.
    """
    blob=Path(path).read_bytes();end=blob.rfind(b'MEI\014\013\012\013\016')
    if end<0:raise ValueError('No supported PyInstaller cookie')
    _,size,toc,length,version,library=struct.unpack('!8sIIII64s',blob[end:end+88])
    if version!=sys.version_info.major*100+sys.version_info.minor:raise ValueError('Use Python '+str(version)+' to inspect matching bytecode')
    base=end+88-size;position=base+toc;entries={}
    while position<base+toc+length:
        n,offset,packed,raw,compressed,kind=struct.unpack('!iIIIBc',blob[position:position+18])
        name=blob[position+18:position+n].rstrip(b'\0').decode();position+=n
        if name in ('score','PYZ-00.pyz'):
            data=blob[base+offset:base+offset+packed];entries[name]=zlib.decompress(data) if compressed else data
    functions={}
    def collect(module,code,names):
        for item in code.co_consts:
            if isinstance(item,types.CodeType) and item.co_name in names:
                selected=[];line=item.co_firstlineno
                for op in dis.get_instructions(item):
                    line=op.starts_line or line
                    if item.co_name=='compute_congestion' and not (351<=line<=355 or 369<=line<=390 or 414<=line<=420):continue
                    selected.append([line,op.opname,op.argrepr])
                functions[module+'.'+item.co_name]={'line':item.co_firstlineno,'instructions':selected}
    collect('score',marshal.loads(entries['score']),{'build_capacity','compute_congestion'})
    pyz=entries['PYZ-00.pyz'];index=dict(marshal.loads(pyz[struct.unpack('!i',pyz[8:12])[0]:]));_,offset,length=index['common']
    collect('common',marshal.loads(zlib.decompress(pyz[offset:offset+length])),{'rectangle_cell_indices','locate_interval'})
    return {'elf_sha256':sha256(path),'python':version,'library':library.rstrip(b'\0').decode(),'inspection_only':True,'official_binary_executed':False,'functions':functions}


def guide_profile(path, def_path):
    text = Path(def_path).read_text(encoding='utf8')
    area = list(map(int, re.search(r'DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)', text).groups()))
    section = re.search(r'^NETS\s+\d+\s*;(.*?)^END NETS', text, re.M | re.S).group(1)
    expected = set(re.findall(r'^\s*-\s+(\S+)', section, re.M))
    del text, section
    seen = set(); counts = []; layers = collections.Counter(); errors = []
    count = duplicates = outside = very_wide = rectangles = 0
    largest = []; name = None; opened = False; current = set()
    def error(message):
        if len(errors) < 20: errors.append(message)
    with Path(path).open(encoding='utf8') as stream:
        for line_number, line in enumerate(stream, 1):
            line = line.strip()
            if not line: continue
            if name is None:
                if line in seen: error('duplicate Net: ' + line)
                if line not in expected: error('unknown Net: ' + line)
                name = line; seen.add(name); count = 0; current = set()
            elif not opened:
                if line != '(': error('expected opening parenthesis at ' + str(line_number))
                opened = True
            elif line == ')':
                if not count: error('empty Net: ' + name)
                counts.append(count); largest.append((count, name))
                largest = sorted(largest, reverse=True)[:10]
                name = None; opened = False
            else:
                fields = line.split()
                if len(fields) != 5 or not re.fullmatch(r'metal(?:[1-9]|1[0-9]|20)', fields[-1]):
                    error('invalid rectangle/layer at ' + str(line_number)); continue
                try: x1, y1, x2, y2 = map(int, fields[:4])
                except ValueError: error('noninteger coordinate at ' + str(line_number)); continue
                if x1 >= x2 or y1 >= y2: error('nonpositive rectangle at ' + str(line_number))
                if not (area[0] <= x1 < x2 <= area[2] and area[1] <= y1 < y2 <= area[3]): outside += 1
                rect = (x1, y1, x2, y2, fields[-1]); duplicates += rect in current; current.add(rect)
                very_wide += (x2-x1)*(y2-y1) > .01*(area[2]-area[0])*(area[3]-area[1])
                count += 1; rectangles += 1; layers[fields[-1]] += 1
    if name is not None: error('unterminated Net')
    missing = expected - seen
    if missing: error('missing DEF Nets: ' + str(len(missing)))
    ordered = sorted(counts)
    return dict(bytes=Path(path).stat().st_size, sha256=sha256(path), nets=len(seen), def_nets=len(expected),
                rectangles=rectangles, largest_nets=largest, duplicates=duplicates, out_of_die_rectangles=outside,
                rectangles_over_one_percent_die_area=very_wide, diearea=area, layers=dict(layers), errors=errors,
                rectangle_percentiles={str(p): ordered[min(len(ordered)-1, math.floor(p/100*len(ordered)))] for p in (50,90,99,100)},
                basic_syntax_pass=not errors and not outside,
                scope='Syntax/net identity/finite integer geometry only; not full Pin connectivity or official legality')


def history(directory):
    run = load_run(directory); rows = []
    for invocation in sorted(Path(directory).rglob('invocation.json')):
        d = invocation.parent; record = read(invocation); process = read(d/'evaluator_process.json'); exit_record = read(d/'evaluator_exit.json')
        report = read(d/'evaluation_report.json'); host = []
        if (d/'host_monitor.jsonl').exists():
            for line in (d/'host_monitor.jsonl').read_text(encoding='utf8').splitlines():
                try: host.append(json.loads(line))
                except ValueError: pass
        tail = decode_mixed((d/'evaluator.log').read_bytes()[-8192:]) if (d/'evaluator.log').exists() else ''
        stages = re.findall(r'\[INFO\]\s*(.+)', tail)
        rows.append(dict(directory=str(d.relative_to(PROJECT)), invocation_status=record.get('status'),
                         classification=record.get('failure_category','UNKNOWN'), authoritative_exit=exit_record,
                         process=process, report_status=report.get('status'), last_stage=stages[-1] if stages else None,
                         last_host=host[-1] if host else {}, invocation_finalized='finished_at' in record,
                         evaluator_sha256=record.get('evaluator_sha256'), timeout_seconds=record.get('timeout_seconds'),
                         stored_evidence=record.get('evidence',[]), wsl_after=record.get('wsl_after'),
                         windows_events=record.get('windows_events'), log_tail=tail,
                         resource_usage_bytes=(d/'resource_usage.txt').stat().st_size if (d/'resource_usage.txt').exists() else None))
    return dict(directory=str(Path(directory).relative_to(PROJECT)), router_status=run.get('status'),
                router_returncode=run.get('returncode'), router_sha256=run.get('router_sha256'),
                guide=guide_profile(Path(directory)/'route.guide',run['inputs']['def']), evaluations=rows)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--rules-only',action='store_true');a=p.parse_args()
    if a.output.exists():p.error('Refusing to overwrite evidence')
    if a.rules_only:
        data=inspect_elf(EDA/'\u8bc4\u4f30\u7a0b\u5e8f/score')
        with a.output.open('x',encoding='utf8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
        print('Read-only bytecode evidence saved; no evaluator invocation');return
    rows=[]
    for case in ('001','003','005'):
        for d in sorted((OUTPUTS/'runs'/('open_3d_case'+case)).iterdir()):
            if (d/'run.json').exists() and (d/'route.guide').exists() and (case!='001' or d.name=='case001_v3_2'):
                rows.append(history(d))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf8') as f:json.dump(rows,f,ensure_ascii=False,indent=2)
    for row in rows:print(row['directory'],row['guide']['nets'],row['guide']['rectangles'],[(x['last_stage'],x['classification']) for x in row['evaluations']])


if __name__=='__main__':main()
