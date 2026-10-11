"""Paired Q4 repair quality, harmless no-ops, edits and generation regressions."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import socket
import subprocess
import time
import urllib.request

import jsonschema

from corpus_protocol import keyed_schema
from custom_training_data import read, save
from data_utils import digest, write_jsonl
from paths import PROJECT
from repair_expansion import checked, design
from repair_review import cached_call, controls, REVIEW, VERDICT
from rl_parser_cache import ParserCache
from rl_serving_eval import generate, evaluate as generation_evaluate
from rl_gpu import sha


def generate_arm(root, model, server, arm, port=4231, threads=8, slots=4):
    output = root/'q4'
    output.mkdir(exist_ok=True)
    identity = {'model_sha256': sha(model), 'cases_sha256': sha(root/'dataset/evaluation-cases.jsonl'),
                'server_sha256': sha(server), 'threads': threads, 'slots': slots,
                'context_per_slot': 4096, 'backend': 'llama.cpp CPU Q4_0',
                'scope': 'Quality comparison, not browser GPU timing', 'automatic_promotion': False}
    pin = output/(arm+'-identity.json')
    if pin.exists() and json.loads(pin.read_text()) != identity:
        raise ValueError('Comparison inputs changed')
    save(pin, identity)
    endpoint = f'http://127.0.0.1:{port}'
    def sample():
        return generate(root/'dataset/evaluation-cases.jsonl', (root/'system-prompt.txt').read_text(),
                        identity['model_sha256'], endpoint, output/(arm+'.jsonl'), slots, case_messages=True)
    manifest = output/(arm+'.manifest.json')
    if manifest.exists() and json.loads(manifest.read_text()).get('complete'):
        return sample()
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', port))
    command = [str(server), '--model', str(model), '--host', '127.0.0.1', '--port', str(port),
               '--threads', str(threads), '--threads-batch', str(threads), '--ctx-size', str(slots*4096),
               '--parallel', str(slots), '--jinja', '--no-webui',
               '--chat-template-kwargs', '{"enable_thinking":false}']
    save(output/(arm+'-command.json'), command)
    with (output/(arm+'-server.log')).open('a') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic()+120
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Evaluation server exited; inspect log')
                try:
                    with urllib.request.urlopen(endpoint+'/health', timeout=2) as response:
                        if json.load(response).get('status') == 'ok':
                            break
                except (OSError, ValueError):
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError('Evaluation server startup')
                time.sleep(.5)
            return sample()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


def preserved_metadata(case, candidate):
    expected = case['target'] if case['panel'] == 'edit' else case['current_card']
    return design({**candidate, 'oracle_text': ''}) == design({**expected, 'oracle_text': ''})


def score(case, candidate, parsed, judgment, valid):
    metadata = bool(valid and preserved_metadata(case, candidate))
    semantic = bool(judgment and judgment['verdict'] == 'pass' and not judgment['issues'])
    unchanged = bool(valid and design(candidate) == design(case['current_card']))
    unsupported = bool(case.get('unsupported_control'))
    faithful = bool(valid and metadata and semantic)
    return {'schema_valid': valid, 'metadata_preserved': metadata, 'semantics_preserved': semantic,
            'mtgish_accepted': parsed, 'faithful_and_parsed': faithful and parsed,
            'unchanged': unchanged, 'unsupported_control': unsupported,
            'successful': faithful and (unchanged if unsupported else parsed),
            'unsupported_preserved': unsupported and faithful and unchanged}


def paired_report(cases, rows):
    lookup = {c['id']: c for c in cases}
    panels = defaultdict(lambda: defaultdict(dict))
    for row in rows:
        panels[lookup[row['case_id']]['panel']][row['arm']][row['case_id']] = row
    result = {}
    for panel, arms in panels.items():
        if set(arms) != {'dpo', 'repair'} or set(arms['dpo']) != set(arms['repair']):
            raise ValueError('Unpaired evaluation panel')
        keys = sorted(arms['dpo'])
        metrics = sorted(arms['dpo'][keys[0]]['metrics'])
        counts = {arm: {m: sum(bool(row['metrics'][m]) for row in values.values()) for m in metrics}
                  for arm, values in arms.items()}
        groups = defaultdict(list)
        for key in keys:
            groups[lookup[key]['source_group_id']].append(
                int(arms['repair'][key]['metrics']['successful'])-int(arms['dpo'][key]['metrics']['successful']))
        grouped = list(groups.values()); rng = random.Random(71); samples = []
        for _ in range(5000):
            sample = [grouped[rng.randrange(len(grouped))] for _ in grouped]
            samples.append(100*sum(map(sum, sample))/sum(map(len, sample)))
        samples.sort()
        result[panel] = {'cases': len(keys), 'families': len(grouped), 'counts': counts,
            'success_difference_pp': 100*(counts['repair']['successful']-counts['dpo']['successful'])/len(keys),
            'family_bootstrap_95_percent_interval_pp': [samples[124], samples[4874]]}
    return result


def evaluate(root, workers=3):
    output = root/'q4/comparison'; output.mkdir(parents=True, exist_ok=True)
    cases = read(root/'dataset/evaluation-cases.jsonl'); lookup = {c['id']: c for c in cases}
    schema = json.loads((PROJECT/'schemas/design-draft-v1.json').read_text())
    schema['required'] += ['name', 'rarity']
    predictions = {}; identities = {}
    for arm in ('dpo', 'repair'):
        path = root/'q4'/(arm+'.jsonl'); receipt = json.loads(path.with_suffix('.manifest.json').read_text())
        rows = read(path)
        if (not receipt['complete'] or receipt['sha256'] != sha(path)
                or receipt['cases_sha256'] != sha(root/'dataset/evaluation-cases.jsonl')
                or len(rows) != len(cases) or {r['case_id'] for r in rows} != set(lookup)):
            raise ValueError('Incomplete or changed evaluation predictions')
        predictions[arm] = rows; identities[arm] = receipt
    if identities['dpo']['decoding'] != identities['repair']['decoding']:
        raise ValueError('Decoding protocols differ')
    unique = {}; aliases = {}; assessed = {}; by_key = {}
    with ParserCache(output/'parser-cache') as parser:
        for arm, rows in predictions.items():
            for row in rows:
                case = lookup[row['case_id']]
                if case['panel'] == 'generation':
                    continue
                key = digest([arm, row['case_id']])
                by_key[key] = (arm, case, row)
                try:
                    candidate = json.loads(row['raw'])
                    jsonschema.validate(candidate, schema)
                    valid = not row['hit_token_limit']
                except (ValueError, jsonschema.ValidationError):
                    candidate = None; valid = False
                check = checked(parser, candidate) if valid else {}
                assessed[key] = (candidate, valid, check)
                if valid:
                    data = {'current_card': case['current_card'],
                        'requested_change': case['requested_change'], 'candidate': design(candidate)}
                    content_key = digest(data)
                    aliases[key] = content_key
                    unique[content_key] = {'id': content_key, **data}
        provenance = parser.provenance
    pending = list(unique.values())
    pending.sort(key=lambda r: digest(['repair-eval-blind-order', r['id']]))
    labels = [f'c{i}' for i in range(8)]
    def judge(index):
        batch = pending[index*8:(index+1)*8]
        data = {k: {field: row[field] for field in ('current_card', 'requested_change', 'candidate')}
                for k, row in zip(labels, batch)}
        data.update(controls())
        value = cached_call(root, {'instructions': REVIEW, 'cases': data},
            keyed_schema('reviews', labels+['control_bad', 'control_good'], VERDICT),
            output/'judge'/f'{index:04}')['reviews']
        if value['control_bad']['verdict'] != 'reject' or value['control_good']['verdict'] != 'pass':
            raise ValueError('Evaluation reviewer failed calibration')
        return {row['id']: value[k] for k, row in zip(labels, batch)}
    judgments = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for batch in pool.map(judge, range((len(pending)+7)//8)):
            judgments.update(batch)
    scored = []
    for key, (arm, case, row) in by_key.items():
        candidate, valid, check = assessed[key]; judgment = judgments.get(aliases.get(key))
        scored.append({'arm': arm, 'case_id': case['id'], 'source_group_id': case['source_group_id'],
            'metrics': score(case, candidate, check.get('parse_complete') is True, judgment, valid),
            'raw': row['raw'], 'check': check, 'judgment': judgment})
    generation = output/'generation'; generation.mkdir(exist_ok=True)
    generation_cases = [c for c in cases if c['panel'] == 'generation']
    write_jsonl(generation/'cases.jsonl', generation_cases)
    for arm, rows in predictions.items():
        path = generation/(arm+'.jsonl')
        write_jsonl(path, [r for r in rows if lookup[r['case_id']]['panel'] == 'generation'])
        save(path.with_suffix('.manifest.json'), {**identities[arm], 'sha256': sha(path),
            'cases_sha256': sha(generation/'cases.jsonl'), 'cases': len(generation_cases),
            'completions': len(generation_cases), 'subset_of': identities[arm]['sha256']})
    generation_evaluate(generation/'cases.jsonl', {a: generation/(a+'.jsonl') for a in predictions},
                        generation/'scored', workers, display_names=True)
    for row in read(generation/'scored/cases.jsonl'):
        parsed = row['checks'].get('mtgish', {}).get('parse_complete') is True
        if row['checks'].get('mtgish', {}).get('status') == 'parser_error':
            raise ValueError('Generation parser infrastructure error')
        scored.append({**row, 'metrics': {'schema_valid': row['checks']['schema_valid'],
            'mtgish_accepted': parsed, 'intent_faithful': row['intent_faithful'],
            'successful': row['intent_faithful'] and parsed}})
    write_jsonl(output/'cases.jsonl', scored)
    real_cases = [{**c, 'panel': 'unsupported_valid' if c.get('unsupported_control') else 'real_wording_repair'}
                  for c in cases if c['panel'] == 'real']
    real_ids = {c['id'] for c in real_cases}
    walker_cases = [{**c, 'panel': 'planeswalker_'+c['panel']} for c in cases
                    if c['panel'] != 'generation' and 'Planeswalker' in c['current_card']['type_line']]
    walker_ids = {c['id'] for c in walker_cases}
    report = {'panels': paired_report(cases, scored),
        'real_error_breakdown': paired_report(real_cases, [r for r in scored if r['case_id'] in real_ids]),
        'planeswalker_breakdown': paired_report(walker_cases, [r for r in scored if r['case_id'] in walker_ids]),
        'predictions': identities, 'parser': provenance,
        'judge': 'gpt-6.1-sol medium; blind candidate identities; calibrated batches',
        'automatic_promotion': False, 'inference': 'Native CPU llama.cpp Q4_0; not browser timing',
        'caveats': ['Existing development panel is diagnostic, not an untouched final test.',
            'Repair holdout families are excluded from this continuation but may occur in original SFT.',
            'Same model family supplies teacher and reviewer labels; no claim of human accuracy.',
            'Parser rejection can mean unsupported rules; unsupported controls require unchanged output.',
            'Family bootstrap intervals are descriptive and uncorrected for multiple comparisons.']}
    save(output/'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['generate', 'evaluate'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--model', type=Path); parser.add_argument('--server', type=Path)
    parser.add_argument('--arm', choices=['dpo', 'repair']); parser.add_argument('--port', type=int, default=4231)
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    if args.action == 'generate':
        if not args.model or not args.server or not args.arm:
            parser.error('generate needs --model, --server and --arm')
        result = generate_arm(args.root, args.model, args.server, args.arm, args.port)
    else:
        result = evaluate(args.root, args.workers)
    print(json.dumps(result, indent=2))
