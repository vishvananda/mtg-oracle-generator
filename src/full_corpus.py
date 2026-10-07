#!/usr/bin/env python3
"""Project the complete Oracle snapshot into completion tasks before teacher calls.

All layouts remain inventoried. Mechanical cards are retained independently of
engine support; art and marketing fronts carry no gameplay draft. Each card has
five description detail levels and explicit supplied/proposed field metadata.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import re

from paths import ARTIFACTS, PROJECT
from data_utils import digest, write_jsonl
from data_utils import without_reminder
from default_coherence import sparse_defaults

VERSION='full-oracle-completion-v2'
ROOT=ARTIFACTS/'datasets'/VERSION
FIELDS=('mana_cost','type_line','colors','power','toughness','loyalty','defense','hand_modifier','life_modifier')


def rules(text, name, aliases=(), keep_reminder=False):
    text=(text or '') if keep_reminder else without_reminder(text or '')
    # Protect the whole literal named-card clause before replacing full/short
    # self names. A short self-reference also occurs after "on" and "by".
    protected=[]
    def protect(match):
        protected.append(match[0])
        return f'\x00{len(protected)-1}\x00'
    text=re.sub(r'\bnamed\s+[^.\n;]+',protect,text)
    replacements={name:'CARDNAME',**dict(aliases)}
    for old,new in list(replacements.items()):
        if ',' in old: replacements.setdefault(old.split(',')[0],new)
    pattern='|'.join(re.escape(k) for k in sorted(replacements,key=len,reverse=True))
    text=re.sub(r'(?<!\w)(?:'+pattern+r')(?!\w)',lambda m:replacements[m[0]],text)
    return re.sub(r'\x00(\d+)\x00',lambda m:protected[int(m[1])],text)


def face_draft(face, parent, index=None):
    result={k:face[k] for k in FIELDS if face.get(k) is not None}
    if 'colors' not in result:
        result['colors']=face.get('color_indicator') or sorted(set(re.findall(r'[WUBRG]',face.get('mana_cost',''))),key='WUBRG'.index)
        if re.search(r'^Devoid\b',face.get('oracle_text',''),re.M): result['colors']=[]
    result['colors']=sorted(result['colors'],key='WUBRG'.index)
    aliases=[(f['name'],'Draft Face '+str(i+1)) for i,f in enumerate(parent.get('card_faces',[])) if f['name']!=face['name']]
    result['oracle_text']=rules(face.get('oracle_text',''),face['name'],aliases)
    if index is not None: result['name']='Draft Face '+str(index+1)
    return result


def draft(card):
    if card.get('card_faces'):
        value={k:card[k] for k in FIELDS if card.get(k) is not None}
        value.update(layout=card['layout'],oracle_text='',card_faces=[face_draft(f,card,i) for i,f in enumerate(card['card_faces'])])
        return value
    result=face_draft(card,card)
    if card['layout']!='normal': result['layout']=card['layout']
    return result


def normalized_group(card):
    value=draft(card)
    texts=[f['oracle_text'] for f in value.get('card_faces',[value])]
    text='\nFACE\n'.join(texts)
    text=re.sub(r'\{[^}]+\}|\d+|\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\b','#',text)
    text=re.sub(r'\b(?:white|blue|black|red|green)\b','COLOR',text)
    return digest([card['layout'] if card.get('card_faces') else 'single',' '.join(text.lower().split())])


def project(card):
    full=draft(card)
    primary=full.get('card_faces',[full])[0]
    main=primary['type_line'].split(' — ')[0]
    broad=' '.join(w for w in main.split() if w not in ('Legendary','Snow','World','Basic'))
    typ=broad+(' — '+primary['type_line'].split(' — ',1)[1] if 'Creature' in broad and ' — ' in primary['type_line'] else '')
    minimal={'type_line':typ,'oracle_text':''}
    fixed_stats=all(re.fullmatch(r'-?\d+',str(primary.get(k,''))) for k in ('power','toughness'))
    if fixed_stats: minimal.update(power=primary['power'],toughness=primary['toughness'])
    concept={'type_line':typ,'colors':primary.get('colors',[]),'oracle_text':''}
    if str(primary.get('power','')).isdigit() and str(primary.get('toughness','')).isdigit():
        size=max(int(primary['power']),int(primary['toughness']))
        concept['design_notes']=['small' if size<=2 else 'midsize' if size<=4 else 'large']
    if 'card_faces' in full:
        mechanics={'layout':full['layout'],'type_line':full['type_line'],'oracle_text':'',
                   'card_faces':[{k:f[k] for k in ('name','type_line','oracle_text')} for f in full['card_faces']]}
    else:
        mechanics={'type_line':broad,'oracle_text':full['oracle_text']}
        if 'layout' in full: mechanics['layout']=full['layout']
    values=[]
    for style,expressed in [('minimal',minimal),('concept',concept),('mechanics',mechanics),('complete',full),('casual_complete',full)]:
        target=json.loads(json.dumps(full if style not in ('minimal','concept') else primary))
        if style in ('minimal','concept'):
            # An incomplete single-face concept does not request other source abilities/faces.
            target.pop('name',None); target.pop('layout',None)
            target.update(type_line=typ,oracle_text='')
            # A star stat depends on the omitted defining ability. For sparse
            # concepts propose an editable fixed body instead of an orphaned *.
            if 'power' in primary and not fixed_stats: target.update(power='3',toughness='3')
            if 'design_notes' in expressed: target['design_notes']=expressed['design_notes']
            target,_=sparse_defaults(target,expressed)
        def leaf_paths(value,prefix=''):
            return [p for key,val in value.items() for p in (
                [prefix+key+'.'+str(i)+'.'+part for i,face in enumerate(val) for part in leaf_paths(face)]
                if key=='card_faces' else [prefix+key])]
        expressed_paths=leaf_paths(expressed)
        values.append({'id':digest([VERSION,card['oracle_id'],style])[:24], 'style':style,
                       'target':expressed,'draft_target':target,
                       'rules_coverage':'unspecified' if style in ('minimal','concept') else
                                        'all' if any(f.get('oracle_text') for f in full.get('card_faces',[full])) else 'explicit_none',
                       'specified_fields':expressed_paths,'proposed_fields':[k for k in leaf_paths(target) if k not in expressed_paths],
                       'omitted_fields':[k for k in FIELDS if k not in expressed]})
    return values


def prepare(output=ROOT, split_map=None):
    if output.exists(): raise ValueError('Use a new immutable corpus directory')
    source=json.loads((ARTIFACTS/'full-oracle/source-manifest.json').read_text())
    with Path(source['source_file']).open('rb') as f:
        if hashlib.file_digest(f,'sha256').hexdigest()!=source['download_sha256']: raise ValueError('Source hash mismatch')
    # Published runs supply their frozen Oracle-ID split map. Fresh runs use
    # deterministic mechanic-family hashing and need no private editor corpus.
    old=json.loads(split_map.read_text()) if split_map else {}
    if any(s not in ('train','validation','test') for s in old.values()):
        raise ValueError('Invalid split map')
    cards=[]; excluded=[]; groups=defaultdict(set)
    with gzip.open(source['source_file'],'rt') as stream:
        for line in stream:
            card=json.loads(line)
            if card['layout'] in ('art_series','front_card'):
                excluded.append({'oracle_id':card['oracle_id'],'layout':card['layout'],'reason':'No mechanical game-card content'})
                continue
            try:
                group=normalized_group(card)
                projections=project(card)
            except (KeyError,TypeError,ValueError) as e:
                excluded.append({'oracle_id':card['oracle_id'],'layout':card['layout'],'reason':'Projection needs review: '+str(e)})
                continue
            if card['oracle_id'] in old: groups[group].add(old[card['oracle_id']])
            cards.append((card,group,projections))
    output.mkdir(parents=True)
    source_rows=[]
    for card,group,projections in cards:
        prior=groups[group]
        split=('test' if 'test' in prior else 'validation' if 'validation' in prior else 'train') if prior else (
            'test' if int(group[:8],16)%10==0 else 'validation' if int(group[:8],16)%10==1 else 'train')
        source_rows.append({'card_key':digest(card['oracle_id'])[:24],'oracle_id':card['oracle_id'],
            'name':card['name'],'source_group_id':group,'split':split,'layout':card['layout'],
            'source_sha256':digest(card),'source_updated_at':source['updated_at'],
            'engine_support':'unmeasured; not an eligibility condition',
            'source_oracle_text':card.get('oracle_text'),'source_faces':[{k:f[k] for k in ('name','oracle_text') if k in f} for f in card.get('card_faces',[])],
            'projections':projections})
    source_rows.sort(key=lambda x:(x['split'],digest(x['card_key'])))
    files={'sources.jsonl':write_jsonl(output/'sources.jsonl',source_rows),
           'excluded.jsonl':write_jsonl(output/'excluded.jsonl',excluded)}
    for target,source_path in [('student-prompt.txt',PROJECT/'prompts/design-draft-v2.txt'),
                              ('draft-schema.json',PROJECT/'schemas/design-draft-v1.json')]:
        (output/target).write_bytes(source_path.read_bytes())
        files[target]=digest((output/target).read_bytes())
    # Keep the complete raw snapshot by reference/hash, not images, prices or printing metadata in model input.
    report={'version':VERSION,'source':source,'eligible_cards':len(source_rows),
            'planned_descriptions':sum(len(r['projections']) for r in source_rows),
            'layouts':dict(Counter(r['layout'] for r in source_rows)),
            'cards_by_split':dict(Counter(r['split'] for r in source_rows)),
            'excluded':dict(Counter(r['reason'] for r in excluded)),
            'split_policy':'Group before teacher calls; all faces and projections inherit one split. Pinned identities remain held out; group conflicts prefer test then validation.',
            'split_map_sha256':digest(split_map.read_bytes()) if split_map else None,
            'completion_policy':'Full editable characteristics with only requested mechanics; alternate unspecified choices are allowed.',
            'task':'propose_card_draft','files':files,'generated_model_descriptions':0,
            'status':'sources_projected; teacher generation and fidelity audit pending',
            'human_reviewed':False,'public_uploads':False}
    (output/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('source','files')},indent=2))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--output',type=Path,default=ROOT)
    p.add_argument('--split-map',type=Path,help='Frozen oracle_id -> split mapping from a published release')
    a=p.parse_args(); prepare(a.output,a.split_map)
