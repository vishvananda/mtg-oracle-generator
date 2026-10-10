"""Freeze reviewed custom examples, train-only replay, and paired evaluation inputs."""
import argparse
from collections import Counter
import json
from pathlib import Path

from custom_training_data import STYLES, VERSION, read, save, coverage, target
from data_utils import digest, write_jsonl, without_reminder
from filter_custom_candidates import canonical_parser_key
from rl_parser_cache import ParserCache


def canonical_target(reviewed, source_parse, parser):
    canonical = {**reviewed, 'oracle_text':without_reminder(reviewed['oracle_text'])}
    policy = 'removed' if canonical['oracle_text']!=reviewed['oracle_text'] else 'absent'
    parsed = parser.check(canonical)
    # Upstream requires reminder syntax for some keywords (e.g. Cycling on
    # spells). Retain reviewed source text when removing it loses support.
    if parsed.get('status')=='rejected' and policy=='removed':
        canonical = reviewed
        policy = 'retained_for_parser'
        parsed = parser.check(canonical)
    if parsed.get('parse_complete') is not True:
        raise ValueError('Normalized training target no longer parses: '+reviewed['name'])
    if canonical_parser_key(parsed['mtgish'])!=canonical_parser_key(source_parse['mtgish']):
        raise ValueError('Target normalization changed the parsed mechanics: '+reviewed['name'])
    return canonical, parsed, policy


