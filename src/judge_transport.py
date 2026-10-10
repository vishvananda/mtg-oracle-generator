"""Blind semantic labels and constrain response IDs without varying the cached schema."""
import json
from codex_cached_batch import invoke
from data_utils import digest
from rl_rewards import judge_payload,judge_schema,align_judgments


def invoke_judge(model,cases,output,*,prefix_root,reasoning_effort='medium'):
    if not 1<=len(cases)<=9 or len({c['id'] for c in cases})!=len(cases):
        raise ValueError('Judge batches need one to nine unique cases')
    labels=[f'c{i}' for i in range(9)]
    blinded=[{**case,'id':label} for case,label in zip(cases,labels)]
    payload=judge_payload(blinded);schema=judge_schema()
    schema['properties']['reviews']['anyOf'][1]['items']['properties']['id']={'type':'string','enum':labels}
    identity={'cases_sha256':digest(cases),'model':model,'effort':reasoning_effort,
              'labels':{label:case['id'] for case,label in zip(cases,labels)}}
    if output.exists():
        saved=json.loads((output/'case-map.json').read_text())
        receipt=json.loads((output/'receipt.json').read_text())
        if saved!=identity or receipt.get('error') or json.loads((output/'input.json').read_text())!=payload:
            raise ValueError('Existing judge batch is incomplete or belongs to different cases')
        if receipt['output_sha256']!=digest((output/'output.json').read_bytes()):raise ValueError('Judge output changed')
        value=json.loads((output/'output.json').read_text())
    else:
        value,receipt=invoke(model,payload,schema,output,prefix_root=prefix_root,reasoning_effort=reasoning_effort)
        (output/'case-map.json').write_text(json.dumps(identity,indent=2)+'\n')
    ordered=align_judgments(value,blinded)
    reviews=[{**ordered[label],'id':case['id']} for case,label in zip(cases,labels)]
    return {'reviews':reviews},receipt
