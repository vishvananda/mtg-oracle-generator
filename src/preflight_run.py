"""CPU-only length and field audit before renting a GPU. No rows are truncated."""
import argparse
from collections import Counter
import json
from pathlib import Path
from data_utils import digest
from train_qlora import preflight


def inspect(dataset, config, tokenizer=None):
    result=preflight(dataset,config)
    result.update(dataset_manifest_sha256=digest((dataset/'manifest.json').read_bytes()),
                  config_sha256=digest(config),tokenizer_checked=tokenizer is not None,splits={})
    for split in ('train','validation'):
        counts=Counter();lengths=[];examples=[]
        for line in (dataset/(split+'.sft.jsonl')).open():
            row=json.loads(line);counts['rows']+=1
            target=json.loads(row['completion'][0]['content'])
            counts['named']+=bool(target.get('name'));counts['with_rarity']+=bool(target.get('rarity'))
            faces=target.get('card_faces') or [target]
            walkers=[f for f in faces if 'Planeswalker' in f.get('type_line','')]
            counts['planeswalker_rows']+=bool(walkers)
            counts['planeswalker_faces']+=len(walkers)
            counts['planeswalker_faces_with_loyalty']+=sum('loyalty' in f for f in walkers)
            if tokenizer:
                ids=tokenizer.apply_chat_template(row['prompt']+row['completion'],tokenize=True,enable_thinking=False)
                lengths.append(len(ids))
                if len(ids)>config['max_length']:
                    counts['over_context']+=1
                    if len(examples)<10: examples.append({'row':counts['rows']-1,'tokens':len(ids)})
        value=dict(counts)
        if lengths:
            lengths.sort()
            value.update(tokens=sum(lengths),max_tokens=lengths[-1],
                p50_tokens=lengths[len(lengths)//2],p95_tokens=lengths[int((len(lengths)-1)*.95)],
                p99_tokens=lengths[int((len(lengths)-1)*.99)],over_context_examples=examples)
        result['splits'][split]=value
    result['ready']=not any(v.get('over_context',0) for v in result['splits'].values()) if tokenizer is not None else None
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,required=True);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--tokenize',action='store_true')
    a=p.parse_args();config=json.loads(a.config.read_text());tokenizer=None
    if a.tokenize:
        from transformers import AutoTokenizer
        tokenizer=AutoTokenizer.from_pretrained(config['model'],revision=config['model_revision'])
    result=inspect(a.dataset,config,tokenizer)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    if result['ready'] is False: raise SystemExit(2)
