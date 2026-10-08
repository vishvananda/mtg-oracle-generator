"""CPU Oracle embedding index and semantic-mutation diagnostics; never a reward.

Use train-reward scope for judge context. all-reference is a separate reference
index and must never enter training/reward selection for this frozen holdout.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time

from data_utils import digest,write_jsonl

MODEL='BAAI/bge-small-en-v1.5'


def document(card):
    faces=card.get('card_faces') or [card]
    return '\n//\n'.join('\n'.join([f.get('type_line',''),
        'Colors: '+''.join(f.get('colors',card.get('colors',[]))),
        'Mana: '+f.get('mana_cost',''),
        'Stats: '+('/'.join(str(f.get(k,'')) for k in ('power','toughness')) if 'power' in f else str(f.get('loyalty',f.get('defense','')))),
        f.get('oracle_text','').replace(f.get('name') or card.get('name') or 'CARDNAME','CARDNAME')]) for f in faces)


class Encoder:
    def __init__(self,revision,threads=8):
        import torch
        from transformers import AutoTokenizer,AutoModel
        self.torch=torch;torch.set_num_threads(threads)
        self.tokenizer=AutoTokenizer.from_pretrained(MODEL,revision=revision)
        self.model=AutoModel.from_pretrained(MODEL,revision=revision,trust_remote_code=False).eval()
        self.long_documents=0

    def encode(self,texts,batch_size=64):
        import numpy as np
        torch=self.torch;parts=[];owners=[]
        for i,text in enumerate(texts):
            ids=self.tokenizer(text,add_special_tokens=False,verbose=False)['input_ids']
            self.long_documents+=len(ids)>480
            for start in range(0,max(1,len(ids)),448):
                window=ids[start:start+480]
                parts.append({'input_ids':self.tokenizer.build_inputs_with_special_tokens(window)})
                owners.append(i)
                if start+480>=len(ids):break
        vectors=[]
        with torch.inference_mode():
            for start in range(0,len(parts),batch_size):
                batch=self.tokenizer.pad(parts[start:start+batch_size],padding=True,return_tensors='pt')
                # BGE's documented CLS pooling and L2 normalization.
                output=self.model(**batch).last_hidden_state[:,0]
                vectors.append(torch.nn.functional.normalize(output,p=2,dim=1).cpu().numpy())
        chunks=np.concatenate(vectors);result=np.zeros((len(texts),chunks.shape[1]),dtype=np.float32)
        counts=np.zeros(len(texts),dtype=np.float32)
        for owner,vector in zip(owners,chunks):result[owner]+=vector;counts[owner]+=1
        result/=counts[:,None]
        result/=np.maximum(np.linalg.norm(result,axis=1,keepdims=True),1e-12)
        return result


def allowed_ids(dataset,scope):
    manifest=json.loads((dataset/'manifest.json').read_text())
    split_map=dataset/'split-map.json'
    if digest(split_map.read_bytes())!=manifest['files']['split-map.json']['sha256']:
        raise ValueError('Split map changed')
    splits=json.loads(split_map.read_text())
    if scope not in ('train-reward','all-reference'):raise ValueError('Unknown index scope')
    return {key for key,split in splits.items() if scope=='all-reference' or split=='train'},splits


def build(oracle,dataset,output,scope,revision):
    import numpy as np
    if output.exists():raise ValueError('Choose a new immutable index directory')
    if len(revision)!=40:raise ValueError('Pin the embedding model to a commit SHA')
    ids,splits=allowed_ids(dataset,scope);rows=[]
    with gzip.open(oracle,'rt') as stream:
        for line in stream:
            card=json.loads(line)
            if card.get('oracle_id') in ids:
                rows.append({'oracle_id':card['oracle_id'],'name':card['name'],'split':splits[card['oracle_id']],
                    'type_line':card.get('type_line',''),'document':document(card)})
    if len({r['oracle_id'] for r in rows})!=len(rows) or {r['oracle_id'] for r in rows}!=ids:
        raise ValueError('Index must contain exactly one reference per allowed Oracle ID')
    rows.sort(key=lambda r:r['oracle_id']);output.mkdir(parents=True)
    started=time.monotonic();encoder=Encoder(revision)
    embeddings=[]
    for start in range(0,len(rows),512):
        embeddings.append(encoder.encode([r['document'] for r in rows[start:start+512]]))
        print(json.dumps({'embedded':min(start+512,len(rows)),'total':len(rows)}),flush=True)
    matrix=np.concatenate(embeddings);np.save(output/'vectors.npy',matrix)
    write_jsonl(output/'cards.jsonl',rows)
    metadata={'model':MODEL,'revision':revision,'scope':scope,'cards':len(rows),'dimensions':int(matrix.shape[1]),
        'holdout_cards_indexed':sum(r['split']!='train' for r in rows),
        'excluded_source_cards':len(splits)-len(rows),'dataset_manifest_sha256':digest((dataset/'manifest.json').read_bytes()),
        'oracle_snapshot_sha256':digest(oracle.read_bytes()),'encoder_source_sha256':digest(Path(__file__).read_bytes()),
        'vector_sha256':digest((output/'vectors.npy').read_bytes()),'cards_sha256':digest((output/'cards.jsonl').read_bytes()),
        'long_documents_chunked':encoder.long_documents,'chunking':'480 tokens, 32 overlap; mean chunk vectors, then L2 normalize',
        'seconds':time.monotonic()-started,'embedding_reward_weight':0,
        'versions':{p:importlib.metadata.version(p) for p in ('torch','transformers','numpy')}}
    (output/'manifest.json').write_text(json.dumps(metadata,indent=2)+'\n');return metadata


def probe(index,cases_path,output):
    import numpy as np
    metadata=json.loads((index/'manifest.json').read_text())
    for name,key in [('vectors.npy','vector_sha256'),('cards.jsonl','cards_sha256')]:
        if digest((index/name).read_bytes())!=metadata[key]:raise ValueError('Index hash mismatch')
    matrix=np.load(index/'vectors.npy',mmap_mode='r');cards=[json.loads(l) for l in (index/'cards.jsonl').open()]
    cases=[json.loads(l) for l in cases_path.open()]
    encoder=Encoder(metadata['revision']);vectors=encoder.encode([document(c['candidate']) for c in cases])
    reference=encoder.encode([document(c['reference']) for c in cases]);results=[]
    for c,v,ref in zip(cases,vectors,reference):
        scores=matrix@v;top=np.argsort(scores)[-3:][::-1]
        results.append({'id':c['id'],'category':c['category'],'expected':c['expected'],
            'cosine_to_faithful_reference':float(v@ref),'nearest':[{'oracle_id':cards[i]['oracle_id'],
                'name':cards[i]['name'],'cosine':float(scores[i])} for i in top]})
    if output.exists():raise ValueError('Choose a new probe output directory')
    output.mkdir(parents=True);write_jsonl(output/'similarities.jsonl',results)
    summary={'index_manifest_sha256':digest((index/'manifest.json').read_bytes()),'cases_sha256':digest(cases_path.read_bytes()),
        'cases':len(cases),'wrong_candidates_above_0_9_cosine_to_reference':[r['id'] for r in results if r['expected']=='fail' and r['cosine_to_faithful_reference']>.9],
        'interpretation':'Similarity diagnoses neighborhood/style; neither numeric threshold nor nearest-card agreement proves intent or legality.'}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    b=sub.add_parser('build')
    for name in ('oracle','dataset','output'):b.add_argument('--'+name,type=Path,required=True)
    b.add_argument('--scope',choices=['train-reward','all-reference'],default='train-reward');b.add_argument('--revision',required=True)
    q=sub.add_parser('probe')
    for name in ('index','cases','output'):q.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    result=build(a.oracle,a.dataset,a.output,a.scope,a.revision) if a.action=='build' else probe(a.index,a.cases,a.output)
    print(json.dumps(result,indent=2))
