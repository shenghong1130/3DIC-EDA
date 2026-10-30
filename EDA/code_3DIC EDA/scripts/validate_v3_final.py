"""Compile/test a temporary Final, compare it with V2, and optionally publish.

One fixed config. No Router runs for case003/005. No version registration or Git
writes. Temporary binaries are automatically removed on success and failure.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile
from common import PROJECT, OUTPUTS, backend_command, linux_path, sha256, safe_label, resolve_config

FLAGS=['-std=c++17','-O3','-DNDEBUG','-Wall','-Wextra','-I','include']
SOURCES=['src/main.cpp','src/parser.cpp','src/router.cpp']


def save_new(path,value):
    with path.open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')


def quality_gate(rows):
    reasons=[];strict=False
    for case in ['toy','sample001','open_3d_case001']:
        pair={r['variant']:r for r in rows if r['case']==case}
        if not {'v2','v3'}<=set(pair):reasons.append(case+': missing pair');continue
        a,b=pair['v2'],pair['v3']
        if any(r['status']!='ROUTED' or r['official_status']!='PASS' for r in [a,b]):reasons.append(case+': routing/evaluation incomplete');continue
        if b['wirelength_dbu']>a['wirelength_dbu']+1e-6:reasons.append(case+': official wirelength regression')
        if b['wirelength_score']<a['wirelength_score']-1e-9:reasons.append(case+': wirelength score regression')
        if b['official_overflow']>a['official_overflow']:reasons.append(case+': official overflow regression')
        if b['congestion_score']<a['congestion_score']-1e-9:reasons.append(case+': congestion score regression')
        if b['thermal_score']<a['thermal_score']-1e-9:reasons.append(case+': thermal score regression')
        old=pair.get('old_final')
        if old:
            if old['status']!='ROUTED' or old['official_status']!='PASS':reasons.append(case+': old Final control incomplete')
            else:
                for key,direction in [('wirelength_dbu',1),('official_overflow',1),('wirelength_score',-1),('congestion_score',-1),('thermal_score',-1)]:
                    if direction*(b[key]-old[key])>1e-9:reasons.append(case+': regression against old Final: '+key)
        if case=='open_3d_case001':strict=b['wirelength_dbu']<a['wirelength_dbu']-1e-6 or b['official_overflow']<a['official_overflow']
    if not strict:reasons.append('No strict case001 wirelength/overflow improvement')
    return not reasons,reasons


def main():
    # The old Final publication experiment is historical, not a normal build
    # or regression prerequisite. Reading it must never reconstruct old runs.
    p=argparse.ArgumentParser(description='Read verified historical results; legacy Final publication is retired.')
    p.add_argument('--history-only',action='store_true')
    p.add_argument('--apply',action='store_true')
    a=p.parse_args()
    if a.apply:p.error('Legacy publication is retired; no build or Router is launched by this tool.')
    from case001_history import print_history
    print_history();return 0


if __name__=='__main__':raise SystemExit(main())
