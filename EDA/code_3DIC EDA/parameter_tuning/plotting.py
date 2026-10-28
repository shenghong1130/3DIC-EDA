"""Offline Matplotlib PNGs; missing/failed official data is never drawn as zero."""
from pathlib import Path
import io
import os
from support import atomic_bytes, TuningError

def make_figures(rows, report, comparable=True):
    previous={key:os.environ.get(key) for key in ['OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']}
    try:
        # Tiny charts do not need a machine-wide BLAS thread pool. Restore the
        # environment afterwards so Router/evaluator subprocesses are unchanged.
        for key in previous:
            os.environ[key]='1'
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as e:
        raise TuningError('Install plotting dependency: python -m pip install -r parameter_tuning/requirements.txt') from e
    finally:
        for key,value in previous.items():
            if value is None:
                os.environ.pop(key,None)
            else:
                os.environ[key]=value
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9})
    target=Path(report)/'figures';target.mkdir(parents=True,exist_ok=True)
    labels=[r['id']+'\n'+r['state']+'/'+r['official_status'] for r in rows]
    x=np.arange(len(rows));figures=[]
    def finish(fig,ax,name,title,ylabel,has_data):
        ax.set_title(title);ax.set_ylabel(ylabel)
        ax.set_xticks(x,labels,rotation=60,ha='right');ax.grid(axis='y',alpha=.2)
        if not has_data:
            ax.text(.5,.55,'No comparable observations yet\nMissing values are N/A (not zero)',
                transform=ax.transAxes,ha='center',va='center',bbox={'facecolor':'white','alpha':.95,'edgecolor':'#dddddd'})
        if not comparable:
            ax.text(.5,1.06,'BATCH BLOCKED / INCOMPARABLE',transform=ax.transAxes,ha='center',color='#b91c1c')
        fig.tight_layout();b=io.BytesIO();fig.savefig(b,format='png',dpi=140);plt.close(fig)
        atomic_bytes(target/name,b.getvalue());figures.append(name)
    for name,metric,title,ylabel,scale,official in [
        ('score_comparison.png','total_score','Official total score','Score',1,True),
        ('runtime_comparison.png','router_runtime_seconds','Router helper elapsed time','Seconds',1,False),
        ('memory_comparison.png','router_peak_rss_bytes','Router peak RSS (see source in CSV)','MiB',1/1048576,False),
        ('parameter_sensitivity.png','delta_total_score','Change vs baseline: official total','Score delta',1,True)]:
        fig,ax=plt.subplots(figsize=(max(10,len(rows)*.8),6));found=False
        for i,r in enumerate(rows):
            value=r.get(metric)
            valid=(r['eligible'] and comparable) if official else r.get('router_status')=='ROUTED'
            if value is not None and valid:
                ax.bar(i,value*scale,color='#3b82f6' if official else '#64748b');found=True
            else:
                ax.annotate('N/A',(i,0),ha='center',fontsize=8,color='#b91c1c')
        if name=='parameter_sensitivity.png' and not found:
            title+=' (no explicit total field / baseline unavailable)'
        finish(fig,ax,name,title,ylabel,found)
    fig,ax=plt.subplots(figsize=(max(10,len(rows)*.8),6));found=False
    for j,(metric,color) in enumerate([('wirelength_score','#3b82f6'),('congestion_score','#10b981'),('thermal_score','#f59e0b')]):
        values=[r.get(metric) if r['eligible'] and comparable and r.get(metric) is not None else float('nan') for r in rows]
        found=found or any(v==v for v in values)
        ax.bar(x+(j-1)*.24,values,width=.24,color=color,label=metric)
    ax.legend();finish(fig,ax,'score_breakdown.png','Official component scores','Score',found)
    # A useful sensitivity chart even when this evaluator has no total field.
    fig,ax=plt.subplots(figsize=(max(10,len(rows)*.8),6));found=False
    for j,(metric,color) in enumerate([('wirelength_score','#3b82f6'),('congestion_score','#10b981'),('thermal_score','#f59e0b')]):
        values=[r.get('delta_'+metric) if r['eligible'] and comparable and r.get('delta_'+metric) is not None else float('nan') for r in rows]
        found=found or any(v==v for v in values)
        ax.bar(x+(j-1)*.24,values,width=.24,color=color,label=metric)
    ax.axhline(0,color='#888888',linewidth=.6);ax.legend()
    finish(fig,ax,'parameter_component_sensitivity.png','Component score change vs baseline','Score delta',found)
    return figures
