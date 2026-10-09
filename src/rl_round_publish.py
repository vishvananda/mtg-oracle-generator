"""Prepare and publish the larger DPO round using an explicit file allowlist."""
import argparse,csv,hashlib,json,random,shutil
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATASET='vishvananda/mtg-oracle-preferences-v2'
MODEL='vishvananda/mtg-oracle-qwen3-4b-dpo-round2-20261009'


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def paired_intervals(path):
    rows=list(map(json.loads,path.read_text().splitlines()));by_arm=defaultdict(dict)
    for r in rows:by_arm[r['arm']][r['case_id']]=r
    if set(by_arm)!= {'sft','dpo'}:raise ValueError('Expected paired SFT/DPO results')
    if set(by_arm['sft'])!=set(by_arm['dpo']):raise ValueError('Unpaired comparison')
    grouped=defaultdict(list)
    for key,old in by_arm['sft'].items():
        new=by_arm['dpo'][key]
        grouped[old['source_group_id']].append((old,new))
    groups=list(grouped.values());rng=random.Random(1709);result={}
    for metric,score in [('intent',lambda r:r['intent_faithful']),
                         ('mtgish',lambda r:r['checks'].get('mtgish',{}).get('parse_complete') is True)]:
        totals=[(sum(int(score(n))-int(score(o)) for o,n in g),len(g)) for g in groups]
        values=[]
        for _ in range(5000):
            selected=[totals[rng.randrange(len(totals))] for _ in totals]
            values.append(sum(x[0] for x in selected)/sum(x[1] for x in selected))
        values.sort();result[metric]={'difference':sum(x[0] for x in totals)/sum(x[1] for x in totals),
            'family_bootstrap_95_percent_interval':[values[125],values[4874]],'resamples':5000,'seed':1709}
    return {'families':len(groups),'requests':len(by_arm['sft']),'metrics':result,
        'limitation':'Intervals describe this designed panel and correlated LLM labels, not all possible user requests.'}


