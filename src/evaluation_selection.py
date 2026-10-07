"""A frozen development panel; no selection is allowed for the final test."""
from collections import defaultdict,deque
import json
from data_utils import digest

POLICY='development-groups-styles-v1'


def evaluation_allowed(summary,split,selection=None):
    if split=='test' and selection: raise ValueError('Final test cannot be sampled')
    complete=not summary['stopped_for_training_budget'] and summary['metrics']['epoch']>=summary['config']['epochs']-1e-6
    if summary['max_steps']!=-1 or (not complete and not (split=='validation' and selection)):
        raise ValueError('Incomplete training may only be scored on a declared development panel; smoke adapters are excluded')
    return complete


def select_cases(rows,selection=None):
    if not selection: return rows
    if selection.get('policy')!=POLICY: raise ValueError('Unknown evaluation selection policy')
    limit=selection['limit']
    if type(limit) is not int or limit<1: raise ValueError('Positive development sample limit required')
    ordered=sorted(rows,key=lambda r:digest(r['example_id']))
    chosen=[];groups=set();ids=set()
    def add(row):
        group=row['source_group_id']
        if len(chosen)>=limit or group in groups: return False
        chosen.append(row);groups.add(group);ids.add(row['example_id']);return True
    def faces(row):
        target=row.get('target') or json.loads(row['messages'][-1]['content'])
        return target.get('card_faces') or [target]
    for predicate,quota in ((lambda r:any('Planeswalker' in f.get('type_line','') for f in faces(r)),limit//8),
                            (lambda r:len(faces(r))>1,limit//16)):
        count=0
        for row in ordered:
            if count>=quota: break
            if predicate(row) and add(row): count+=1
    buckets=defaultdict(deque)
    for row in ordered: buckets[row['style']].append(row)
    while len(chosen)<limit and any(buckets.values()):
        for style in sorted(buckets):
            if len(chosen)>=limit: break
            while buckets[style]:
                if add(buckets[style].popleft()): break
    if len(chosen)<min(limit,len(rows)):
        # Tiny fixtures/releases may not have enough distinct source groups.
        for row in ordered:
            if len(chosen)>=limit: break
            if row['example_id'] not in ids: chosen.append(row);ids.add(row['example_id'])
    return sorted(chosen,key=lambda r:r['example_id'])
