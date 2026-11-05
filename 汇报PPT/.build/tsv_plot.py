import json,pathlib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=pathlib.Path(__file__).resolve().parents[2];O=ROOT/'汇报PPT';C=ROOT/'EDA/code_3DIC EDA'
results={};maps={}
for v in ['v3_5','v3_6']:
 p=C/f'outputs/runs/open_3d_case001/case001_{v}/result.json'
 r=json.loads(p.read_text(encoding='utf8'));results[v]=r
 nx=len(r['x_lines'])-1;ny=len(r['y_lines'])-1
 def pos(n):
  node=n['tsv_node'];x=int(node%nx);y=int(node//nx%ny)
  return ((r['x_lines'][x]+r['x_lines'][x+1])/2/r['dbu_per_micron'],(r['y_lines'][y]+r['y_lines'][y+1])/2/r['dbu_per_micron'])
 maps[v]={n['net']:pos(n) for n in r['nets'] if n.get('tsv_node') is not None}
changed={k for k,v in maps['v3_5'].items() if maps['v3_6'].get(k)!=v}
for v,pts in maps.items():
 fig,ax=plt.subplots(figsize=(6.6,4.6),dpi=180)
 fixed=[p for n,p in pts.items() if n not in changed];move=[p for n,p in pts.items() if n in changed]
 ax.scatter(*zip(*fixed),s=9,c='#008c9e',alpha=.6,label='Unchanged TSV')
 if move:ax.scatter(*zip(*move),s=22,edgecolors='white',linewidth=.4,c='#c66d1d',label='Relocated nets')
 r=results[v];dbu=r['dbu_per_micron'];ax.set_xlim(r['x_lines'][0]/dbu,r['x_lines'][-1]/dbu);ax.set_ylim(r['y_lines'][0]/dbu,r['y_lines'][-1]/dbu)
 ax.set_aspect('equal');ax.set_xlabel('X (µm)',fontsize=12);ax.set_ylabel('Y (µm)',fontsize=12);ax.tick_params(labelsize=10);ax.spines[['top','right']].set_visible(False);ax.grid(alpha=.15);ax.legend(loc='upper left',bbox_to_anchor=(1.01,1),fontsize=9,frameon=False)
 fig.tight_layout();fig.savefig(O/f'assets/tsv_case001_{v}.png',facecolor='white');plt.close(fig)
(O/'assets/tsv_coordinates.json').write_text(json.dumps({'case':'open_3d_case001','unit':'um','count':{k:len(v) for k,v in maps.items()},'changed_final_sites':len(changed),'coordinates':maps},indent=2),encoding='utf8')
print('TSV final sites changed',len(changed))
