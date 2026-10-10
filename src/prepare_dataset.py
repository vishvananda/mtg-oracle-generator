"""Merge reviewed exports into a portable, hash-pinned HF/TRL dataset.

Never moves a source group between splits. Holdouts win description collisions.
Keeps provenance alongside both standard messages and prompt/completion records.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil

from jsonschema import Draft202012Validator
from data_utils import digest, write_jsonl
from export_full_corpus import select_unique
from paths import PROJECT
from training_names import NAME_INSTRUCTIONS
from training_rarity import RARITY_INSTRUCTIONS

SPLITS=('train','validation','test')


def checked_file(root, name, manifest):
    path=root/name
    if Path(name).name!=name or path.is_symlink():
        raise ValueError('Dataset files must be local regular files')
    pin=manifest['files'][name]
    if digest(path.read_bytes())!=(pin['sha256'] if isinstance(pin,dict) else pin):
        raise ValueError('Hash mismatch: '+name)
    return path


def audit(rows):
    groups={}; cards={}; ids=set()
    for row in rows:
        if row['split'] not in SPLITS: raise ValueError('Invalid split')
        if row['example_id'] in ids: raise ValueError('Duplicate example ID')
        ids.add(row['example_id'])
        card=row['source']['oracle_id']
        for label,key,owners in [('group',row['source_group_id'],groups),('card',card,cards)]:
            if owners.setdefault(key,row['split'])!=row['split']:
                raise ValueError(f'Source {label} crosses splits: {key}')
    return {'group_overlap':0,'card_overlap':0,'description_overlap':0,
            'examples':len(rows),'source_cards':len(cards),'source_groups':len(groups)}


def prepare(inputs,output,release_name,allow_partial=False):
    if output.exists(): raise ValueError('Choose a new immutable output directory')
    by_id={}; inputs_info=[]; complete=True
    schema=json.loads((PROJECT/'schemas/design-draft-v1.json').read_text())
    schema['required']=sorted(set(schema['required']+['name','rarity']))
    validator=Draft202012Validator(schema)
    for root in inputs:
        manifest_bytes=(root/'manifest.json').read_bytes()
        manifest=json.loads(manifest_bytes)
        input_complete=manifest.get('generation_complete') is True
        complete &= input_complete
        if not input_complete and not allow_partial:
            raise ValueError('Input generation not marked complete; use --allow-partial for a labeled pilot')
        for split in SPLITS:
            name=split+'.jsonl'
            if name not in manifest['files']: continue
            for line in checked_file(root,name,manifest).read_text().splitlines():
                row=json.loads(line)
                if row['split']!=split: raise ValueError('Row/file split mismatch')
                validator.validate(row['target'])
                if not row['target']['name'].strip(): raise ValueError('Empty card name')
                if row.get('status') not in (None,'accepted'): raise ValueError('Unaccepted row')
                if row.get('deterministic_issues') or row.get('export_issues'):
                    raise ValueError('Row still has review issues')
                review=row.get('fidelity_review')
                if review and (review['verdict']!='pass' or review['issues']):
                    raise ValueError('Fidelity review not passed')
                prior=by_id.setdefault(row['example_id'],row)
                if prior!=row: raise ValueError('Conflicting duplicate example ID')
        inputs_info.append({'version':manifest.get('version'),'manifest_sha256':digest(manifest_bytes),
                            'generation_complete':input_complete})
    rows=list(by_id.values())
    audit(rows)
    rows,duplicates=select_unique(rows)
    checks=audit(rows)
    counts=Counter(r['split'] for r in rows)
    if not counts['train'] or not counts['validation']:
        raise ValueError('Both train and validation examples are required')
    if complete and not counts['test']: raise ValueError('Complete release needs a final test split')
    prompt=(PROJECT/'prompts/design-draft-v2.txt').read_text().rstrip()+NAME_INSTRUCTIONS+RARITY_INSTRUCTIONS+'\n'
    output.mkdir(parents=True)
    files={}
    for split in SPLITS:
        selected=sorted((r for r in rows if r['split']==split),key=lambda r:r['example_id'])
        if not selected: continue
        files[split+'.jsonl']=write_jsonl(output/(split+'.jsonl'),selected)
        messages=[]; supervised=[]
        for row in selected:
            context=[{'role':'system','content':prompt},{'role':'user','content':row['description']}]
            answer=[{'role':'assistant','content':json.dumps(row['target'],ensure_ascii=False,separators=(',',':'))}]
            messages.append({'example_id':row['example_id'],'source_group_id':row['source_group_id'],
                             'style':row['style'],'messages':context+answer})
            supervised.append({'prompt':context,'completion':answer})
        files[split+'.messages.jsonl']=write_jsonl(output/(split+'.messages.jsonl'),messages)
        files[split+'.sft.jsonl']=write_jsonl(output/(split+'.sft.jsonl'),supervised)
    files['duplicates.jsonl']=write_jsonl(output/'duplicates.jsonl',duplicates)
    (output/'system-prompt.txt').write_text(prompt)
    (output/'draft-schema.json').write_text(json.dumps(schema,indent=2)+'\n')
    split_map={r['source']['oracle_id']:r['split'] for r in rows}
    (output/'split-map.json').write_text(json.dumps(split_map,sort_keys=True,indent=2)+'\n')
    shutil.copyfile(PROJECT/'DATA_LICENSE.md',output/'DATA_LICENSE.md')
    for name in ('system-prompt.txt','draft-schema.json','split-map.json','DATA_LICENSE.md'):
        files[name]={'sha256':digest((output/name).read_bytes())}
    manifest={'format_version':1,'release':release_name,'status':'complete' if complete else 'partial',
              'generation_complete':complete,'counts':dict(counts),'checks':checks,
              'styles':dict(Counter(r['style'] for r in rows)),
              'names_and_rarity':True,'inputs':inputs_info,'files':files,
              'test_evaluated':False,'duplicates_removed':len(duplicates),
              'human_reviewed':False,'automatic_training':False,'public_uploads':False}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    configs=''
    for kind,suffix in [('messages','.messages.jsonl'),('sft','.sft.jsonl'),('records','.jsonl')]:
        configs+=f'  - config_name: {kind}\n'+('    default: true\n' if kind=='messages' else '')+'    data_files:\n'
        for split in SPLITS:
            if counts[split]: configs+=f'      - split: {split}\n        path: {split}{suffix}\n'
    card=('---\nlanguage:\n  - en\nlicense: other\nlicense_name: mixed-rights-card-data\n'
          'license_link: DATA_LICENSE.md\ntask_categories:\n  - text-generation\nconfigs:\n'+configs+'---\n\n'
          f'# MTG Oracle descriptions — {release_name}\n\n'
          f'Status: **{manifest["status"]}**. Train: {counts["train"]:,}; validation: {counts["validation"]:,}; test: {counts["test"]:,}.\n\n'
          'Natural-language card requests paired with editable card JSON and Oracle text. '
          'Targets include names and rarity. Unspecified characteristics are editable suggestions, not uniquely correct answers.\n\n'
          'Use `messages` for ordinary chat SFT, `sft` for prompt/completion masking, or `records` for source IDs, detail levels, '
          'specified/proposed fields and review provenance. The local trainer reads `*.sft.jsonl` directly. '
          'Do not flatten all splits and randomly re-split descriptions: related cards share a frozen source group.\n\n'
          'Descriptions were automatically generated and independently reviewed, with deterministic checks and controls; '
          'they are not human-certified. Historical wording uses a recorded exclusion policy for mechanical errata and obsolete rules. '
          'Names and rarity were added after review using the pinned source. No artwork is included.\n\n'
          'No final-test generation accuracy is claimed by this dataset release. The known 16k pilot predates name/rarity targets '
          'and is a different dataset. See `manifest.json` for counts, provenance and SHA-256 hashes. '
          'See [data rights and attribution](DATA_LICENSE.md) before redistribution.\n')
    (output/'README.md').write_text(card)
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--input',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--release-name',required=True)
    p.add_argument('--allow-partial',action='store_true')
    a=p.parse_args()
    print(json.dumps(prepare(a.input,a.output,a.release_name,a.allow_partial),indent=2))
