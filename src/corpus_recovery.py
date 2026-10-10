"""Immutable, source-audited recovery of quarantined description rows.

Original worker files are never modified. A recovery snapshot is an explicit
overlay for export, bound to hashes of the source manifest and original rows.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import fcntl
import gzip
import hashlib
import json
from pathlib import Path
import time
import uuid

from jsonschema import Draft202012Validator
from corpus_checks import row_checks, VERSION as CHECKS_VERSION
from corpus_protocol import keyed_schema
from codex_cached_batch import invoke
from data_utils import digest, write_jsonl
from full_corpus import draft, project
from paths import PROJECT

VERSION = 'quarantine-recovery-v1'
TERMINAL = {'accepted', 'quarantined', 'source_needs_review', 'error'}


def atomic(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    tmp.replace(path)


def source_manifest(root):
    data = json.loads((root/'manifest.json').read_text())
    if 'source' not in data:
        parent = Path(data['source_root'])
        if not parent.is_absolute(): parent = root/parent
        blob = (parent/'manifest.json').read_bytes()
        if digest(blob) != data['source_manifest_sha256']: raise ValueError('Parent source manifest changed')
        data = json.loads(blob)
    return data['source']


def load_sources(root):
    manifest = json.loads((root/'manifest.json').read_text())
    pin = manifest['files']['sources.jsonl']
    expected = pin['sha256'] if isinstance(pin, dict) else pin
    if digest((root/'sources.jsonl').read_bytes()) != expected: raise ValueError('Source projections changed')
    with (root/'sources.jsonl').open() as stream:
        return {s['oracle_id']: s for line in stream if (s := json.loads(line))}


def audit_sources(root, sources):
    source = source_manifest(root)
    path = Path(source['source_file'])
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != source['download_sha256']:
            raise ValueError('Raw Oracle snapshot changed')
    changes, holds = {}, {}
    with gzip.open(path, 'rt') as stream:
        for line in stream:
            raw = json.loads(line)
            oid = raw['oracle_id']
            if oid not in sources: continue
            src = sources[oid]
            if digest(raw) != src['source_sha256']: raise ValueError('Card snapshot hash mismatch')
            new = draft(raw)
            old = next(p['draft_target'] for p in src['projections'] if p['style'] in ('complete', 'broad_plain'))
            if old != new:
                projections = {p['style']: p for p in project(raw)}
                changes[oid] = {'name': src['name'], 'old': old, 'new': new,
                                'projections': projections, 'reason': 'source_projection_corrected'}
            # Modal DFCs can transform under the 2025 Spider-Man rules update.
            # Source doubts raised by review stay unresolved, without guessed targets.
    return {'cards_audited': len(sources), 'raw_snapshot_sha256': source['download_sha256'],
            'changes': changes, 'holds': holds}


def corrected_row(row, audit):
    result = deepcopy(row)
    change = audit['changes'].get(row['source']['oracle_id'])
    if change and row['style'] not in ('minimal', 'concept'):
        if row.get('description_policy') == 'plausible_completion': result['target'] = deepcopy(change['new'])
        else:
            p = change['projections'][row['style']]
            result['target'] = deepcopy(p['draft_target'])
            result['expressed_constraints'] = deepcopy(p['target'])
    return result


def controls_valid(row):
    return row.get('fidelity_controls_rejected') == 3 and (
        row.get('description_policy') != 'plausible_completion' or row.get('completion_controls_checked') == 4)


def assess(original, source, audit, validator):
    row = corrected_row(original, audit)
    changed = row['target'] != original['target'] or row['expressed_constraints'] != original['expressed_constraints']
    issues = row_checks(row, source)
    schema_issues = ['schema:'+e.message for e in validator.iter_errors(row['target'])]
    issues += schema_issues
    if not controls_valid(original): issues.append('missing_review_controls')
    row.update(deterministic_issues=issues, checks_version=CHECKS_VERSION)
    hold = audit['holds'].get(row['source']['oracle_id'])
    # Sparse tasks intentionally omit the source mechanics and layout.
    blocked = bool(hold and row['style'] not in ('minimal', 'concept')) or bool(schema_issues)
    passed = original['fidelity_review']['verdict'] == 'pass' and not original['fidelity_review']['issues']
    if original['status'] == 'accepted' and not changed and not blocked and not issues: return None
    state = 'source_needs_review' if blocked else 'accepted' if not changed and not issues and passed else 'pending'
    row['status'] = 'accepted' if state == 'accepted' else 'quarantined'
    return {'example_id': row['example_id'], 'original_sha256': digest(original), 'original': original,
            'row': row, 'source': {k: source[k] for k in ('name', 'oracle_id', 'source_sha256')},
            'target_changed': changed, 'source_issues': (hold['issues'] if hold and blocked else [])+schema_issues,
            'state': state, 'repair_attempts': 0, 'history': [],
            'recovery_reason': 'target_audit' if changed or blocked else 'description_checks_or_review'}


def prepare(root, output, model='gpt-6.1-sol', batch_size=24, prior=None):
    if output.exists(): raise ValueError('Use a new recovery directory')
    sources = load_sources(root)
    audit = audit_sources(root, sources)
    output.mkdir(parents=True)
    (output/'records').mkdir()
    atomic(output/'target-audit.json', audit)
    instructions = '\n'.join((PROJECT/'prompts'/name).read_text() for name in
                             ('recover-descriptions-v1.txt','player-language-v1.txt','rules-updates-v1.txt'))
    code_pins = {name: digest((PROJECT/'src'/name).read_bytes()) for name in
                 ('corpus_recovery.py', 'corpus_checks.py', 'full_corpus.py', 'codex_cached_batch.py')}
    config = {'version': VERSION, 'root': str(root.resolve()), 'created_at': time.time(),
              'source_manifest_sha256': digest((root/'manifest.json').read_bytes()),
              'audit_sha256': digest((output/'target-audit.json').read_bytes()),
              'checks_version': CHECKS_VERSION, 'model': model, 'reasoning_effort': 'medium',
              'max_repair_attempts': 2, 'batch_size': batch_size, 'instructions': instructions,
              'code_sha256': code_pins,
              'prior_snapshot_sha256':digest((prior/'manifest.json').read_bytes()) if prior else None}
    atomic(output/'config.json', config)
    refresh(root, output, sources=sources, audit=audit)
    if prior: adopt_prior(root, output, prior)
    return progress(output)


def verify(root, output):
    config = json.loads((output/'config.json').read_text())
    if str(root.resolve()) != config['root'] or digest((root/'manifest.json').read_bytes()) != config['source_manifest_sha256']:
        raise ValueError('Recovery belongs to a different source corpus')
    if digest((output/'target-audit.json').read_bytes()) != config['audit_sha256']: raise ValueError('Target audit changed')
    for name, pin in config['code_sha256'].items():
        if digest((PROJECT/'src'/name).read_bytes()) != pin: raise ValueError('Recovery code changed; start a new version: '+name)
    return config


def progress(output):
    records = [json.loads(p.read_text()) for p in sorted((output/'records').glob('*.json'))]
    states = Counter(r['state'] for r in records)
    report = {'updated_at': time.time(), 'states': dict(states), 'candidate_rows': len(records),
              'accepted_without_rewrite': sum(r['state']=='accepted' and r['row']['description']==r['original']['description'] for r in records),
              'accepted_rewritten': sum(r['state']=='accepted' and r['row']['description']!=r['original']['description'] for r in records),
              'previously_accepted_held': sum(r['original']['status']=='accepted' and r['state']!='accepted' for r in records),
              'repair_attempts': sum(r['repair_attempts'] for r in records), 'automatic_training': False}
    atomic(output/'progress.json', report)
    return report


def refresh(root, output, *, sources=None, audit=None):
    verify(root, output)
    sources = sources if sources is not None else load_sources(root)
    audit = audit if audit is not None else json.loads((output/'target-audit.json').read_text())
    validator = Draft202012Validator(json.loads((root/'draft-schema.json').read_text()))
    seen = {p.stem for p in (output/'records').glob('*.json')}
    for path in sorted((root/'queue').glob('*.json')):
        job = json.loads(path.read_text())
        if job['state'] != 'completed': continue
        for line in (root/'runs'/job['id']/'results.jsonl').read_text().splitlines():
            row = json.loads(line)
            if row['example_id'] in seen: continue
            record = assess(row, sources[row['source']['oracle_id']], audit, validator)
            if record: atomic(output/'records'/(row['example_id']+'.json'), record)
    return progress(output)


def control_cases():
    return [
        ({'type_line':'Sorcery','oracle_text':'Each opponent loses 3 life.'}, 'A sorcery that makes me lose 3 life.', 'reject', None),
        ({'type_line':'Creature','oracle_text':'When this creature enters, you may gain 4 life.'}, 'A creature whose ETB forces me to gain 4 life; I cannot decline.', 'reject', None),
        ({'type_line':'Sorcery','oracle_text':'Until end of turn, you may play cards from the top of your library.'}, 'A sorcery letting me play cards from the top of my library for the rest of the game.', 'reject', None),
        ({}, 'A planeswalker that makes plants and has a big card-draw ultimate.', 'pass',
         {'type_line':'Legendary Planeswalker','loyalty':'3','oracle_text':'+1: Create a 0/1 green Plant creature token.\n−7: Draw seven cards.'}),
        ({}, 'A planeswalker whose ultimate deals seven damage to each opponent.', 'reject',
         {'type_line':'Legendary Planeswalker','oracle_text':'+1: Create a 0/1 green Plant creature token.\n−7: Draw seven cards.'}),
        ({}, 'A red instant for rummaging.', 'pass', {'type_line':'Instant','colors':['R'],'oracle_text':'As an additional cost to cast this spell, discard a card.\nDraw two cards.'}),
        ({}, 'A red instant that lets me draw first and then discard.', 'reject', {'type_line':'Instant','colors':['R'],'oracle_text':'As an additional cost to cast this spell, discard a card.\nDraw two cards.'}),
        ({}, 'A two-sided creature I can cast on either side, whose front can transform into its back.', 'pass',
         {'layout':'modal_dfc','type_line':'Creature // Creature','oracle_text':'','card_faces':[
             {'name':'Draft Face 1','type_line':'Creature','mana_cost':'{1}{W}','oracle_text':'{3}: Transform CARDNAME. Activate only as a sorcery.','power':'1','toughness':'1'},
             {'name':'Draft Face 2','type_line':'Creature','mana_cost':'{3}{W}','oracle_text':'Vigilance','power':'3','toughness':'3'}]}),
    ]


def review_request(records, instructions):
    cases = []
    for r in records:
        row = r['row']
        case = {k: row[k] for k in ('description', 'style', 'rules_coverage', 'expressed_constraints')}
        if row.get('description_policy') == 'plausible_completion':
            case.update(description_policy='plausible_completion', reference_draft=row['target'])
        cases.append((row['example_id'], case, None))
    for i, (target, description, verdict, reference) in enumerate(control_cases()):
        case = {'description': description, 'style': 'mechanics', 'rules_coverage':'all', 'expressed_constraints':target}
        if reference: case.update(style='broad_plain', rules_coverage='summary', description_policy='plausible_completion', reference_draft=reference)
        cases.append(('control_'+str(i), case, verdict))
    cases.sort(key=lambda c: digest(c[0]))
    mapping, expected, payload = {}, {}, []
    for i, (key, case, verdict) in enumerate(cases):
        label = f'case_{i+1:03d}'
        if verdict: expected[label] = verdict
        else: mapping[label] = key
        payload.append({'id': label, **case})
    schema = keyed_schema('reviews', [c['id'] for c in payload], {
        'type':'object','additionalProperties':False,'required':['verdict','issues','suggested_description'],
        'properties': {'verdict': {'enum':['pass','reject','uncertain']},
                       'issues': {'type':'array','items':{'type':'string'}},
                       'suggested_description': {'type':['string','null']}}})
    return {'instructions':instructions, 'cases':payload}, schema, mapping, expected


def checked_reply(reply, mapping, expected):
    reviews = reply.get('reviews')
    if not isinstance(reviews, dict) or set(reviews) != set(mapping)|set(expected): raise ValueError('Missing or unknown review labels')
    for review in reviews.values():
        if review['verdict']=='pass' and (review['issues'] or review['suggested_description'] is not None):
            raise ValueError('Pass cannot contain issues or self-approved edits')
    if any(reviews[k]['verdict'] != verdict for k, verdict in expected.items()): raise ValueError('Reviewer failed fidelity controls')
    return {mapping[k]: reviews[k] for k in mapping}


def process_batch(output, paths, config, *, call=invoke):
    records = [json.loads(p.read_text()) for p in paths]
    for r, p in zip(records, paths):
        r['state']='running'; atomic(p, r)
    pending = records[:]
    batch = output/'runs'/uuid.uuid4().hex
    try:
        for turn in range(config['max_repair_attempts']+1):
            if not pending: break
            payload, schema, mapping, expected = review_request(pending, config['instructions'])
            run = batch/str(turn)
            reply, receipt = call(config['model'], payload, schema, run, prefix_root=output/'codex-prefixes', reasoning_effort=config['reasoning_effort'])
            reviews = checked_reply(reply, mapping, expected)
            again = []
            for r in pending:
                row = r['row']; review = reviews[r['example_id']]
                r['history'].append({'description':row['description'], 'review':review, 'receipt':str(run/'receipt.json'),
                                     'output_sha256':receipt['output_sha256'], 'controls_checked':len(expected)})
                issues = row_checks(row, r['source'])
                if review['verdict']=='pass' and not issues and controls_valid(r['original']):
                    row.update(status='accepted', checks_version=CHECKS_VERSION, deterministic_issues=[],
                               fidelity_review={k:review[k] for k in ('verdict','issues')}, reviewer=config['model'],
                               reviewer_output_sha256=receipt['output_sha256'])
                    r['state']='accepted'
                elif r['repair_attempts'] < config['max_repair_attempts'] and review['suggested_description']:
                    suggestion = review['suggested_description']
                    if suggestion == row['description']:
                        r['state']='quarantined'
                    else:
                        row['description']=suggestion; r['repair_attempts']+=1; again.append(r)
                else: r['state']='quarantined'
                if r['state'] != 'accepted': row['status']='quarantined'
                row['deterministic_issues']=row_checks(row, r['source'])
                atomic(output/'records'/(r['example_id']+'.json'), r)
            pending = again
    except Exception as error:
        for r in pending:
            if r['state']=='running':
                r.update(state='error', error=str(error)); atomic(output/'records'/(r['example_id']+'.json'), r)
        raise


def run(root, output, workers=1, follow=False, max_batches=None, interval=300):
    with (output/'coordinator.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        config=verify(root, output)
        count=0
        while True:
            refresh(root, output)
            paths=[p for p in sorted((output/'records').glob('*.json')) if json.loads(p.read_text())['state']=='pending']
            batches=[paths[i:i+config['batch_size']] for i in range(0,len(paths),config['batch_size'])]
            if max_batches is not None: batches=batches[:max(0,max_batches-count)]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                # At most one wave is queued; a failure cannot launch the entire backlog.
                for i in range(0,len(batches),workers):
                    futures=[pool.submit(process_batch,output,b,config) for b in batches[i:i+workers]]
                    for f in futures: f.result(); count+=1
                    print(json.dumps(progress(output)),flush=True)
            if max_batches is not None or not follow: break
            states=json.loads((root/'progress.json').read_text())['states']
            if not any(v for k,v in states.items() if k!='completed'):
                # Generation may have finished while this wave was being reviewed.
                final=refresh(root,output)
                if final['states'].get('pending',0): continue
                break
            # Detached coordinator sleep; no assistant tool call waits on this.
            time.sleep(interval)
        result=progress(output)
        atomic(output/'run-finished.json',{'finished_at':time.time(),'batches':count,'progress':result})
        return result


def snapshot(root, output, destination):
    config=verify(root,output)
    if destination.exists(): raise ValueError('Use a new immutable snapshot directory')
    records=[json.loads(p.read_text()) for p in sorted((output/'records').glob('*.json'))]
    destination.mkdir(parents=True)
    files={'overrides.jsonl':write_jsonl(destination/'overrides.jsonl',records)}
    (destination/'target-audit.json').write_bytes((output/'target-audit.json').read_bytes())
    files['target-audit.json']={'sha256':config['audit_sha256']}
    manifest={'version':VERSION,'source_manifest_sha256':config['source_manifest_sha256'],
              'config_sha256':digest(config),'config':config,'states':dict(Counter(r['state'] for r in records)),
              'files':files,'created_at':time.time()}
    atomic(destination/'manifest.json',manifest)
    return manifest


def load_overlay(root, directory):
    manifest=json.loads((directory/'manifest.json').read_text())
    if manifest['source_manifest_sha256'] != digest((root/'manifest.json').read_bytes()): raise ValueError('Recovery snapshot source mismatch')
    for name, info in manifest['files'].items():
        if digest((directory/name).read_bytes()) != info['sha256']: raise ValueError('Recovery snapshot hash mismatch: '+name)
    records={}
    with (directory/'overrides.jsonl').open() as stream:
        for line in stream:
            r=json.loads(line)
            if r['example_id'] in records: raise ValueError('Duplicate recovery override')
            records[r['example_id']]=r
    return records,json.loads((directory/'target-audit.json').read_text()),manifest


def apply_overlay(row, records, audit):
    record=records.get(row['example_id'])
    if not record:
        # A partial recovery snapshot must not admit future, unaudited rows from
        # affected cards, even if the old reviewer passed them.
        if row['style'] not in ('minimal','concept') and row['source']['oracle_id'] in (audit['changes'].keys()|audit['holds'].keys()):
            return row,['target_audit_pending']
        return row,[]
    if record['original_sha256'] != digest(row): raise ValueError('Original row changed since recovery')
    candidate=deepcopy(record['row'])
    for key in ('example_id','source','source_group_id','split','style'):
        if candidate[key] != row[key]: raise ValueError('Recovery changed source identity or split')
    corrected=corrected_row(row,audit)
    if any(candidate[k] != corrected[k] for k in ('target','expressed_constraints')): raise ValueError('Unverified target mutation in recovery')
    candidate['recovery']={'version':VERSION,'original_sha256':record['original_sha256'],
                           'repair_attempts':record['repair_attempts'],'target_changed':record['target_changed'],
                           'history':record['history'],'state':record['state']}
    return candidate,[] if record['state']=='accepted' else ['recovery_'+record['state']]


def adopt_prior(root, output, prior):
    """Recheck earlier work under new code without repeating valid model calls."""
    records, prior_audit, _ = load_overlay(root, prior)
    config = verify(root, output)
    for identifier, old in records.items():
        path = output/'records'/(identifier+'.json')
        if not path.exists(): continue
        fresh = json.loads(path.read_text())
        if fresh['original_sha256'] != old['original_sha256']: raise ValueError('Prior recovery original changed')
        candidate, _ = apply_overlay(fresh['original'], records, prior_audit)
        # Review can be reused only for exactly the same target and constraints.
        if any(candidate[k] != fresh['row'][k] for k in ('target','expressed_constraints')): continue
        record = deepcopy(old)
        record['row'].pop('recovery', None)
        row = record['row']
        issues = row_checks(row, record['source'])
        row.update(deterministic_issues=issues, checks_version=CHECKS_VERSION)
        last = record['history'][-1] if record['history'] else None
        reviewed = bool(last and last['description']==row['description'] and last['review']['verdict']=='pass'
                        and not last['review']['issues'] and last['controls_checked']==len(control_cases()))
        original_pass = (not record['target_changed'] and row['description']==record['original']['description']
                         and record['original']['fidelity_review']['verdict']=='pass'
                         and not record['original']['fidelity_review']['issues'])
        if fresh['source_issues']:
            record.update(state='source_needs_review',source_issues=fresh['source_issues'])
            row.update(status='quarantined',deterministic_issues=fresh['row']['deterministic_issues'])
        elif not issues and controls_valid(record['original']) and (reviewed or original_pass):
            record['state']='accepted'; row['status']='accepted'
            if reviewed:
                row.update(fidelity_review={k:last['review'][k] for k in ('verdict','issues')},
                           reviewer_output_sha256=last['output_sha256'],reviewer=config['model'])
        elif old['state'] in ('accepted','running'):
            record['state']='pending'; row['status']='quarantined'
        record['prior_recovery_snapshot_sha256']=digest((prior/'manifest.json').read_bytes())
        atomic(path,record)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','refresh','run','snapshot'))
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--prior',type=Path,help='Reuse an immutable earlier recovery snapshot with fresh checks')
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--batch-size',type=int,default=24)
    parser.add_argument('--max-batches',type=int)
    parser.add_argument('--follow',action='store_true')
    args=parser.parse_args()
    if args.workers<1 or args.batch_size<1: parser.error('Counts must be positive')
    if args.action=='snapshot' and not args.destination: parser.error('--destination is required')
    if args.action=='prepare': result=prepare(args.root,args.output,batch_size=args.batch_size,prior=args.prior)
    elif args.action=='refresh': result=refresh(args.root,args.output)
    elif args.action=='snapshot': result=snapshot(args.root,args.output,args.destination)
    else: result=run(args.root,args.output,args.workers,args.follow,args.max_batches)
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
