"""Join printing rarity onto reviewed targets without mutating teacher queues."""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

from data_utils import digest

RARITIES = ('common', 'uncommon', 'rare', 'mythic', 'special', 'bonus')
VERSION = 'card-rarity-v1'
RARITY_INSTRUCTIONS = (' Always include rarity: common, uncommon, rare, mythic, special, or bonus. '
                       'Preserve an explicitly requested rarity; otherwise propose an editable rarity, normally one of the first four. '
                       'Rarity is a design suggestion, not a balance certification.')


def rarity_record(row, card, *, explicit=False):
    if row['source']['oracle_id'] != card['oracle_id'] or row['source']['source_sha256'] != digest(card):
        raise ValueError('Rarity source does not match the reviewed card snapshot')
    rarity = row.get('expressed_constraints', {}).get('rarity', card.get('rarity'))
    if rarity not in RARITIES: raise ValueError('Missing or invalid source rarity')
    result = deepcopy(row)
    result['target']['rarity'] = rarity
    specified = 'rarity' in result.get('expressed_constraints', {})
    if explicit and not specified:
        result['description'] += '\nMake it ' + ('mythic rare' if rarity == 'mythic' else rarity) + '.'
        result.setdefault('expressed_constraints', {})['rarity'] = rarity
        result['parent_example_id'] = row['example_id']
        result['example_id'] = digest([VERSION, row['example_id'], rarity])[:24]
        specified = True
    result['proposed_fields'] = [p for p in result.get('proposed_fields', []) if p != 'rarity']
    if specified:
        result['specified_fields'] = sorted(set(result.get('specified_fields', []) + ['rarity']))
    else: result['proposed_fields'].append('rarity')
    result['rarity_provenance'] = {'version':VERSION, 'policy':'specified' if specified else 'reference_printing',
        'printing_id':card.get('id'), 'set':card.get('set'), 'source_sha256':digest(card)}
    return result


def add_training_rarity(rows, source_root):
    manifest = json.loads((source_root/'manifest.json').read_text())
    # Broad-description corpora pin their parent Oracle corpus instead of
    # duplicating its bulk-download metadata. Verify that link before joining.
    if 'source' not in manifest:
        parent = Path(manifest['source_root'])
        if not parent.is_absolute(): parent = source_root/parent
        data = (parent/'manifest.json').read_bytes()
        if digest(data) != manifest['source_manifest_sha256']:
            raise ValueError('Rarity parent source manifest hash mismatch')
        manifest = json.loads(data)
    source = manifest['source']
    path = Path(source['source_file'])
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != source['download_sha256']:
            raise ValueError('Rarity bulk snapshot hash mismatch')
    wanted = {row['source']['oracle_id'] for row in rows}
    with gzip.open(path, 'rt') as stream:
        cards = {c['oracle_id']:c for line in stream if (c := json.loads(line))['oracle_id'] in wanted}
    # Keep one target per row; a stable subset teaches explicit rarity requests.
    return [rarity_record(row, cards[row['source']['oracle_id']],
            explicit=int(digest([VERSION, row['example_id']])[:8], 16) % 5 == 0) for row in rows]


def enrich_snapshot(input_root, source_root, output):
    """Upgrade an existing immutable train/validation snapshot; never launch training."""
    from collections import Counter
    from jsonschema import Draft202012Validator
    from data_utils import write_jsonl
    from export_full_corpus import select_unique
    from paths import PROJECT
    from training_names import add_training_names, NAME_INSTRUCTIONS
    if output.exists(): raise ValueError('Choose a new immutable snapshot directory')
    manifest = json.loads((input_root/'manifest.json').read_text())
    rows = []
    for split in ('train', 'validation'):
        path = input_root/(split+'.jsonl')
        pin = manifest['files'][path.name]
        if digest(path.read_bytes()) != (pin['sha256'] if isinstance(pin, dict) else pin):
            raise ValueError('Input training snapshot hash mismatch')
        for line in path.open():
            row = json.loads(line)
            if row['split'] != split: raise ValueError('Split mismatch')
            if row.get('naming') or row.get('rarity_provenance'): raise ValueError('Snapshot is already enriched')
            rows.append(row)
    rows, duplicates = select_unique(add_training_rarity(add_training_names(rows,source_root),source_root))
    schema = json.loads((PROJECT/'schemas/design-draft-v1.json').read_text())
    schema['required'] = sorted(set(schema['required']+['name','rarity']))
    validator = Draft202012Validator(schema)
    for row in rows: validator.validate(row['target'])
    prompt = (PROJECT/'prompts/design-draft-v2.txt').read_text().rstrip()+NAME_INSTRUCTIONS+RARITY_INSTRUCTIONS+'\n'
    output.mkdir(parents=True)
    files = {}
    for split in ('train','validation'):
        selected = [r for r in rows if r['split']==split]
        files[split+'.jsonl'] = write_jsonl(output/(split+'.jsonl'),selected)
        files[split+'.sft.jsonl'] = write_jsonl(output/(split+'.sft.jsonl'),[
            {'prompt':[{'role':'system','content':prompt},{'role':'user','content':r['description']}],
             'completion':[{'role':'assistant','content':json.dumps(r['target'],ensure_ascii=False,separators=(',',':'))}]} for r in selected])
    (output/'draft-schema.json').write_text(json.dumps(schema,indent=2)+'\n')
    (output/'student-prompt.txt').write_text(prompt)
    for name in ('draft-schema.json','student-prompt.txt'): files[name]={'sha256':digest((output/name).read_bytes())}
    receipt = {'version':'names-rarity-v1', 'counts':dict(Counter(r['split'] for r in rows)),
        'rarities':dict(Counter(r['target']['rarity'] for r in rows)),
        'explicit_rarity_examples':sum(r['rarity_provenance']['policy']=='specified' for r in rows),
        'checks':{'group_overlap':0,'description_overlap':0,'schema_passed':len(rows)},
        'duplicates_removed':len(duplicates),'files':files,
        'parent_manifest_sha256':digest((input_root/'manifest.json').read_bytes()),
        'source_manifest_sha256':digest((source_root/'manifest.json').read_bytes()),
        'rarity_policy':'Pinned reference printing, not an Oracle-level invariant or verified balance rating.',
        'final_test_used':False,'training_started':False}
    (output/'manifest.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(enrich_snapshot(args.input,args.source_root,args.output),indent=2))
