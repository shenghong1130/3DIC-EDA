#!/usr/bin/env python3
"""Independent contest entry: supplied inputs -> seed -> wire refinement.

No saved public GUIDE, case-name lookup, official report, or network is used.
Gain and search budget are global heuristics. Hidden-case quality is unverified.
"""
import argparse,json,math,shutil,subprocess,sys,tempfile,time
from pathlib import Path
from design import Design
from validate import validate


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--def',dest='def_file',type=Path,required=True)
    for name in ('lef','thermal-report','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--wire-budget',type=int,default=3000)
    p.add_argument('--maze-budget',type=int,default=256)
    p.add_argument('--thermal-gain',type=float,default=12.0)
    p.add_argument('--congestion-slack',type=float,default=0.0)
    p.add_argument('--no-scale-search',action='store_true',
                   help='Disable input-size-based additional maze refinement')
    p.add_argument('--joint-tsv-budget',type=int,default=16000,
                   help='Final joint TSV/Steiner search budget; 0 disables it')
    p.add_argument('--congestion-budget',type=int,default=12000,
                   help='Final congestion-first search under October-31 observed penalty')
    p.add_argument('--escape-budget',type=int,default=100000,
                   help='Cheap parallel-layer escape before expensive congestion search')
    p.add_argument('--fast-seed',action='store_true',
                   help='Use the adaptive seed directly before the shared-tree/thermal/congestion stages')
    a=p.parse_args()
    if min(a.wire_budget,a.maze_budget,a.joint_tsv_budget,a.congestion_budget,a.escape_budget)<0 or a.thermal_gain<=0 or not 0<=a.congestion_slack<=5:p.error('Invalid search budget or gain')
    a.output=a.output.resolve()
    if a.output.exists():p.error('Output exists')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    here=Path(__file__).resolve().parent
    stage=Path(tempfile.mkdtemp(prefix='.portable-',dir=a.output.parent)).resolve()
    seed=stage/'seed.guide';final=stage/'wire.guide';started=time.monotonic()
    baseline=[sys.executable,str(here/'optimize.py'),'--def',str(a.def_file.resolve()),
              '--lef',str(a.lef.resolve()),'--thermal-report',str(a.thermal_report.resolve()),
              '--thermal-gain',str(a.thermal_gain),'--output',str(seed)]
    if a.fast_seed:
        baseline=[sys.executable,str(here/'adaptive_router.py'),'--def',str(a.def_file.resolve()),
                  '--lef',str(a.lef.resolve()),'--thermal-report',str(a.thermal_report.resolve()),'--output',str(seed)]
    subprocess.run(baseline,check=True)
    seed_report=json.loads(seed.with_suffix('.run.json').read_text())
    design=Design(a.def_file,a.lef)
    seed_validation=validate(design,seed)
    wire_report=None
    if a.wire_budget:
        subprocess.run([sys.executable,str(here/'wire_shortener.py'),'--def',str(a.def_file.resolve()),
                        '--lef',str(a.lef.resolve()),'--seed-guide',str(seed),'--output',str(final),
                        '--max-reroutes',str(a.wire_budget),'--congestion-slack',str(a.congestion_slack)],check=True)
        wire_report=json.loads(final.with_suffix('.run.json').read_text())
    else:final=seed
    maze_report=None
    if a.maze_budget:
        maze=stage/'maze.guide'
        subprocess.run([sys.executable,str(here/'maze_wire.py'),'--def',str(a.def_file.resolve()),
                        '--lef',str(a.lef.resolve()),'--seed-guide',str(final),'--output',str(maze),
                        '--max-reroutes',str(a.maze_budget),'--congestion-slack',str(a.congestion_slack)],check=True)
        maze_report=json.loads(maze.with_suffix('.run.json').read_text());final=maze
    compact=stage/'compact.guide'
    subprocess.run([sys.executable,str(here/'compact_guide.py'),'--def',str(a.def_file.resolve()),
                    '--lef',str(a.lef.resolve()),'--seed-guide',str(final),'--output',str(compact)],check=True)
    compact_report=json.loads(compact.with_suffix('.run.json').read_text());final=compact
    scaled_report=None
    extra_budget=0 if a.no_scale_search or not a.maze_budget else max(0,min(8000,math.ceil(len(design.nets)*0.04))-a.maze_budget)
    if extra_budget:
        expanded=stage/'scaled-maze.guide'
        subprocess.run([sys.executable,str(here/'maze_wire.py'),'--def',str(a.def_file.resolve()),
                        '--lef',str(a.lef.resolve()),'--seed-guide',str(final),'--output',str(expanded),
                        '--max-reroutes',str(extra_budget),'--min-pins','2',
                        '--congestion-slack',str(a.congestion_slack)],check=True)
        scaled_report=json.loads(expanded.with_suffix('.run.json').read_text())
        final_compact=stage/'scaled-compact.guide'
        subprocess.run([sys.executable,str(here/'compact_guide.py'),'--def',str(a.def_file.resolve()),
                        '--lef',str(a.lef.resolve()),'--seed-guide',str(expanded),'--output',str(final_compact)],check=True)
        compact_report=json.loads(final_compact.with_suffix('.run.json').read_text());final=final_compact
    joint_report=None
    if a.joint_tsv_budget:
        joint=stage/'joint.guide'
        subprocess.run([sys.executable,str(here/'balanced_thermal.py'),'--def',str(a.def_file.resolve()),
                        '--lef',str(a.lef.resolve()),'--thermal-report',str(a.thermal_report.resolve()),
                        '--seed-guide',str(final),'--output',str(joint),'--max-reroutes',str(a.joint_tsv_budget)],check=True)
        joint_report=json.loads(joint.with_suffix('.run.json').read_text())
        final_compact=stage/'joint-compact.guide'
        subprocess.run([sys.executable,str(here/'compact_guide.py'),'--def',str(a.def_file.resolve()),
                        '--lef',str(a.lef.resolve()),'--seed-guide',str(joint),'--output',str(final_compact)],check=True)
        compact_report=json.loads(final_compact.with_suffix('.run.json').read_text());final=final_compact
    escape_report=None
    if a.escape_budget:
        escaped=stage/'escape.guide'
        subprocess.run([sys.executable,str(here/'layer_escape.py'),'--def',str(a.def_file.resolve()),
                        '--lef',str(a.lef.resolve()),'--seed-guide',str(final),'--output',str(escaped),
                        '--max-reroutes',str(a.escape_budget)],check=True)
        escape_report=json.loads(escaped.with_suffix('.run.json').read_text());final=escaped
    congestion_report=None
    if a.congestion_budget:
        repaired=stage/'congestion.guide'
        subprocess.run([sys.executable,str(here/'congestion_repair.py'),'--def',str(a.def_file.resolve()),
                        '--lef',str(a.lef.resolve()),'--seed-guide',str(final),'--output',str(repaired),
                        '--max-reroutes',str(a.congestion_budget)],check=True)
        congestion_report=json.loads(repaired.with_suffix('.run.json').read_text())
        final_compact=stage/'congestion-compact.guide'
        subprocess.run([sys.executable,str(here/'compact_guide.py'),'--def',str(a.def_file.resolve()),
                        '--lef',str(a.lef.resolve()),'--seed-guide',str(repaired),'--output',str(final_compact)],check=True)
        compact_report=json.loads(final_compact.with_suffix('.run.json').read_text());final=final_compact
    report=dict(version='0.12-portable',seconds=time.monotonic()-started,
                input_only=True,case_name_lookup=False,official_report_required=False,
                global_thermal_gain=a.thermal_gain,wire_budget=a.wire_budget,maze_budget=a.maze_budget,
                fast_seed=a.fast_seed,
                baseline=seed_report,seed_validation=seed_validation,wire=wire_report,maze=maze_report,
                extra_maze_budget=extra_budget,scaled_maze=scaled_report,
                joint_tsv_budget=a.joint_tsv_budget,joint_thermal=joint_report,compaction=compact_report,
                congestion_budget=a.congestion_budget,congestion_repair=congestion_report,
                escape_budget=a.escape_budget,layer_escape=escape_report,
                official_score=None,hidden_case_quality='UNVERIFIED')
    final.replace(a.output)
    a.output.with_suffix('.run.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    if stage.parent!=a.output.parent or not stage.name.startswith('.portable-'):
        raise RuntimeError('Unexpected staging path; retaining files')
    shutil.rmtree(stage)
    print(json.dumps({'version':report['version'],'seconds':report['seconds'],'output':str(a.output)}),flush=True)

if __name__=='__main__':main()
