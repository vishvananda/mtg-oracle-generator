"""Plot raw training observations and only actually measured validation points."""
import argparse
import csv
import json
from pathlib import Path
import statistics


def plot(summary_path,output,csv_path=None,state_path=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    summary=json.loads(summary_path.read_text())
    if state_path:
        history=json.loads(state_path.read_text())['log_history']
        rows=[r for r in history if 'loss' in r]
    else:
        history=[]
        with csv_path.open() as stream:
            rows=[{k:float(v) for k,v in r.items()} for r in csv.DictReader(stream)]
    if not rows: raise ValueError('No training observations')
    output.mkdir(parents=True,exist_ok=True)
    with (output/'training-curve.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=['step','loss','mean_token_accuracy'],lineterminator='\n')
        writer.writeheader(); writer.writerows({k:r[k] for k in writer.fieldnames} for r in rows)
    fig,axes=plt.subplots(1,2,figsize=(11,4.5),layout='constrained')
    validation={0:summary['base_eval'],summary['optimizer_steps']:summary['adapter_eval']}
    for r in history:
        if 'eval_loss' in r: validation[r['step']]=r
    for ax,field,metric,label,factor in [
        (axes[0],'loss','eval_loss','Completion-token loss',1),
        (axes[1],'mean_token_accuracy','eval_mean_token_accuracy','Token accuracy (%)',100)]:
        x=[r['step'] for r in rows]; y=[r[field]*factor for r in rows]
        ax.plot(x,y,color='#3177a4',alpha=.18,linewidth=.7,label='Training batches')
        ax.plot(x,[statistics.mean(y[max(0,i-49):i+1]) for i in range(len(y))],
                color='#23658e',linewidth=2,label='Training: 50-step mean')
        points=sorted((s,r[metric]*factor) for s,r in validation.items() if metric in r)
        ax.scatter([p[0] for p in points],[p[1] for p in points],color='#bc5525',s=50,label='Measured validation',zorder=5)
        ax.set(xlabel='Optimizer step',ylabel=label);ax.grid(alpha=.2)
        ax.spines[['top','right']].set_visible(False);ax.legend(frameon=False,fontsize=8)
    counts=summary['dataset']['splits']
    fig.suptitle(f'{summary["config"]["model"].split("/")[-1]} · {counts["train"]:,} training examples',fontweight='bold')
    fig.supxlabel(f'{counts["validation"]:,} validation examples; {len(validation)} measured evaluation points. '
                  'Teacher-forced token accuracy is not whole-card correctness.',fontsize=8)
    for suffix in ('png','svg'): fig.savefig(output/f'training-curve.{suffix}',dpi=180)
    svg=output/'training-curve.svg'
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
    plt.close(fig)
    return {'training_observations':len(rows),'validation_steps':sorted(validation)}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--summary',type=Path,required=True)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--csv',type=Path)
    source.add_argument('--state',type=Path)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(plot(a.summary,a.output,a.csv,a.state)))