def prepare(root):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    review=json.loads((root/'merged-review/review.json').read_text())
    if review['approved_pairs_sha256']!=sha(root/'merged-review/preferences.jsonl'):raise ValueError('Preferences changed')
    summary=json.loads((root/'train/training-summary.json').read_text())
    if summary['stopped_for_budget'] or summary['steps']!=summary['planned_steps']:raise ValueError('Incomplete DPO run')
    report=root/'report';report.mkdir(exist_ok=True)
    rows=[r for r in json.loads((root/'train/checkpoints/trainer_state.json').read_text())['log_history'] if 'loss' in r]
    fields=['step','epoch','loss','learning_rate','rewards/margins','rewards/accuracies']
    with (report/'training.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n');writer.writeheader()
        writer.writerows({key:r.get(key) for key in fields} for r in rows)
    fig,ax=plt.subplots(figsize=(8,3.5));ax.plot([r['step'] for r in rows],[r['loss'] for r in rows],linewidth=1)
    ax.set(xlabel='Optimizer update',ylabel='Training DPO loss',title=f"{summary['pairs']} preference pairs; one pass")
    fig.tight_layout();fig.savefig(report/'loss-curve.png',dpi=160);plt.close(fig)
    comparisons={}
    for label,path in [('nf4_development',root/'nf4-development'),('q4_development',root/'final-q4/comparison'),
                       ('q4_release',root/'release-q4/comparison')]:
        comparisons[label]=json.loads((path/'comparison.json').read_text())
        shutil.copyfile(path/'comparison.json',report/(label+'.json'))
        if label!='nf4_development':
            (report/(label+'-paired-intervals.json')).write_text(json.dumps(paired_intervals(path/'cases.jsonl'),indent=2)+'\n')
    release=comparisons['q4_release']['metrics'];dev=comparisons['q4_development']['metrics']
    lines=['# DPO round two: measured results','',f"Trained {summary['pairs']} reviewed pairs for {summary['steps']} updates (one pass).",'',
        'The policy and frozen reference started at the completed one-epoch SFT adapter.',
        'The preference generator and reviewer are separate Sol sessions; their errors can be correlated.',
        'Luna supplied an additional critic; every proposed pair received a final high-effort Sol review.',
        'These are model judgments, not human-expert labels or official rules certification.','',
        '| Production Q4_0 measure | SFT development | DPO development | SFT locked release | DPO locked release |',
        '| --- | ---: | ---: | ---: | ---: |']
    for key in ('schema_valid','intent_faithful','faithful_clean_oracle','mtgish_accepted','faithful_and_parsed','exact_metadata_failure','abstentions'):
        values=[f"{m[key]}/{m['cases']}" for m in (dev['sft'],dev['dpo'],release['sft'],release['dpo'])]
        lines.append('| '+key.replace('_',' ')+' | '+' | '.join(values)+' |')
    lines+=['','The two 128-request panels contain 96 families each; each original-design family has two detail levels.',
        'Source-card families were seen during SFT but excluded from this preference round. This tests new requests,',
        'not wholly unseen-card knowledge. The old 400-card SFT final test was not reused for decisions.',
        'The final checkpoint was preselected for release evaluation; midpoint scores are development diagnostics.',
        'Parser acceptance does not establish intent, balance or legality. Unsupported valid mechanics remain possible.','',
        'Intent requires schema/explicit-field success and a high-confidence pass without violations. Clean Oracle also requires style score 2.','',
        '![DPO training loss](loss-curve.png)','',
        'Loss and training reward accuracy are not held-out card accuracy. Raw metrics and family-bootstrap intervals accompany this report.',
        'NF4 and Q4_0 comparisons are reported separately. Production comparisons share system prompt, guide, greedy decoding and limits.',
        'No workshop replacement is automatic; review the generated cards and critical regressions before promotion.','']
    (report/'README.md').write_text('\n'.join(lines))
    data=root/'public-dataset';data.mkdir(exist_ok=True)
    for name in ('preferences.jsonl','audit.jsonl','review.json'):shutil.copyfile(root/'merged-review'/name,data/name)
    for name in ('manifest.json','development-cases.jsonl','release-cases.jsonl'):
        shutil.copyfile(root/'evaluation'/name,data/('evaluation-'+name))
    shutil.copyfile(root/'reserved-families.json',data/'reserved-families.json')
    shutil.copyfile(root/'serving-system.txt',data/'serving-system.txt')
    for name in ('pipeline-code.json','audit-quality.json','audit-corrections.json'):
        shutil.copyfile(root/name,data/name)
    shutil.copyfile(ROOT/'DATA_LICENSE.md',data/'DATA_LICENSE.md')
    for name in ('reward-judge-v3.txt','intent-checklist-v1.txt','intent-repair-v1.txt'):
        shutil.copyfile(ROOT/'prompts'/name,data/name)
    for label,source in [('nf4-development',root/'nf4-development/cases.jsonl'),('q4-development',root/'final-q4/comparison/cases.jsonl'),
                         ('q4-release',root/'release-q4/comparison/cases.jsonl')]:shutil.copyfile(source,data/(label+'.jsonl'))
    quality=json.loads((root/'audit-quality.json').read_text())
    sources=[('audit',root/quality['review_directory'],root/'audit-candidates.jsonl')]
    for batch in sorted(root.glob('batch-*')):
        if (batch/'review/review.json').exists():sources.append((batch.name,batch/'review',batch/'sample/candidates.jsonl'))
    for label,folder,candidates in sources:
        dest=data/'sources'/label;dest.mkdir(parents=True,exist_ok=True)
        for name in ('review.json','audit.jsonl','candidates-reviewed.jsonl','requirements.jsonl'):
            shutil.copyfile(folder/name,dest/name)
        shutil.copyfile(candidates,dest/'candidates.jsonl')
    card=f'''---
language: [en]
license: other
license_name: mixed-rights-card-content
license_link: https://huggingface.co/datasets/{DATASET}/blob/main/DATA_LICENSE.md
configs:
  - config_name: preferences
    default: true
    data_files:
      - split: train
        path: preferences.jsonl
---

# MTG Oracle preference data v2

{summary['pairs']} reviewed conversational TRL `prompt/chosen/rejected` pairs.
One pair per source family, no policy-training use of the reserved evaluation families.
Most positives are Qwen samples or Sol repairs; provenance and short judgments are in the audit.
Separate blinded Sol sessions are not independent human labels. Luna criticism and
disagreement adjudication supplement schema, explicit-field and upstream mtgish checks.

Evaluation files are diagnostics and must not be mixed into training when reproducing
these results. Source cards were seen in SFT; the evaluation measures new-request
generalization. Source Oracle content and generated descriptions have mixed rights;
see [attribution](DATA_LICENSE.md). Code is [on GitHub](https://github.com/vishvananda/mtg-oracle-generator).
No artwork, credentials, raw worker conversations or optimizer states are included.

[Model and results](https://huggingface.co/{MODEL})
'''
    (data/'README.md').write_text(card)
    model=root/'public-model';model.mkdir(exist_ok=True)
    allowed=('adapter_model.safetensors','adapter_config.json','tokenizer.json','tokenizer_config.json',
             'special_tokens_map.json','vocab.json','merges.txt','added_tokens.json')
    for name in allowed:
        source=root/'train/adapter'/name
        if source.exists():shutil.copyfile(source,model/name)
    for checkpoint in sorted((root/'train/checkpoints').glob('checkpoint-*')):
        dest=model/'checkpoints'/checkpoint.name;dest.mkdir(parents=True,exist_ok=True)
        for name in allowed:
            if (checkpoint/name).exists():shutil.copyfile(checkpoint/name,dest/name)
    for source in report.iterdir():shutil.copyfile(source,model/source.name)
    for name in ('training-summary.json','identity.json','phase-complete.json','midpoint-policy.json'):
        shutil.copyfile(root/'train'/name,model/name)
    for name,source in [('system-prompt.txt',root/'serving-system.txt'),('DATA_LICENSE.md',ROOT/'DATA_LICENSE.md')]:
        shutil.copyfile(source,model/name)
    header=f'''---
language: [en]
license: apache-2.0
library_name: peft
base_model: Qwen/Qwen3-4B-Instruct-2507
pipeline_tag: text-generation
tags: [mtg, oracle-text, lora, dpo]
datasets: [{DATASET}]
---

Load this complete DPO adapter **once on the original base**, revision
`cdbee75f17c01a7cc42f958dc650907174af0554`. Do not stack the SFT adapter separately.
Its starting SFT revision was `decd0eb5344719c298649ca4cf2be4950da4b18d` in
`vishvananda/mtg-oracle-qwen3-4b-checkpoints-20261007`. All saved policy checkpoints
are under `checkpoints/`; reference adapter and optimizer states are excluded.

Use the supplied system prompt, disable thinking and request one editable JSON
card. Pin this repository commit for reproducibility. Source card rights are
separate from the Apache-2.0 model license; see [attribution](DATA_LICENSE.md).

'''
    (model/'README.md').write_text(header+(report/'README.md').read_text())
    return {'pairs':summary['pairs'],'steps':summary['steps'],'report':str(report)}


