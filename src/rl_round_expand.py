"""Prepare disjoint larger preference batches and merge reviewed pairs."""
import argparse,json,re
from collections import Counter
from pathlib import Path

from data_utils import digest,write_jsonl
from rl_data import select,checked_file,sha_file
from rl_round_prepare import write_pilot


def prepare(root,dataset,old_pilot,index=1,count=2048):
    destination=root/f'batch-{index:02d}'
    if destination.exists():raise ValueError('Expansion batch already exists')
    heldout=set(json.loads((root/'reserved-families.json').read_text())['source_group_ids'])
    old=json.loads((old_pilot/'manifest.json').read_text())
    excluded=heldout|{c['source_group_id'] for c in map(json.loads,checked_file(old_pilot,'cases.jsonl',old).read_text().splitlines())}
    for previous in root.glob('batch-*/pilot/cases.jsonl'):
        excluded.update(c['source_group_id'] for c in map(json.loads,previous.read_text().splitlines()))
    dm=json.loads((dataset/'manifest.json').read_text());source=checked_file(dataset,'train.jsonl',dm)
    with source.open() as stream:
        cases,_=select((r for r in map(json.loads,stream) if r['source_group_id'] not in excluded),count,f'rl-round2-batch-{index}')
    names=[];modified=0
    for case in cases:
        raw=case['request'];case['original_request']=raw
        marker=int(digest(['metadata-variation',case['id']])[:8],16)
        multi=bool(case['reference'].get('card_faces'))
        if marker%4==0 and not multi and 'name' not in case['explicit_metadata'] and case['reference'].get('name','') not in raw:
            first=['Amber','Cinder','Glass','Ivory','Mire','Moss','Opal','Silver','Thorn','Tidal','Umber','Verdant','Ash','Dawn','Dusk','Frost']
            last=['warden','caller','weaver','keeper','watcher','seeker','binder','singer','runner','speaker','guard','mender','shaper','walker','tender','witness']
            place=['Larkfen','Duskfall','Brightmarsh','Ashgrove','Mistvale','Glassreach','Thornhollow','Stillwater','Emberfold','Cloudrest','Mossdeep','Saltmere','Starfen','Dawnreach','Shadebrook','Windmere']
            name=first[marker%16]+last[(marker//16)%16]+' of '+place[(marker//256)%16]
            # Names need not be globally unique, but every literal request must be honored.
            case['request']+=f' Name the card "{name}".';case['explicit_metadata']['name']=name
            if 'rarity' not in case['explicit_metadata'] and not re.search(r'\b(common|uncommon|rare|mythic)\b',raw,re.I):
                rarity=['uncommon','rare','mythic'][marker%3]
                case['request']+=f' Its rarity should be {rarity}.';case['explicit_metadata']['rarity']=rarity
            modified+=1;names.append(name)
        case['request_derivation']='Source description plus explicit editable metadata exercise' if case['request']!=raw else 'Unchanged source description'
    destination.mkdir(parents=True)
    result=write_pilot(destination/'pilot',cases,(root/'serving-system.txt').read_text(),sha_file(dataset/'manifest.json'),
        reserved_families_sha256=sha_file(root/'reserved-families.json'),production_system_sha256=sha_file(root/'serving-system.txt'),
        explicit_metadata_exercises=modified,original_dataset_requests=count-modified,
        excluded_previous_and_evaluation_families=len(excluded))
    return result


def merge(root,directories,output,maximum=2000):
    if output.exists():raise ValueError('Fresh merged review required')
    heldout=set(json.loads((root/'reserved-families.json').read_text())['source_group_ids'])
    examples=[];seen=set();sources=[]
    for directory in directories:
        review=json.loads((directory/'review.json').read_text());path=directory/'preferences.jsonl'
        if not review['independent_review'] or review['approved_pairs_sha256']!=sha_file(path):raise ValueError('Unverified preferences')
        rows=list(map(json.loads,path.read_text().splitlines()))
        audit=[r for r in map(json.loads,(directory/'audit.jsonl').read_text().splitlines()) if r['approved']]
        if len(rows)!=len(audit) or len(rows)!=review['approved_pairs']:raise ValueError('Pair audit alignment differs')
        for row,meta in zip(rows,audit):
            group=meta['source_group_id']
            if group in heldout:raise ValueError('Evaluation family entered training')
            if group in seen:raise ValueError('Repeated training family')
            seen.add(group);examples.append((digest(['pair-order',group]),row,meta))
        sources.append({'review_sha256':sha_file(directory/'review.json'),'pairs_sha256':sha_file(path),'pairs':len(rows)})
    examples.sort(key=lambda item:item[0])
    # A full effective-batch multiple avoids a final update spilling into a
    # second data-loader epoch in the pinned Trainer version.
    selected=min(maximum,len(examples))//8*8
    examples=examples[:selected]
    output.mkdir(parents=True)
    file=write_jsonl(output/'preferences.jsonl',[row for _,row,_ in examples])
    write_jsonl(output/'audit.jsonl',[meta for _,_,meta in examples])
    value={'independent_review':True,'independence_note':'Blinded separate Sol calls with Luna criticism; not independent human labels',
        'approved_pairs':len(examples),'approved_pairs_sha256':file['sha256'],'source_reviews':sources,
        'chosen_origins':dict(Counter(meta['chosen_origin'] for _,_,meta in examples)),
        'reserved_families_sha256':sha_file(root/'reserved-families.json'),'final_test_used':False,
        'one_pair_per_family':True,'format':'TRL conversational prompt/chosen/rejected'}
    (output/'review.json').write_text(json.dumps(value,indent=2)+'\n');return value


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    prep=sub.add_parser('prepare')
    for n in ('root','dataset','old-pilot'):prep.add_argument('--'+n,type=Path,required=True)
    prep.add_argument('--index',type=int,default=1);prep.add_argument('--count',type=int,default=2048)
    join=sub.add_parser('merge');join.add_argument('--root',type=Path,required=True);join.add_argument('--output',type=Path,required=True)
    join.add_argument('--review',type=Path,action='append',required=True);join.add_argument('--maximum',type=int,default=2000)
    a=p.parse_args()
    result=prepare(a.root,a.dataset,a.old_pilot,a.index,a.count) if a.action=='prepare' else merge(a.root,a.review,a.output,a.maximum)
    print(json.dumps(result,indent=2))
