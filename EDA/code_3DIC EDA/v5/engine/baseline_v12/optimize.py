#!/usr/bin/env python3
"""Run the v0.5 input-only adaptive and TSV-relocation pipeline."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--def',dest='def_file',type=Path,required=True)
    p.add_argument('--lef',dest='lef_file',type=Path,required=True)
    p.add_argument('--thermal-report',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--thermal-gain',type=float,default=12.0,
                   help='One global thermal heuristic for all input names')
    a=p.parse_args()
    a.output=a.output.resolve()
    if a.output.exists():
        p.error('Output already exists')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    here=Path(__file__).resolve().parent
    if a.thermal_gain<=0:p.error('Thermal gain must be positive')
    gain=a.thermal_gain
    stage=Path(tempfile.mkdtemp(prefix='.v05-stage-',dir=a.output.parent)).resolve()
    start=time.perf_counter()
    inputs=['--def',str(a.def_file.resolve()),'--lef',str(a.lef_file.resolve()),'--thermal-report',str(a.thermal_report.resolve())]
    seed=stage/'adaptive.guide'
    local=stage/'local.guide'
    global_route=stage/'global.guide'
    rerouted=stage/'rerouted.guide'
    slot1=stage/'slot1.guide'
    final=stage/'final.guide'
    commands=[[sys.executable,str(here/'adaptive_router.py'),*inputs,'--output',str(seed)],
              [sys.executable,str(here/'relocate.py'),*inputs,'--seed-guide',str(seed),'--output',str(local),
               '--thermal-gain',str(gain)],
              [sys.executable,str(here/'relocate.py'),*inputs,'--seed-guide',str(local),'--output',str(global_route),
               '--thermal-gain',str(gain),'--global-thermal'],
              [sys.executable,str(here/'negotiated.py'),'--def',str(a.def_file.resolve()),'--lef',str(a.lef_file.resolve()),
               '--seed-guide',str(global_route),'--output',str(rerouted)],
              [sys.executable,str(here/'relocate.py'),*inputs,'--seed-guide',str(rerouted),'--output',str(slot1),
               '--thermal-gain',str(gain),'--global-thermal','--full-tsv-price','--max-reroutes','12000'],
              [sys.executable,str(here/'relocate.py'),*inputs,'--seed-guide',str(slot1),'--output',str(final),
               '--thermal-gain',str(gain),'--global-thermal','--full-tsv-price','--max-reroutes','12000']]
    for command in commands:
        subprocess.run(command,check=True)
    # Keep the detailed stage reports after removing only our own staging tree.
    report={'version':'0.5','seconds':time.perf_counter()-start,
        'adaptive':json.loads(seed.with_suffix('.run.json').read_text()),
        'local_relocation':json.loads(local.with_suffix('.run.json').read_text()),
        'global_relocation':json.loads(global_route.with_suffix('.run.json').read_text()),
        'negotiated':json.loads(rerouted.with_suffix('.run.json').read_text()),
        'slot_refinement_first':json.loads(slot1.with_suffix('.run.json').read_text()),
        'slot_refinement':json.loads(final.with_suffix('.run.json').read_text()),
        'pin_mode':'physical','official_evaluation':'NOT_RUN'}
    report.update(nets=report['adaptive']['nets'],rectangles=report['slot_refinement']['rectangles'],cross_die_nets=report['adaptive']['cross_die_nets'])
    final.replace(a.output)
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    if stage.parent!=a.output.parent or not stage.name.startswith('.v05-stage-'):
        raise RuntimeError('Unexpected staging path')
    shutil.rmtree(stage)
    print(json.dumps({'version':'0.5','seconds':report['seconds'],'output':str(a.output)}),flush=True)


if __name__=='__main__':main()
