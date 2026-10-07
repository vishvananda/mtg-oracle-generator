"""Export an immutable, audited snapshot of accepted full-Oracle examples."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator

from data_utils import digest, write_jsonl
from corpus_workers import deterministic_checks
from full_corpus import ROOT
from paths import PROJECT
from default_coherence import coherent_record
from training_names import add_training_names, NAME_INSTRUCTIONS
from training_rarity import add_training_rarity, RARITY_INSTRUCTIONS


def description_key(text):
    return ' '.join(re.sub(r'[^\w{}/*+-]+', ' ', text.casefold()).split())


def select_unique(rows):
    # Holdouts win collisions. Never move a held-out source into training.
    priority={'test':0,'validation':1,'train':2}
    chosen=[]; duplicates=[]; seen=set(); groups={}; ids=set()
    for row in sorted(rows,key=lambda r:(priority[r['split']],r['example_id'])):
        if row['example_id'] in ids: raise ValueError('Duplicate example ID')
        ids.add(row['example_id'])
        group=row['source_group_id']
        if groups.setdefault(group,row['split'])!=row['split']:
            raise ValueError('Source group crosses splits')
        key=description_key(row['description'])
        if key in seen:
            duplicates.append({'example_id':row['example_id'],'split':row['split'],'reason':'duplicate_normalized_description'})
        else:
            seen.add(key); chosen.append(row)
    return chosen,duplicates


def export(root, output):
    if output.exists(): raise ValueError('Export snapshots are immutable; choose a new output directory')
    manifest=json.loads((root/'manifest.json').read_text())
    for name,metadata in manifest['files'].items():
        expected=metadata['sha256'] if isinstance(metadata,dict) else metadata
        if digest((root/name).read_bytes())!=expected: raise ValueError('Source package hash mismatch: '+name)
    schema=json.loads((root/'draft-schema.json').read_text())
    validator=Draft202012Validator(schema)
    prompt=(root/'student-prompt.txt').read_text().rstrip()+NAME_INSTRUCTIONS+RARITY_INSTRUCTIONS+'\n'
    admitted=[]; quarantined=[]; states=Counter()
    for path in sorted((root/'queue').glob('*.json')):
        job=json.loads(path.read_text()); states[job['state']]+=1
        if job['state']!='completed': continue
        if job.get('fidelity_controls_rejected')!=3: continue
        result_path=root/'runs'/job['id']/'results.jsonl'
        for line in result_path.read_text().splitlines():
            row=json.loads(line)
            issues=list(row['deterministic_issues'])
            issues+=deterministic_checks({'style':row['style'],'target':row['expressed_constraints']},row['description'])
            review=row['fidelity_review']
            if review['verdict']!='pass' or review['issues']: issues.append('fidelity_review_not_passed')
            if row.get('fidelity_controls_rejected')!=3: issues.append('missing_fidelity_controls')
            if row.get('description_policy')=='plausible_completion' and row.get('completion_controls_checked')!=4:
                issues.append('missing_completion_controls')
            issues += ['schema:'+e.message for e in validator.iter_errors(row['target'])]
            if row['rules_coverage']=='unspecified' and row['target'].get('oracle_text'):
                issues.append('unrequested_source_abilities')
            if issues:
                quarantined.append({**row,'export_issues':sorted(set(issues))})
            else: admitted.append(row)
    augmentation_reports=[]
    if (root/'augmentations.json').exists():
        for attachment in json.loads((root/'augmentations.json').read_text()):
            directory=Path(attachment['path'])
            if digest((directory/'manifest.json').read_bytes())!=attachment['manifest_sha256']:
                raise ValueError('Augmentation manifest changed')
            extra=json.loads((directory/'manifest.json').read_text())
            if extra['source_manifest_sha256']!=digest((root/'manifest.json').read_bytes()):
                raise ValueError('Augmentation belongs to another source corpus')
            for split in ('train','validation','test'):
                path=directory/(split+'.jsonl')
                if digest(path.read_bytes())!=extra['files'][path.name]['sha256']: raise ValueError('Augmentation examples changed')
                for line in path.open():
                    row=json.loads(line); validator.validate(row['target'])
                    if row['split']!=split: raise ValueError('Augmentation split mismatch')
                    admitted.append(row)
            augmentation_reports.append({'manifest_sha256':attachment['manifest_sha256'],'accepted_examples':extra['accepted_examples'],
                                         'version':extra['version'],'needs_review':extra['needs_review'],
                                         'functional_exclusions':extra.get('functional_exclusions',0),
                                         'admission_policy':extra.get('admission_policy'),
                                         'exclusion_coverage':extra.get('exclusion_coverage')})
    admitted=[coherent_record(row) for row in admitted]
    rows,duplicates=select_unique(admitted)
    rows,naming_duplicates=select_unique(add_training_rarity(add_training_names(rows,root),root))
    export_schema=json.loads((PROJECT/'schemas/design-draft-v1.json').read_text())
    export_schema['required']=sorted(set(export_schema['required']+['name','rarity']))
    for row in rows: Draft202012Validator(export_schema).validate(row['target'])
    duplicates.extend(naming_duplicates)
    output.mkdir(parents=True)
    files={}
    for split in ('train','validation','test'):
        selected=[r for r in rows if r['split']==split]
        files[split+'.jsonl']=write_jsonl(output/(split+'.jsonl'),selected)
        sft=[{'example_id':r['example_id'],'source_group_id':r['source_group_id'],
              'messages':[{'role':'system','content':prompt},{'role':'user','content':r['description']},
                          {'role':'assistant','content':json.dumps(r['target'],ensure_ascii=False,separators=(',',':'))}]} for r in selected]
        files[split+'.sft.jsonl']=write_jsonl(output/(split+'.sft.jsonl'),sft)
    files['quarantined.jsonl']=write_jsonl(output/'quarantined.jsonl',quarantined)
    files['duplicates.jsonl']=write_jsonl(output/'duplicates.jsonl',duplicates)
    for name in ('draft-schema.json','student-prompt.txt'):
        if name=='student-prompt.txt': (output/name).write_text(prompt)
        else: (output/name).write_text(json.dumps(export_schema,indent=2)+'\n')
        files[name]={'sha256':digest((output/name).read_bytes())}
    result={'version':manifest['version'],'task':'propose_card_draft','target':'Oracle text with editable card characteristics',
            'naming':{'version':'card-names-v1','named_targets':len(rows),
                      'explicit_name_examples':dict(Counter(r['split'] for r in rows if r['naming']['policy']=='specified'))},
            'rarity':{'policy':'pinned reference printing; explicit requests in a stable subset',
                      'counts':dict(Counter(r['target']['rarity'] for r in rows)),
                      'explicit_examples':sum(r['rarity_provenance']['policy']=='specified' for r in rows)},
            'source_manifest_sha256':digest((root/'manifest.json').read_bytes()),
            'initial_generation_config_sha256':digest((root/'generation-config.json').read_bytes()),
            'reviewer_counts':dict(Counter(r.get('reviewer','source_derived_no_model_review') for r in rows)),
            'augmentations':augmentation_reports,
            'states':dict(states),'generation_complete':states['completed']==sum(states.values()),
            'accepted_exported':len(rows),'quarantined':len(quarantined),'duplicates_removed':len(duplicates),
            'editable_default_adjustments':sum(bool(r.get('completion_adjustments')) for r in rows),
            'description_policies':dict(Counter(r.get('description_policy','exact_constraints') for r in rows)),
            'counts':dict(Counter(r['split'] for r in rows)),
            'fidelity_review':'Model paraphrases have a separate review pass, deterministic checks and three negative controls per batch. Source-derived wording augmentations use their recorded admission policy. Neither is human-reviewed ground truth.',
            'validation_policy':'Source-derived Oracle targets; engine support is not an eligibility condition. mtgish recognition cannot prove intent fidelity.',
            'group_overlap':0,'normalized_description_overlap':0,'files':files,'automatic_training':False,'public_uploads':False}
    (output/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='files'},indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); export(args.root,args.output)
