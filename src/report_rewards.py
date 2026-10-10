"""Publish small reward diagnostics and a cosine-similarity plot, not training data."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import shutil

from data_utils import digest,write_jsonl


def read_rows(path):
    with path.open() as stream:return [json.loads(line) for line in stream]


def condition(source,output):
    summary=json.loads((source/'summary.json').read_text());cases=read_rows(source/'scored.jsonl')
    judgments={};batches=[];initializations={}
    for path in sorted(source.glob('judge-*/output.json')):
        judgments.update({r['id']:r for r in json.loads(path.read_text())['reviews']})
        receipt=json.loads(path.with_name('receipt.json').read_text())
        batches.extend(receipt['usage'])
        init=Path(receipt['base_initialization_receipt'])
        initializations[str(init)]=json.loads(init.read_text())['usage']
    confusion=Counter();agreements=0
    for case in cases:
        judge=judgments[case['id']];case['judge']=judge
        agreements+=judge['verdict']==case['expected']
        if judge['confidence']!='high' or judge['verdict']=='uncertain':confusion['abstained']+=1
        elif judge['verdict']=='pass':confusion['true_accept' if case['expected']=='pass' else 'false_accept']+=1
        else:confusion['true_reject' if case['expected']=='fail' else 'false_reject']+=1
    usage=[*batches,*[r for values in initializations.values() for r in values]]
    total={k:sum(r.get(k,0) for r in usage) for k in ('input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens')}
    total['uncached_input_tokens']=total['input_tokens']-total['cached_input_tokens']
    output.mkdir(parents=True)
    for name in ('summary.json','cases.jsonl'):shutil.copyfile(source/name,output/name)
    write_jsonl(output/'scored.jsonl',cases)
    (output/'usage.json').write_text(json.dumps({'including_base_initialization':total,'incremental_batches':batches},indent=2)+'\n')
    return {'cases':len(cases),'label_agreement_including_low_confidence':agreements,
            'high_confidence_confusion':dict(confusion),'usage_including_base_initialization':total,
            'judge_batch_seconds_excluding_initialization_and_parsing':summary['judge_seconds'],
            'fixture_versions':summary.get('fixture_versions',['semantic-mutations-v1']),
            'source_scored_sha256':digest((source/'scored.jsonl').read_bytes())}


def report(root,output):
    if output.exists():raise ValueError('Choose a new report directory')
    names={'v1-plain':'luna-probe','v1-reversed':'luna-probe-reversed','v1-retrieval':'luna-probe-retrieval',
           'v2-plain':'v2-plain','v2-retrieval':'v2-retrieval'}
    # Fail before creating a half-populated publication when an experiment is pending.
    for folder in names.values():
        if not (root/folder/'summary.json').exists():raise ValueError('Experiment pending: '+folder)
    output.mkdir(parents=True)
    comparisons={label:condition(root/folder,output/label) for label,folder in names.items()}
    for src,dest in [('reference-index-train/manifest.json','index-manifest.json'),('pilot/manifest.json','pilot-manifest.json'),
                     ('v2-embedding/summary.json','embedding-summary.json'),('v2-embedding/similarities.jsonl','similarities.jsonl')]:
        shutil.copyfile(root/src,output/dest)
    results=read_rows(output/'similarities.jsonl');scores={r['id']:r for r in read_rows(output/'v2-plain/scored.jsonl')}
    mutations=[r for r in results if r['expected']=='fail']
    accepted=[r['id'] for r in mutations if scores[r['id']]['assessment'].get('mtgish',{}).get('parse_complete') is True]
    measured={'purpose':'Small hand-authored diagnostic; not independent gold labels, final-test accuracy, or an RL result',
              'conditions':comparisons,'wrong_candidates':len(mutations),'wrong_candidates_parsed':len(accepted),
              'wrong_candidates_above_0_9_reference_cosine':sum(r['cosine_to_faithful_reference']>.9 for r in mutations),
              'cosine_range_wrong_candidates':[min(r['cosine_to_faithful_reference'] for r in mutations),max(r['cosine_to_faithful_reference'] for r in mutations)],
              'fixture_revision_note':'v2 makes exile wording explicit, uses a recognized planeswalker subtype, and fixes a green creature mana/color mismatch; v1 retained unchanged.',
              'final_test_used':False,'rl_training_started':False,'embedding_reward_weight':0}
    test_log=root/'cpu-tests.log'
    if test_log.exists():
        log=test_log.read_text();match=re.search(r'Ran (\d+) tests in [^\n]+\n\nOK\s*$',log)
        measured['cpu_tests']={'passed':int(match.group(1)) if match else None,'log_sha256':digest(test_log.read_bytes())}
    (output/'comparison.json').write_text(json.dumps(measured,indent=2)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    fig,axes=plt.subplots(1,2,figsize=(10,6),sharey=True)
    labels=[r['category'].replace('_',' ') for r in mutations]
    for ax,title,key in [(axes[0],'Similarity to intended card','reference'),(axes[1],'Similarity to nearest existing card','neighbor')]:
        for y,r in enumerate(mutations):
            x=r['cosine_to_faithful_reference'] if key=='reference' else r['nearest'][0]['cosine']
            ax.scatter(x,y,color='#c43e47' if r['id'] in accepted else '#485a9b',s=65,zorder=3)
        ax.set_xlim(.87,1.007);ax.set_xticks([.9,.95,1]);ax.grid(axis='x',alpha=.2)
        ax.set_title(title,fontsize=11);ax.set_xlabel('Cosine similarity')
        ax.spines[['top','right']].set_visible(False)
    axes[0].set_yticks(range(len(labels)),labels);axes[0].invert_yaxis()
    fig.suptitle('Wrong cards can be close in embedding space',fontsize=15)
    fig.legend(handles=[Line2D([0],[0],marker='o',color='w',markerfacecolor='#c43e47',label='mtgish accepted',markersize=8),
                        Line2D([0],[0],marker='o',color='w',markerfacecolor='#485a9b',label='mtgish did not accept',markersize=8)],
               loc='lower center',ncol=2,frameon=False)
    fig.tight_layout(rect=(0,.065,1,.95))
    for extension in ('png','svg'):fig.savefig(output/('similarity-counterexamples.'+extension),dpi=180)
    svg=output/'similarity-counterexamples.svg'
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
    plt.close(fig)
    return measured


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(report(a.root,a.output),indent=2))
