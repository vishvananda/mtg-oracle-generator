"""Compact, stable wire labels; permanent dataset IDs never change."""
import copy

VERSION = 'compact-prefix-v1'


def keyed_schema(key, labels, value_schema):
    labels = list(labels)
    entries = {'type': 'object', 'additionalProperties': False, 'required': list(labels),
               'properties': {label: copy.deepcopy(value_schema) for label in labels}}
    # Null is only for warming the instruction-only base. Batch decoders reject it.
    return {'type': 'object', 'additionalProperties': False, 'required': [key],
            'properties': {key: {'anyOf': [{'type': 'null'}, entries]}}}


def teacher_request(payload):
    payload = copy.deepcopy(payload)
    mapping = {f'd{i+1:02}': row['id'] for i, row in enumerate(payload['projections'])}
    for label, row in zip(mapping, payload['projections']):
        row['id'] = label
    return payload, keyed_schema('descriptions', mapping, {'type': 'string'}), mapping


def teacher_reply(reply, mapping):
    rows = reply.get('descriptions')
    if not isinstance(rows, dict) or set(rows) != set(mapping):
        raise ValueError('Missing or unknown compact description labels')
    if any(not isinstance(v, str) or not v.strip() for v in rows.values()):
        raise ValueError('Empty or invalid compact description')
    return {'descriptions': [{'id': identifier, 'description': rows[label], 'coverage_notes': ''}
                             for label, identifier in mapping.items()]}


def review_schema(labels):
    return keyed_schema('reviews', labels, {
        'type': 'object', 'additionalProperties': False, 'required': ['verdict', 'issues'],
        'properties': {'verdict': {'enum': ['pass', 'reject', 'uncertain']},
                       'issues': {'type': 'array', 'items': {'type': 'string'}}}})


def upgrade_config(config):
    """Version the wire format without changing the frozen description policy."""
    config = copy.deepcopy(config)
    old = config['instructions']
    if config.get('description_policy') == 'plausible_completion':
        start = old.index('Return JSON with')
        end = old.index('was retained and which implementation details were deliberately left open.', start)
        end += len('was retained and which implementation details were deliberately left open.')
    else:
        start = old.index('Return one JSON object with key "descriptions"')
        end = old.index('Do not add a reasoning transcript.', start)+len('Do not add a reasoning transcript.')
    replacement = ('Return a JSON object with "descriptions": an object mapping every supplied '
                   'short projection label to its description string. Include every label exactly once. '
                   'Do not output coverage notes, targets, original IDs, or a reasoning transcript.')
    config['instructions'] = old[:start]+replacement+old[end:]
    config['wire_protocol'] = VERSION
    config['transport'] = 'cached-text-fork-v1'
    return config
