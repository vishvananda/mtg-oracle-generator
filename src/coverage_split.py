"""Repartition a verified release into training and a small, locked final panel.

Whole rules families remain disjoint. Unselected descriptions of held-out
families are archived, never returned to training. No model scores are used.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import shutil

from data_utils import digest
from hub_dataset import verify

POLICY = 'coverage-protected-two-way-v2'
CARD_TYPES = {'artifact','battle','conspiracy','creature','dungeon','enchantment','instant','kindred',
              'land','phenomenon','plane','planeswalker','scheme','sorcery','vanguard'}


def features(card):
    result = {'keyword:'+s.casefold() for s in card.get('keywords', [])}
    faces = card.get('card_faces') or [card]
    result.add('layout:'+card.get('layout', 'normal'))
    for face in faces:
        text = face.get('oracle_text', '')
        types = face.get('type_line', '').split('—')[0].casefold().split()
        result.update('type:'+s for s in types if s in CARD_TYPES)
        # Only recognized ability words: arbitrary headings include unique
        # dungeon-room names/flavor labels, not distinct Magic mechanics.
        result.update('ability_word:'+s.casefold() for s in re.findall(r'(?m)^([A-Za-z][A-Za-z ,\'-]{1,45}) — ', text)
                      if 'keyword:'+s.casefold() in result)
        for label, pattern in {
            'loyalty_positive':r'(?m)^\+\d+:', 'loyalty_negative':r'(?m)^[−-]\d+:',
            'loyalty_zero':r'(?m)^0:', 'loyalty_variable':r'(?m)^[+−-]?X:',
            'rummage':r'discard.*then draw', 'loot':r'draw.*then discard',
            'opponent_library':r"opponent(?:'s|s’|s')? librar", 'modal':r'•',
            'replacement':r'\binstead\b', 'alternate_win':r'\bwin the game\b',
            'alternate_loss':r'\blose the game\b', 'extra_turn':r'\bextra turn',
            'copy':r'\bcopy\b', 'mana':r'\bAdd \{', 'token':r'\btoken\b',
            'counter':r'\bcounter\b', 'exile':r'\bexile\b',
        }.items():
            if re.search(pattern, text, re.I): result.add('pattern:'+label)
    colors=set(c for face in faces for c in face.get('colors',card.get('colors',[])))
    result.update('color:'+c for c in colors)
    result.add('color_bucket:'+('colorless' if not colors else 'multicolor' if len(colors)>1 else next(iter(colors))))
    result.add('color_combination:'+(''.join(c for c in 'WUBRG' if c in colors) or 'C'))
    return result


def choose_groups(groups, count=400, seed='oracle-two-way-20261008'):
    """Keep all <=10-family features and >=90% of every other feature in train."""
    totals = Counter(f for g in groups.values() for f in g['features'])
    # Type/layout/color strata need representatives on both sides where possible;
    # rare ability features retain the stronger minimum.
    minimum = {f:(1 if f.startswith(('type:','layout:','color')) else max(min(n, 10), math.ceil(.9*n)))
               for f,n in totals.items()}
    remaining = totals.copy()
    ordered = sorted(groups, key=lambda g:digest(seed+g))
    selected = []
    required=set(f for f in totals if f.startswith(('type:','color:','color_bucket:')))
    represented=set()
    def add(g, feature=None):
        if g in selected or len(selected)>=count or len(groups[g]['cards'])>4: return False
        fs = groups[g]['features']
        if any(remaining[f]-1 < minimum[f] for f in fs): return False
        choices=groups[g]['card_features']
        eligible=[c for c in choices if feature is None or feature in choices[c]]
        if not eligible:return False
        card=min(eligible,key=lambda c:(-len((required-represented)&choices[c]),digest(seed+c)))
        groups[g]['representative_card']=card;represented.update(choices[card])
        selected.append(g); remaining.subtract(fs); return True
    for feature in sorted(required,key=lambda f:(totals[f],f)):
        if feature in represented: continue
        if not any(add(g,feature) for g in ordered if feature in groups[g]['features']):
            raise ValueError('Cannot cover holdout stratum without removing protected training abilities: '+feature)
    # Include exact color combinations too whenever the ability constraints allow.
    for feature in sorted(f for f in totals if f.startswith('color_combination:')):
        if feature not in represented:
            any(add(g,feature) for g in ordered if feature in groups[g]['features'])
    # Explicit challenge strata; report this as a mixed test panel, not a
    # prevalence-weighted estimate of the entire Oracle universe.
    for feature, quota in [('type:planeswalker', count//16)]:
        for g in ordered:
            if sum(feature in groups[x]['features'] for x in selected)>=quota: break
            if feature in groups[g]['features']: add(g,feature)
    for g in ordered:
        if len(selected)==count: break
        add(g)
    if len(selected)!=count: raise ValueError('Insufficient families satisfying training coverage constraints')
    return set(selected), {f:{'total_families':totals[f], 'training_families':remaining[f],
        'heldout_families':totals[f]-remaining[f], 'minimum_training_families':minimum[f]} for f in sorted(totals)}


def rows(root):
    for split in ('train','validation','test'):
        path=root/(split+'.jsonl')
        if path.exists():
            with path.open() as stream:
                for line in stream: yield json.loads(line)


def rebuild(source, oracle, output, count=400, seed='oracle-two-way-20261008'):
    original=verify(source)
    if output.exists(): raise ValueError('Choose a new immutable output directory')
    cards={}; groups=defaultdict(lambda:{'cards':set(), 'features':set(), 'card_features':{}, 'examples':[]})
    old_features=defaultdict(set)
    for r in rows(source):
        card=r['source']['oracle_id']; group=r['source_group_id']
        if card in cards and cards[card]!=group: raise ValueError('Card spans source families')
        cards[card]=group;groups[group]['cards'].add(card)
        groups[group]['examples'].append((r['example_id'],r['style'],card))
        old_features[group].add(r['split'])
    raw_cards={}
    with gzip.open(oracle,'rt') as stream:
        for line in stream:
            card=json.loads(line); oid=card.get('oracle_id')
            if oid in cards:
                raw_cards[oid]=card
                groups[cards[oid]]['features'].update(features(card))
                groups[cards[oid]]['card_features'][oid]=features(card)
    if set(cards)-set(raw_cards): raise ValueError('Missing source cards in pinned Oracle snapshot')
    selected, coverage=choose_groups(groups,count,seed)
    old_missing=[]
    for feature in coverage:
        if not any(feature in g['features'] and 'train' in old_features[k] for k,g in groups.items()):
            old_missing.append(feature)
    # One independently weighted case per rules family, balanced over available
    # detail styles. Selection is frozen before training or model evaluation.
    panel=set(); style_counts=Counter()
    for group in sorted(selected, key=lambda g:digest(seed+'panel'+g)):
        candidates=[r for r in groups[group]['examples'] if r[2]==groups[group]['representative_card']]
        example,style,_=min(candidates,key=lambda r:(style_counts[r[1]],digest(seed+r[0])))
        panel.add(example);style_counts[style]+=1
    output.mkdir(parents=True)
    prompt=(source/'system-prompt.txt').read_text()
    names=[s+x for s in ('train','test') for x in ('.jsonl','.messages.jsonl','.sft.jsonl')]+['heldout-reserve.jsonl']
    handles={n:(output/n).open('w') for n in names}
    counts=Counter(); styles=Counter(); ids=set(); descriptions={}; owners={}; training_cards=set()
    def write(name,row): handles[name].write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
    try:
        for row in rows(source):
            group=row['source_group_id']; eid=row['example_id']
            split='test' if group in selected else 'train'
            if eid in ids: raise ValueError('Duplicate example ID')
            ids.add(eid)
            key=digest(' '.join(re.sub(r'[^\w{}/*+-]+',' ',row['description'].casefold()).split()))
            if key in descriptions and descriptions[key]!=split: raise ValueError('Description crosses splits')
            descriptions[key]=split
            card=row['source']['oracle_id']
            if owners.setdefault(card,split)!=split: raise ValueError('Card crosses splits')
            row={**row,'previous_split':row['split'],'split':split,'split_policy':POLICY}
            if split=='test' and eid not in panel:
                row['evaluation_role']='reserved_not_scored';write('heldout-reserve.jsonl',row);counts['reserved']+=1
                continue
            if split=='train': training_cards.add(card)
            counts[split]+=1;styles[row['style']]+=1
            write(split+'.jsonl',row)
            context=[{'role':'system','content':prompt},{'role':'user','content':row['description']}]
            answer=[{'role':'assistant','content':json.dumps(row['target'],ensure_ascii=False,separators=(',',':'))}]
            write(split+'.messages.jsonl',{'example_id':eid,'source_group_id':group,'style':row['style'],'messages':context+answer})
            write(split+'.sft.jsonl',{'prompt':context,'completion':answer})
    finally:
        for h in handles.values(): h.close()
    for name in ('system-prompt.txt','draft-schema.json','DATA_LICENSE.md'): shutil.copyfile(source/name,output/name)
    (output/'split-map.json').write_text(json.dumps(owners,sort_keys=True,indent=2)+'\n')
    policy={'name':POLICY,'seed':seed,'test_cases':count,'test_families':len(selected),
        'test_source_cards':sum(len(groups[g]['cards']) for g in selected), 'training_source_cards':len(training_cards),
        'test_panel_styles':dict(style_counts),'monitoring':'training diagnostics only; no held-out validation',
        'test_panel_features':dict(Counter(f for g in selected for f in groups[g]['card_features'][groups[g]['representative_card']])),
        'selection':'All card types/colors; exact color combinations when feasible; 25/400 planeswalker family quota when coverage permits; remaining families SHA-256 ranked; styles balanced; holdout families limited to 4 cards to avoid removing huge generic families',
        'unit':'whole normalized rules family; all variants and detail levels excluded from train together',
        'checkpoint_selection':'fixed final epoch; no test-based checkpoint selection',
        'protected_features':'Scryfall keywords, ability words, card types/layouts and documented text patterns; not exhaustive semantic coverage',
        'required_holdout_coverage':'Every recognized card type, W/U/B/R/G, colorless, multicolor; exact color combinations included where ability coverage permits',
        'coverage':coverage,'previous_split_features_absent_from_training':old_missing,
        'heldout_reserve_examples':counts['reserved'],'target_or_description_changes':0}
    (output/'split-policy.json').write_text(json.dumps(policy,indent=2)+'\n')
    files={}
    for p in sorted(output.iterdir()):
        with p.open('rb') as f: sha=hashlib.file_digest(f,'sha256').hexdigest()
        files[p.name]={'sha256':sha}
    manifest={**original,'format_version':2,'release':'oracle-full-v2-small-holdout',
        'counts':{'train':counts['train'],'validation':0,'test':counts['test']},
        'checks':{'group_overlap':0,'card_overlap':0,'description_overlap':0,'examples':sum(counts.values()),
            'source_cards':len(cards),'source_groups':len(groups),'features_missing_from_training':0},
        'styles':dict(styles),'files':files,'split_policy':POLICY,'test_evaluated':False,
        'inputs':[{'release':original['release'],'manifest_sha256':digest((source/'manifest.json').read_bytes())}],
        'oracle_snapshot_sha256':digest(oracle.read_bytes()),'reserved_examples':counts['reserved']}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    configs=''
    for kind,suffix in [('messages','.messages.jsonl'),('sft','.sft.jsonl'),('records','.jsonl')]:
        configs+=f'  - config_name: {kind}\n'+('    default: true\n' if kind=='messages' else '')+'    data_files:\n'
        for split in ('train','test'): configs+=f'      - split: {split}\n        path: {split}{suffix}\n'
    (output/'README.md').write_text('---\nlanguage: [en]\nlicense: other\nlicense_name: mixed-rights-card-data\nlicense_link: DATA_LICENSE.md\ntask_categories: [text-generation]\nconfigs:\n'+configs+'---\n\n'
        '# MTG Oracle descriptions — small holdout v2\n\n'
        f'Training: **{counts["train"]:,} descriptions / {len(training_cards):,} cards**. Final evaluation: **{count} descriptions from {count} disjoint rules families**. '
        f'All {counts["reserved"]:,} other descriptions of held-out families are preserved in `heldout-reserve.jsonl`, **excluded from training and headline evaluation**. No validation split.\n\n'
        'Same reviewed descriptions, editable JSON/Oracle targets, names, rarity, loyalty and provenance as v1; only assignment changed. '
        'Choose `messages`, `sft` (prompt/completion), or `records` (provenance). Names and unspecified characteristics can be editable suggestions, not unique correct answers.\n\n'
        'The final panel is frozen before training. Whole related rules families, card identities and normalized descriptions are disjoint. '
        'Every ability feature with <=10 rules families stays entirely in training; other ability features retain at least 90% and at least 10 families. '
        'Every recognized card type and each color, colorless and multicolor has held-out representation; type/layout/color strata also retain training representation. '
        'Features cover Scryfall keywords, ability words, card types/layouts and listed text patterns, not every possible semantic ability. '
        'The panel balances detail styles and reserves up to 25 planeswalker families. It is a mixed challenge panel, not a prevalence-weighted estimate of all cards. '
        'See `split-policy.json` for exact counts and coverage.\n\n'
        'Train from the pinned base, not a v1 adapter: a v1 adapter may have seen these test cards. Use a fixed recipe/epoch; evaluate once at the end. '
        'Training-sample loss diagnostics are not validation accuracy. If test results influence future model choices, call this a development set and reserve a fresh final holdout for those experiments. '
        '400 independent families provide useful aggregate precision, not precise estimates for rare individual mechanics.\n\n'
        'Automatically generated and independently reviewed; not human-certified. Historical wording follows recorded errata/obsolete-rule exclusions. '
        'No artwork. No final-test generation accuracy claimed yet. See [data rights and attribution](DATA_LICENSE.md).\n')
    verify(output)
    return {'counts':manifest['counts'], 'reserved':counts['reserved'], 'training_cards':len(training_cards),
        'heldout_cards':policy['test_source_cards'],'features':len(coverage),'previously_missing_features':old_missing}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','oracle','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--count',type=int,default=400)
    a=p.parse_args();print(json.dumps(rebuild(a.source,a.oracle,a.output,a.count),indent=2))
