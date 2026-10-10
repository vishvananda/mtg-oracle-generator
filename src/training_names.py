"""Add supervised names at export time without changing reviewed teacher queues."""
from copy import deepcopy
import json
import re

from data_utils import digest

VERSION = 'card-names-v1'
NAME_INSTRUCTIONS = (' Always include a nonempty name and names for every card face. '
                     'Preserve any explicitly requested name exactly; otherwise propose a fitting, editable name. '
                     'Keep CARDNAME for self-references in Oracle text.')
REQUESTED_NAMES = ('Emberwake', 'Moonlit Accord', 'Veil of Echoes', 'Crown of Tides',
                   'Wildheart Wanderer', 'The Last Lantern', 'Winter’s Promise', 'Dawnwarden')


def named_record(row, source, requested_name=None):
    if (row['source']['oracle_id'] != source['oracle_id'] or
            row['source']['source_sha256'] != source['source_sha256']):
        raise ValueError('Naming source does not match the reviewed card snapshot')
    result = deepcopy(row)
    target = result['target']
    source_faces = source.get('source_faces') or []
    aliases = {f'Draft Face {i+1}': f['name'] for i, f in enumerate(source_faces)}
    def bind(value):
        if isinstance(value, str):
            return re.sub(r'\bDraft Face [1-9]\d*\b', lambda m: aliases.get(m[0], m[0]), value)
        if isinstance(value, list): return [bind(v) for v in value]
        if isinstance(value, dict): return {k: bind(v) for k, v in value.items()}
        return value
    target = result['target'] = bind(target)
    result['description'] = bind(result['description'])
    result['expressed_constraints'] = bind(result.get('expressed_constraints', {}))
    if target.get('card_faces'):
        if requested_name is not None: raise ValueError('Explicit-name augmentation currently covers single-face drafts')
        if len(target['card_faces']) != len(source_faces): raise ValueError('Card face count changed before naming')
        for face, original in zip(target['card_faces'], source_faces): face['name'] = original['name']
        target['name'] = ' // '.join(f['name'] for f in target['card_faces'])
    else:
        target['name'] = requested_name or (source_faces[0]['name'] if source_faces else source['name'])
    if not isinstance(target['name'], str) or not target['name'].strip(): raise ValueError('Missing source name')
    result['naming'] = {'version': VERSION, 'policy': 'specified' if requested_name else 'proposed_source_name',
                        'source_sha256': source['source_sha256']}
    proposed = [p for p in result.get('proposed_fields', []) if p != 'name']
    if requested_name:
        result['description'] += '\nName it ' + json.dumps(requested_name, ensure_ascii=False) + '.'
        result['expressed_constraints']['name'] = requested_name
        result['specified_fields'] = sorted(set(result.get('specified_fields', []) + ['name']))
        result['parent_example_id'] = row['example_id']
        result['example_id'] = digest([VERSION, row['example_id'], requested_name])[:24]
    else: proposed.append('name')
    result['proposed_fields'] = proposed
    return result


def add_training_names(rows, source_root):
    manifest = json.loads((source_root/'manifest.json').read_text())
    pin = manifest['files']['sources.jsonl']
    expected = pin['sha256'] if isinstance(pin, dict) else pin
    if digest((source_root/'sources.jsonl').read_bytes()) != expected: raise ValueError('Naming source hash mismatch')
    sources = {r['oracle_id']: r for line in (source_root/'sources.jsonl').open() if (r := json.loads(line))}
    enriched = []
    for row in rows:
        source = sources[row['source']['oracle_id']]
        enriched.append(named_record(row, source))
        # Same source group/split; about one extra explicit-name example per five cards.
        choice = int(digest([VERSION, row['example_id']])[:8], 16)
        if not row['target'].get('card_faces') and choice % 5 == 0:
            enriched.append(named_record(row, source, REQUESTED_NAMES[(choice // 5) % len(REQUESTED_NAMES)]))
    return enriched
