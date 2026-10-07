"""Collect paired parser scores, measured loss curves, and a reviewable release report."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from data_utils import digest,write_jsonl
from hub_dataset import verify
from paths import PROJECT
from plot_training import plot
from evaluation_selection import POLICY,select_cases,evaluation_allowed


def paired_identity(base,adapter):
    fields=('dataset_manifest_sha256','split','model','model_revision','config_sha256','seed','decoding',
            'config_canonical_sha256','code_files','backend','scheduled_cases','scheduled_ids_sha256','selection','selection_code_sha256')
    if any(base.get(k)!=adapter.get(k) for k in fields): raise ValueError('Base/adapter generation conditions differ')
    if base.get('adapter_files') or not adapter.get('adapter_files'): raise ValueError('Expected one base and one adapter arm')
    if not base.get('complete') or not adapter.get('complete'): raise ValueError('Paired generation is incomplete')
    if any(r.get('cases')!=r.get('scheduled_cases') for r in (base,adapter)): raise ValueError('Scheduled cases are missing')


def comparison(base,adapter):
    paired_identity(base['generation'],adapter['generation'])
    if base['parser']!=adapter['parser'] or base['cases']!=adapter['cases']:
        raise ValueError('Parser or evaluation denominator differs')
    return {'format_version':1,'split':adapter['split'],'final_test':adapter['split']=='test',
            'dataset_manifest_sha256':adapter['dataset_manifest_sha256'],
            'adapter_files':adapter['generation']['adapter_files'],
            'base':base,'adapter':adapter,
            'acceptance_change_percentage_points':100*(adapter['mtgish_acceptance_all_cases']-base['mtgish_acceptance_all_cases']),
            'interpretation':'Parser recognition, not intent fidelity, balance, or official card legality.',
            'automatic_promotion':False}


def plot_acceptance(report,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    labels=['JSON object','Schema valid','mtgish accepted'];keys=['json_objects','schema_valid','mtgish_accepted']
    fig,ax=plt.subplots(figsize=(8,4.5),layout='constrained')
    for offset,arm,color in [(-.18,'base','#8a9aa9'),(.18,'adapter','#23658e')]:
        metrics=report[arm];values=[100*metrics[k]/metrics['cases'] for k in keys]
        bars=ax.bar([i+offset for i in range(3)],values,width=.36,label=arm.title(),color=color)
        ax.bar_label(bars,fmt='%.1f%%',fontsize=8,padding=3)
    ax.set(xticks=range(3),xticklabels=labels,ylabel='% of all scheduled cases',ylim=(0,110),
           title=f'{report["split"].title()}: {report["base"]["cases"]:,} cases per model')
    ax.spines[['top','right']].set_visible(False);ax.legend(frameon=False);ax.grid(axis='y',alpha=.15)
    fig.supxlabel('Raw greedy output; parser recognition does not establish intent fidelity.',fontsize=8)
    for suffix in ('png','svg'): fig.savefig(output/f'parser-acceptance.{suffix}',dpi=180)
    plt.close(fig)


def run(dataset,adapter,generations,split,output):
    manifest=verify(dataset);dataset_hash=digest((dataset/'manifest.json').read_bytes())
    summary=json.loads((adapter/'run-summary.json').read_text())
    if summary['training_identity']['dataset_manifest_sha256']!=dataset_hash:
        raise ValueError('Training and evaluation datasets differ')
    predictions={arm:generations/f'{arm}-{split}.jsonl' for arm in ('base','adapter')}
    receipts={arm:json.loads(path.with_suffix('.manifest.json').read_text()) for arm,path in predictions.items()}
    paired_identity(receipts['base'],receipts['adapter'])
    adapter_files={name:digest((adapter/name).read_bytes()) for name in ('adapter_config.json','adapter_model.safetensors')}
    trained=receipts['adapter']
    selection=trained.get('selection')
    complete=evaluation_allowed(summary,split,selection)
    selected=select_cases([json.loads(line) for line in (dataset/f'{split}.jsonl').read_text().splitlines()],selection)
    if trained['scheduled_ids_sha256']!=digest([r['example_id'] for r in selected]):
        raise ValueError('Evaluation panel identity differs')
    if (trained['dataset_manifest_sha256']!=dataset_hash or trained['split']!=split
            or trained['adapter_files']!=adapter_files or trained['model']!=summary['config']['model']
            or trained['model_revision']!=summary['model_revision']
            or trained['config_canonical_sha256']!=summary['training_identity']['config_sha256']
            or trained['scheduled_cases']!=len(selected)):
        raise ValueError('Generation provenance differs from the trained model/dataset')
    identity={'dataset_manifest_sha256':dataset_hash,'adapter_files':adapter_files,'split':split,
              'predictions':{arm:digest(path.read_bytes()) for arm,path in predictions.items()},
              'run_summary_sha256':digest((adapter/'run-summary.json').read_bytes())}
    for arm in receipts:
        if identity['predictions'][arm]!=receipts[arm]['predictions_sha256']: raise ValueError('Predictions changed')
    output.mkdir(parents=True,exist_ok=True);pin=output/'request.json'
    if pin.exists() and json.loads(pin.read_text())!=identity: raise ValueError('Post-training inputs changed')
    pin.write_text(json.dumps(identity,indent=2)+'\n')
    metrics={}
    for arm,path in predictions.items():
        destination=output/arm;staging=output/(arm+'.preparing')
        if not destination.exists():
            if staging.exists(): shutil.rmtree(staging)
            subprocess.run([sys.executable,str(PROJECT/'src/evaluate.py'),'--dataset',str(dataset),
                '--predictions',str(path),'--split',split,'--output',str(staging)],check=True)
            staging.rename(destination)
        metrics[arm]=json.loads((destination/'metrics.json').read_text())
        if metrics[arm]['generation']!=receipts[arm]: raise ValueError('Existing score belongs to another generation')
    report=comparison(metrics['base'],metrics['adapter'])
    report.update(training_complete=complete,training_epoch=summary['metrics']['epoch'],
                  evaluation_scope='development_panel' if selection else 'full_split')
    generated={arm:{r['example_id']:r for r in map(json.loads,path.read_text().splitlines())}
               for arm,path in predictions.items()}
    review=select_cases(selected,{'policy':POLICY,'limit':32})
    write_jsonl(output/'intent-review.jsonl',[{'example_id':r['example_id'],'style':r['style'],
        'description':r['description'],'reference':r['target'],
        'base':generated['base'][r['example_id']]['raw_text'],
        'adapter':generated['adapter'][r['example_id']]['raw_text'],
        'manual_verdict':None,'rubric':'Preserves explicit intent; sensible editable defaults; name/rarity/loyalty present when appropriate. Reference is not the only valid completion.'}
        for r in review])
    report['training_summary_sha256']=identity['run_summary_sha256']
    plot(adapter/'run-summary.json',output,state_path=adapter/'trainer_state.json')
    plot_acceptance(report,output)
    (output/'comparison.json').write_text(json.dumps(report,indent=2)+'\n')
    table='| Model | Cases | Schema valid | mtgish accepted |\n| --- | ---: | ---: | ---: |\n'
    for arm in ('base','adapter'):
        m=report[arm]
        table+=f'| {arm.title()} | {m["cases"]:,} | {m["schema_valid"]:,} | {100*m["mtgish_acceptance_all_cases"]:.2f}% |\n'
    scope=f'Development panel; training epoch {summary["metrics"]["epoch"]:.4f}. Not a full-split score.\n\n' if selection else ''
    (output/'README.md').write_text(f'# {split.title()} — base versus tuned model\n\n'+scope+table+'\n'
        '![Training loss and token accuracy](training-curve.png)\n\n![Parser acceptance](parser-acceptance.png)\n\n'
        'The denominator includes all scheduled cases, including malformed JSON, unsupported layouts, '
        'ignored cards, and parser errors. Parser acceptance does not prove intent preservation or legality. '
        'Teacher-forced token accuracy is a separate metric.\n\n'
        'Each arm retains `metrics.json` (style/type breakdowns and exact parser/model provenance) '
        'and `cases.jsonl` (failure diagnostics). `comparison.json` pins the trained adapter and dataset. '
        'Inspect wrong players/zones, omitted abilities, loyalty and multi-face cards before promoting a model. '
        '`intent-review.jsonl` supplies 32 paired cases for that review; no intent score is inferred. '
        'This command does not change the live service.\n')
    files={str(p.relative_to(output)):digest(p.read_bytes()) for p in sorted(output.rglob('*'))
           if p.is_file() and p.name!='report-manifest.json'}
    (output/'report-manifest.json').write_text(json.dumps({'files':files,'identity':identity},indent=2)+'\n')
    return {'split':split,'cases':report['adapter']['cases'],'report':str(output/'README.md'),
            'base_acceptance':report['base']['mtgish_acceptance_all_cases'],
            'adapter_acceptance':report['adapter']['mtgish_acceptance_all_cases']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,required=True);p.add_argument('--adapter',type=Path,required=True)
    p.add_argument('--generations',type=Path,required=True);p.add_argument('--split',choices=['validation','test'],required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(run(a.dataset,a.adapter,a.generations,a.split,a.output),indent=2))
