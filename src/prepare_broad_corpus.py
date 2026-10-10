#!/usr/bin/env python3
"""Prepare general gameplay summaries using existing source groups and full targets."""
import argparse
import copy
import json
from pathlib import Path
import shutil

from data_utils import digest, write_jsonl
from corpus_workers import prepare, prompt_config, TEACHER
from full_corpus import ROOT
from paths import PROJECT

VERSION='broad-gameplay-v1'


def projections(source):
    reference=next(p['draft_target'] for p in source['projections'] if p['style']=='complete')
    return [{'id':digest([VERSION,source['oracle_id'],style])[:24],
             'style':style,'description_policy':'plausible_completion',
             'target':{},'reference_draft':copy.deepcopy(reference),'draft_target':copy.deepcopy(reference),
             'rules_coverage':'summary','specified_fields':[],'proposed_fields':[],
             'omitted_fields':[], 'field_annotation':'not_extracted'}
            for style in ('broad_plain','broad_shorthand')]


def prepare_broad(root,output,names=(),limit=None):
    if output.exists(): raise ValueError('Use a new immutable broad-description directory')
    parent=json.loads((root/'manifest.json').read_text())
    expected=parent['files']['sources.jsonl']
    expected=expected['sha256'] if isinstance(expected,dict) else expected
    if digest((root/'sources.jsonl').read_bytes())!=expected:
        raise ValueError('Source projections changed')
    cards=[]
    for line in (root/'sources.jsonl').open():
        source=json.loads(line)
        if names and source['name'] not in names: continue
        # Bounded canaries use training groups only; a full preparation retains all splits.
        if (names or limit is not None) and source['split']!='train': continue
        cards.append({**source,'projections':projections(source)})
        if limit is not None and len(cards)>=limit: break
    if not cards: raise ValueError('No matching training cards')
    if names and set(names)!={c['name'] for c in cards}:
        raise ValueError('Requested canary cards must exist in the training split')
    output.mkdir(parents=True)
    files={'sources.jsonl':write_jsonl(output/'sources.jsonl',cards)}
    for target,source in [('student-prompt.txt',PROJECT/'prompts/design-draft-v2.txt'),
                          ('draft-schema.json',root/'draft-schema.json')]:
        shutil.copyfile(source,output/target); files[target]=digest((output/target).read_bytes())
    manifest={'version':VERSION,'source_root':str(root),'source_manifest_sha256':digest((root/'manifest.json').read_bytes()),
              'eligible_cards':len(cards),'planned_descriptions':2*len(cards),'files':files,
              'description_policy':'Source is one plausible completion; omissions permitted, contradictions rejected.',
              'split_policy':'Inherit original source groups and splits unchanged.',
              'field_annotation':'Description claims are reviewed in natural language, not extracted into field labels.',
              'human_reviewed':False,'public_uploads':False}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    prepare(output)
    config_path=output/'generation-config.json'
    config=json.loads(config_path.read_text())
    config.update(prompt_config('plausible_completion'),teacher=TEACHER,reviewer=TEACHER,
                  revision=VERSION,completion_policy=manifest['description_policy'])
    config_path.write_text(json.dumps(config,indent=2)+'\n')
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--name',action='append',default=[])
    p.add_argument('--limit',type=int)
    a=p.parse_args()
    if a.limit is not None and a.limit<1: p.error('--limit must be positive')
    prepare_broad(a.root,a.output,a.name,a.limit)
