"""Pure-text local Codex invocation with durable receipt and no automatic retries."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from data_utils import digest


def invoke(model, payload, schema, output):
    output.mkdir(parents=True,exist_ok=False)
    (output/'input.json').write_text(json.dumps(payload,ensure_ascii=False))
    (output/'schema.json').write_text(json.dumps(schema))
    prompt='Pure text task. Do not use tools, read files, browse, delegate, or change files. Use only this input and return the requested JSON.\n'+json.dumps(payload,ensure_ascii=False)
    command=['codex','exec','--model',model,'--config','model_reasoning_effort=medium',
             '--sandbox','read-only','--ignore-user-config','--skip-git-repo-check','--ephemeral',
             '--config','shell_environment_policy.inherit=none','--output-schema',str(output/'schema.json'),
             '--output-last-message',str(output/'output.json'),'--color','never','--json','-']
    start=time.monotonic()
    receipt={'model_requested':model,'reasoning_effort':'medium','prompt_sha256':digest(prompt.encode()),
             'started_at':datetime.now(timezone.utc).isoformat(),'timeout_seconds':900,'attempts':1}
    with (output/'events.jsonl').open('w') as events,(output/'stderr.log').open('w') as errors:
        try:
            process=subprocess.run(command,input=prompt,text=True,stdout=events,stderr=errors,cwd=output,timeout=900)
            receipt['returncode']=process.returncode
        except subprocess.TimeoutExpired: receipt.update(timed_out=True,returncode=-1)
    receipt['elapsed_seconds']=time.monotonic()-start
    receipt['usage']=[]; receipt['tool_events']=[]
    for line in (output/'events.jsonl').read_text().splitlines():
        try: event=json.loads(line)
        except json.JSONDecodeError: continue
        if event.get('type')=='turn.completed': receipt['usage'].append(event.get('usage'))
        kind=event.get('item',{}).get('type')
        if kind not in (None,'reasoning','agent_message'): receipt['tool_events'].append(kind)
    value=None
    if receipt['returncode']==0 and not receipt['tool_events']:
        try:
            value=json.loads((output/'output.json').read_text())
            Draft202012Validator(schema).validate(value)
            receipt['output_sha256']=digest((output/'output.json').read_bytes())
        except (ValueError,OSError,ValidationError) as e:
            receipt['error']=str(e)
            value=None
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    if value is None: raise ValueError('Codex generation failed; inspect '+str(output/'receipt.json'))
    return value,receipt
