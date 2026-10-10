"""Current Oracle and printed-wording inputs, grouped with the original source.

Identity-linked printed wordings are included by default. Exclude documented
functional changes, obsolete rules, or a directly demonstrated mechanical delta.
Run with ijson==3.3.0 and jsonschema==4.17.3 (streaming AllPrintings reader).
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import lzma
from pathlib import Path
import re

from data_utils import digest, write_jsonl
from full_corpus import ROOT, rules
from paths import ARTIFACTS, PROJECT
from export_full_corpus import select_unique
from data_utils import without_reminder

VERSION='original-wordings-v4'
POLICY_PATH=PROJECT/'configs/historical-wording-exclusions.json'
POLICY_BYTES=POLICY_PATH.read_bytes()
POLICY=json.loads(POLICY_BYTES)


def signature(text, typeline):
    # These are admission checks, not changes to the preserved printed input.
    text=without_reminder(text).replace('−','-').replace('’',"'").replace('“','"').replace('”','"').casefold()
    self_types=['creature','artifact','enchantment','land','planeswalker','battle']
    for typ in self_types:
        if typ in typeline.casefold(): text=text.replace('this '+typ,'cardname')
    if 'aura' in typeline.casefold(): text=text.replace('this aura','cardname')
    changes=[('comes into play','enters'),('come into play','enter'),
             ('enters the battlefield','enters'),('enter the battlefield','enter'),
             ('leaves play','leaves the battlefield'),
             ('his or her','their'),('he or she','they'),
             ('to your mana pool','')]
    for old,new in changes: text=text.replace(old,new)
    # Sentence/quote/cost punctuation can change scope, so retain it too.
    text='\n'.join(' '.join(re.sub(r'''[^\w{}/*+\-.,:;!?"'\s]''', ' ',line).split()) for line in text.splitlines() if line.strip())
    return re.sub(r' +([.,:;!?])',r'\1',text)


def functional_exclusions(name, printed, current, typelines):
    old='\n'.join(printed).replace('’',"'"); new='\n'.join(current).replace('’',"'")
    excluded=[]
    for rule in POLICY['rules']:
        if rule.get('names') and name not in rule['names']: continue
        if not re.search(rule['printed_regex'],old,re.I): continue
        if rule.get('current_regex') and not re.search(rule['current_regex'],new,re.I): continue
        if rule.get('current_absent_regex') and re.search(rule['current_absent_regex'],new,re.I): continue
        excluded.append({k:rule[k] for k in ('id','category','reason','source')})
    # Unlike broad text distance, these require matching surrounding text and a
    # specific changed mechanic. Historical vocabulary by itself never excludes.
    for index,(before,after,typ) in enumerate(zip(printed,current,typelines)):
        left,right=signature(before,typ),signature(after,typ)
        if left==right: continue
        equivalent_choice=bool(re.search(r'\b(?:tap or untap|untap or tap)\b',left)) or bool(re.search(r'\bplay up to\b.*\b(?:additional )?lands?\b',left))
        if not equivalent_choice and (re.sub(r'\b(?:you )?may ', '', left)==right or re.sub(r'\b(?:you )?may ', '', right)==left):
            excluded.append({'id':'changed_optionality','category':'functional_difference',
                             'reason':'The normalized source pair differs only by optional versus mandatory execution.',
                             'evidence':{'face':index,'printed':before,'current':after}})
        numeric_left=re.sub(r'\d+','#',left); numeric_right=re.sub(r'\d+','#',right)
        if numeric_left==numeric_right and re.findall(r'\d+',left)!=re.findall(r'\d+',right):
            excluded.append({'id':'changed_explicit_number','category':'functional_difference',
                             'reason':'The normalized source pair differs only in explicit numeric values.',
                             'evidence':{'face':index,'printed':before,'current':after}})
        for clause in [r' X can\x27t be 0\.',r' Activate only once each turn\.',r' Activate only as a sorcery\.']:
            if re.sub(clause,'',left,flags=re.I)==right or re.sub(clause,'',right,flags=re.I)==left:
                excluded.append({'id':'changed_explicit_restriction','category':'functional_difference',
                                 'reason':'A standalone gameplay restriction was added or removed.',
                                 'evidence':{'face':index,'printed':before,'current':after}})
                break
    return excluded


def normalize_face(text, source, index, keep_reminder=False):
    if source['source_faces']:
        names=[f['name'] for f in source['source_faces']]
        return rules(text,names[index],[(name,'Draft Face '+str(i+1)) for i,name in enumerate(names) if i!=index],keep_reminder=keep_reminder)
    return rules(text,source['name'],keep_reminder=keep_reminder)


def description(target, texts):
    faces=target.get('card_faces',[target])
    lines=[]
    if 'layout' in target: lines.append('Layout: '+target['layout'])
    for i,(face,text) in enumerate(zip(faces,texts)):
        if len(faces)>1: lines.append('Face '+str(i+1)+':')
        for key,label in [('type_line','Type'),('mana_cost','Mana cost'),('colors','Colors'),
                          ('power','Power'),('toughness','Toughness'),('loyalty','Starting loyalty'),
                          ('defense','Defense'),('hand_modifier','Hand modifier'),('life_modifier','Life modifier')]:
            if key in face:
                value=face[key]
                lines.append(label+': '+(', '.join(value) or 'colorless' if isinstance(value,list) else value or '(none)'))
        lines.append('Rules text:\n'+(text or '(no abilities)'))
    return '\n'.join(lines)


def pair(source, texts, kind, provenance):
    projection=next(p for p in source['projections'] if p['style']=='complete')
    target=projection['draft_target']
    return {'example_id':digest([VERSION,source['oracle_id'],kind,texts])[:24],
            'split':source['split'],'source_group_id':source['source_group_id'],
            'source':{k:source[k] for k in ('oracle_id','source_sha256','source_updated_at','layout')},
            'style':kind,'task':'propose_card_draft','description':description(target,texts),'target':target,
            'specified_fields':projection['specified_fields'],'proposed_fields':[],
            'rules_coverage':projection['rules_coverage'],'provenance':provenance,
            'human_reviewed':False,'engine_validation':'not_run; support is not an eligibility condition'}


def assemble_faces(source, entries):
    if not source['source_faces']:
        return [entries[0]] if len(entries)==1 else None
    lookup={c.get('faceName',c['name']):c for c in entries}
    names=[f['name'] for f in source['source_faces']]
    return [lookup[name] for name in names] if set(lookup)==set(names) and len(entries)==len(names) else None


def build(root, printing_manifest, output):
    import ijson
    from jsonschema import Draft202012Validator
    if output.exists(): raise ValueError('Use a new immutable augmentation directory')
    printing_source=json.loads(printing_manifest.read_text())
    with Path(printing_source['source_file']).open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=printing_source['sha256']: raise ValueError('Printings hash mismatch')
    sources={r['oracle_id']:r for line in (root/'sources.jsonl').open() if (r:=json.loads(line))}
    accepted=[]; candidates={}; excluded_rows=[]; metrics=Counter(); seen={}
    for source in sources.values():
        target=next(p['draft_target'] for p in source['projections'] if p['style']=='complete')
        texts=[f['oracle_text'] for f in target.get('card_faces',[target])]
        row=pair(source,texts,'current_oracle',{'source':'Scryfall Oracle snapshot','admission':'identity with normalized self-reference'})
        accepted.append(row); seen[(source['oracle_id'],tuple(texts))]=row
    with lzma.open(printing_source['source_file'],'rb') as stream:
        for set_code,card_set in ijson.kvitems(stream,'data'):
            groups=defaultdict(list)
            for card in card_set.get('cards',[]):
                metrics['printing_faces_scanned']+=1
                if card.get('language')!='English': metrics['non_english_faces']+=1; continue
                oid=card.get('identifiers',{}).get('scryfallOracleId')
                if oid not in sources: metrics['faces_without_projected_oracle_id']+=1; continue
                identity=card.get('identifiers',{}).get('scryfallId') or card['uuid']
                groups[(oid,identity)].append(card)
            for (oid,identity),entries in groups.items():
                source=sources[oid]; faces=assemble_faces(source,entries)
                if faces is None: metrics['incomplete_or_ambiguous_face_groups']+=1; continue
                target=next(p['draft_target'] for p in source['projections'] if p['style']=='complete')
                targets=target.get('card_faces',[target])
                if any('originalText' not in c and f['oracle_text'] for c,f in zip(faces,targets)):
                    metrics['printings_without_original_text']+=1; continue
                try:
                    originals=[normalize_face(c.get('originalText',''),source,i,keep_reminder=True) for i,c in enumerate(faces)]
                    current=[normalize_face(c.get('text',''),source,i) for i,c in enumerate(faces)]
                except ValueError: metrics['unbalanced_reminder_text']+=1; continue
                key=(oid,tuple(originals))
                provenance={'set_code':set_code,'release_date':card_set.get('releaseDate'),
                            'printing_id':identity,'mtgjson_uuids':[c['uuid'] for c in faces],
                            'original_types':[c.get('originalType') for c in faces],
                            'original_texts':[c.get('originalText','') for c in faces]}
                if key in seen:
                    metrics['duplicate_wording_printings']+=1
                    row=seen[key]
                    row.setdefault('additional_printings',[]).append(provenance)
                    continue
                row=pair(source,originals,'printed_wording',{'source':'MTGJSON AllPrintings','printings':[provenance]})
                seen[key]=row
                malformed=False
                try:
                    current_agrees=all(signature(t,f['type_line'])==signature(f['oracle_text'],f['type_line']) for t,f in zip(current,targets))
                    wording_only=all(signature(t,f['type_line'])==signature(f['oracle_text'],f['type_line']) for t,f in zip(originals,targets))
                    exclusions=functional_exclusions(source['name'],originals,[f['oracle_text'] for f in targets],[f['type_line'] for f in targets])
                except ValueError:
                    current_agrees=wording_only=False; malformed=True; exclusions=[]
                row['source_text_diagnostics']={'current_provider_text_matches':current_agrees,'simple_terminology_match':wording_only}
                if malformed:
                    row['review_reasons']=['unbalanced_printed_reminder_text']
                    candidates[key]=row
                elif exclusions:
                    row['exclusions']=exclusions
                    excluded_rows.append(row)
                else:
                    row['provenance']['admission']='Oracle-ID-linked historical wording; no documented or directly detected functional-change exception matched'
                    accepted.append(row)
                metrics['distinct_printed_wordings']+=1
    validator=Draft202012Validator(json.loads((root/'draft-schema.json').read_text()))
    for row in accepted: validator.validate(row['target'])
    rows,duplicates=select_unique(accepted)
    output.mkdir(parents=True)
    files={}
    for split in ('train','validation','test'):
        selected=[r for r in rows if r['split']==split]
        files[split+'.jsonl']=write_jsonl(output/(split+'.jsonl'),selected)
        prompt=(root/'student-prompt.txt').read_text()
        files[split+'.sft.jsonl']=write_jsonl(output/(split+'.sft.jsonl'),[
            {'example_id':r['example_id'],'source_group_id':r['source_group_id'],'messages':[
                {'role':'system','content':prompt},{'role':'user','content':r['description']},
                {'role':'assistant','content':json.dumps(r['target'],ensure_ascii=False,separators=(',',':'))}]} for r in selected])
    files['needs-review.jsonl']=write_jsonl(output/'needs-review.jsonl',list(candidates.values()))
    files['excluded-functional.jsonl']=write_jsonl(output/'excluded-functional.jsonl',excluded_rows)
    files['duplicates.jsonl']=write_jsonl(output/'duplicates.jsonl',duplicates)
    (output/'exclusion-policy.json').write_bytes(POLICY_BYTES)
    files['exclusion-policy.json']={'sha256':digest(POLICY_BYTES)}
    result={'version':VERSION,'base_corpus':str(root),'source_manifest_sha256':digest((root/'manifest.json').read_bytes()),
            'printings_snapshot':printing_source,'counts':dict(Counter(r['split'] for r in rows)),
            'styles':dict(Counter(r['style'] for r in rows)),'accepted_examples':len(rows),
            'needs_review':len(candidates),'duplicate_inputs_removed':len(duplicates),'scan':dict(metrics),
            'functional_exclusions':len(excluded_rows),'exclusion_reasons':dict(Counter(e['id'] for r in excluded_rows for e in r['exclusions'])),
            'split_policy':'Inherited from full Oracle source groups; normalized description collisions prefer held-out splits.',
            'admission_policy':'Include identity-linked original wordings by default. Exclude documented functional changes, obsolete rules, or directly demonstrated functional deltas; textual differences alone do not exclude.',
            'exclusion_coverage':POLICY['coverage'],
            'scope':'Printed wordings, not an archive of every historical Oracle revision. English only. Original text is retained in provenance.',
            'files':files,'human_reviewed':False,'automatic_training':False,'public_uploads':False}
    (output/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='files'},indent=2))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--printings-manifest',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); build(a.root,a.printings_manifest,a.output)