def upload(root):
    from huggingface_hub import HfApi,hf_hub_download
    api=HfApi();receipts={}
    for kind,repo_id,directory in [('dataset',DATASET,root/'public-dataset'),('model',MODEL,root/'public-model')]:
        paths=sorted(p for p in directory.rglob('*') if p.is_file() and p.name!='release-manifest.json')
        files={str(p.relative_to(directory)):{'sha256':sha(p),'bytes':p.stat().st_size} for p in paths}
        (directory/'release-manifest.json').write_text(json.dumps({'files':files},indent=2)+'\n')
        api.create_repo(repo_id,repo_type=kind,private=False,exist_ok=True)
        commit=api.upload_folder(repo_id=repo_id,repo_type=kind,folder_path=str(directory),
            allow_patterns=[*files,'release-manifest.json'],commit_message='Publish reviewed DPO round two and production evaluation')
        # Verify every curated publication against the immutable remote revision.
        for name,entry in files.items():
            if sha(Path(hf_hub_download(repo_id,name,repo_type=kind,revision=commit.oid)))!=entry['sha256']:
                raise ValueError('Remote publication hash differs: '+name)
        receipts[kind]={'repo_id':repo_id,'revision':commit.oid,'files_verified':len(files),
            'url':'https://huggingface.co/'+('datasets/' if kind=='dataset' else '')+repo_id}
        (root/(kind+'-publication.json')).write_text(json.dumps(receipts[kind],indent=2)+'\n')
    return receipts


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    choice=p.add_mutually_exclusive_group(required=True);choice.add_argument('--prepare-only',action='store_true');choice.add_argument('--upload-only',action='store_true')
    a=p.parse_args();print(json.dumps(prepare(a.root) if a.prepare_only else upload(a.root),indent=2))