def export(root, output, development, reserved, serving_system, minimum_train=384):
    if output.exists(): raise ValueError('Use a fresh export directory')
    preparation = json.loads((root/'preparation.json').read_text())
    jobs = [json.loads(p.read_text()) for p in sorted((root/'queue').glob('*.json'))]
    if any(j['state']!='reviewed' for j in jobs): raise ValueError('Corpus generation/review is incomplete')
    for name, pin in preparation['files'].items():
        if digest((root/name).read_bytes())!=pin['sha256']:raise ValueError('Corpus input changed: '+name)
    dev = read(development)
    reserved_ids = set(json.loads(reserved.read_text())['source_group_ids'])
    reserved_ids |= {r['source_group_id'] for r in dev}
    sources = {r['id']:r for r in read(root/'selected.jsonl')}
    accepted = []; excluded = []; references = []; seen_ids = set()
    with ParserCache(output.parent/'reference-parser-cache') as parser:
        for job in jobs:
            receipts = {}
            for role in ('teacher','reviewer'):
                directory = root/'runs'/job['id']/role
                receipt = json.loads((directory/'receipt.json').read_text())
                if receipt.get('error') or digest((directory/'output.json').read_bytes())!=receipt['output_sha256']:
                    raise ValueError('Generation/review receipt changed')
                receipts[role] = receipt
            for row in job['results']:
                key = row['source_id']; source = sources[key]
                if key in seen_ids: raise ValueError('Duplicate source result')
                seen_ids.add(key)
                if row['family_id']!=source['family_id'] or row['split']!=source['split']:
                    raise ValueError('Source identity changed')
                if row['target']!=target(source['draft']):raise ValueError('Source-derived target changed')
                if any(row[role+'_output_sha256']!=receipts[role]['output_sha256'] for role in receipts):
                    raise ValueError('Result is not bound to its model receipts')
                if row['family_id'] in reserved_ids:
                    excluded.append({'id':key,'reason':'old_development_family'});continue
                if row['review']['target']['verdict']!='pass':
                    excluded.append({'id':key,'reason':'target_review','review':row['review']['target']});continue
                # Keyword reminder wording can predate rules updates. Supervise
                # the keyword/rules, not historic parenthetical explanations,
                # matching the original Oracle corpus export policy.
                # Revalidate the actual CARDNAME target used for supervision.
                canonical, parsed, reminder_policy = canonical_target(row['target'],source['validation'],parser)
                references.append({'id':key,'validation':parsed,'reminder_policy':reminder_policy})
                for style in STYLES:
                    if row['review'][style]['verdict']!='pass':
                        excluded.append({'id':key,'style':style,'reason':'description_review','review':row['review'][style]});continue
                    accepted.append({'id':'custom-'+key[:20]+'-'+style,
                        'source_id':key,'source_group_id':row['family_id'],'split':row['split'],
                        'style':style,'request':row['descriptions'][style], 'target':canonical,
                        'explicit_metadata':{k:v for k,v in canonical.items() if k not in ('oracle_text','layout')}
                            if style=='detailed' else {},
                        'evaluation_origin':'unseen_custom_family', 'review':row['review'][style]})
                if row['split']=='train':
                    # Preserve old prose as a direct translation input as requested.
                    # Only pre-audited conservative source normalization is used.
                    original = source['original_draft']
                    metadata = {k:v for k,v in canonical.items() if k!='oracle_text'}
                    request = ('Turn this card into current Oracle wording. Preserve the design.\n'+
                               json.dumps(metadata,ensure_ascii=False)+'\n'+original['oracle_text'])
                    accepted.append({'id':'custom-'+key[:20]+'-original','source_id':key,
                        'source_group_id':row['family_id'],'split':'train','style':'original_wording',
                        'request':request,'target':canonical,'normalization_changes':source['normalization_changes'],
                        'reminder_text_removed_from_target':canonical['oracle_text']!=row['target']['oracle_text']})
        provenance = parser.provenance
    if seen_ids!=set(sources): raise ValueError('Missing source reviews')
    train = [r for r in accepted if r['split']=='train']
    test = [r for r in accepted if r['split']=='test']
    train_groups = {r['source_group_id'] for r in train}; test_groups = {r['source_group_id'] for r in test}
    if train_groups & test_groups: raise ValueError('Custom split leak')
    if len(train_groups)<minimum_train or len(test_groups)<48:raise ValueError('Insufficient reviewed family coverage')
    covered = {s:set().union(*(coverage({'draft':r['target']}) for r in rows)) for s,rows in [('train',train),('test',test)]}
    required = {'color:'+c for c in ('W','U','B','R','G','colorless','multicolor')}
    required |= {'type:'+t for t in ('Creature','Artifact','Enchantment','Instant','Sorcery','Land')}
    if any(required-covered[s] for s in covered):raise ValueError('Reviewed splits lost required colors/types')
    pool = [r for r in read(root/'replay-pool.jsonl') if r['source_group_id'] not in test_groups|reserved_ids]
    # Include about 10% planeswalker replay; do not fabricate parseable custom PWs.
    planeswalkers = [r for r in pool if 'Planeswalker' in r['target'].get('type_line','')]
    rank = lambda r:digest([VERSION,'replay-order',r['example_id']])
    replay = sorted(planeswalkers,key=rank)[:min(192,len(train)//10)]
    replay_ids = {r['example_id'] for r in replay}
    replay += sorted([r for r in pool if r['example_id'] not in replay_ids],key=rank)[:len(train)-len(replay)]
    if len(replay)!=len(train):raise ValueError('Insufficient 1:1 replay pool')
    train += [{'id':'replay-'+r['example_id'],'source_group_id':r['source_group_id'],'split':'train',
               'style':r['style'],'request':r['description'],'target':r['target'],'origin':'official_replay'} for r in replay]
    normalized = lambda s:' '.join(s.casefold().split())
    train_requests = {normalized(r['request']) for r in train}
    if train_requests & {normalized(r['request']) for r in test+dev}:raise ValueError('Train/evaluation description overlap')
    if {r['source_group_id'] for r in train} & {r['source_group_id'] for r in test+dev}:
        raise ValueError('Train/evaluation family overlap')
    # Deduplicate identical training pairs; reject conflicting targets for exact inputs.
    unique = {}
    for row in train:
        key = normalized(row['request'])
        if key in unique and unique[key]['target']!=row['target']:
            raise ValueError('Ambiguous duplicate training prompt')
        unique[key] = row
    train = sorted(unique.values(),key=lambda r:digest([VERSION,'shuffle',r['id']]))
    output.mkdir(parents=True)
    system = (root/'system-prompt.txt').read_text()
    (output/'system-prompt.txt').write_text(system)
    config = json.loads((root/'config.json').read_text())
    files = {'train.jsonl':write_jsonl(output/'train.jsonl',train),
             'custom-test.jsonl':write_jsonl(output/'custom-test.jsonl',test),
             'excluded.jsonl':write_jsonl(output/'excluded.jsonl',excluded),
             'reference-parser.jsonl':write_jsonl(output/'reference-parser.jsonl',references)}
    files['train.sft.jsonl'] = write_jsonl(output/'train.sft.jsonl',[
        {'prompt':[{'role':'system','content':system},{'role':'user','content':r['request']}],
         'completion':[{'role':'assistant','content':json.dumps(r['target'],ensure_ascii=False,separators=(',',':'))}]} for r in train])
    cases = [{**r,'panel':'custom_holdout'} for r in test]
    cases += [{**r,'panel':'existing_development'} for r in dev]
    files['evaluation-cases.jsonl'] = write_jsonl(output/'evaluation-cases.jsonl',cases)
    files['evaluation-prompts.jsonl'] = write_jsonl(output/'evaluation-prompts.jsonl',[
        {'id':r['id'],'prompt':[{'role':'system','content':serving_system},{'role':'user','content':r['request']}]} for r in cases])
    result = {'version':VERSION,'files':files,'checks':{'group_overlap':0,'description_overlap':0},
        'counts':{'train':len(train),'test':len(test),'development':len(dev)},
        'custom_train_families':len(train_groups),'custom_test_families':len(test_groups),
        'replay_examples':len(replay),'planeswalker_replay_examples':len([r for r in replay if 'Planeswalker' in r['target'].get('type_line','')]),
        'styles':dict(Counter(r['style'] for r in train)),
        'source_preparation_sha256':digest((root/'preparation.json').read_bytes()),
        'review_config_sha256':digest(config),'teacher':config['teacher'],'reviewer':config['reviewer'],
        'parser':provenance,'reminder_policy_counts':dict(Counter(r['reminder_policy'] for r in references)),
        'human_reviewed':False,'publication':False,
        'evaluation_policy':'Freeze one recipe before held-out generation. Test variants remain together by family; no held-out rows enter training or monitoring. Old development is a regression panel, not unseen-SFT accuracy.'}
    save(output/'manifest.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('root','output','development','reserved','serving-system'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();print(json.dumps(export(a.root,a.output,a.development,a.reserved,a.serving_system.read_text()),indent=2))
