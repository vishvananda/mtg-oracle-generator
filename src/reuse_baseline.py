"""Verify a completed base arm before reusing it with a later adapter."""
import hashlib
import json

from data_utils import digest
from evaluation_selection import POLICY,select_cases
from hub_dataset import verify
from run_package import verify_package


def baseline_files(package,dataset,stage,predictions):
    manifest=verify_package(package);verify(dataset)
    if stage not in ('development','validation','test'): raise ValueError('Baseline reuse requires evaluation')
    split='validation' if stage=='development' else stage
    if predictions.name!=f'base-{split}.jsonl': raise ValueError('Expected the base prediction filename')
    recipe=json.loads((package/'recipe.json').read_text());config=json.loads((package/'train-config.json').read_text())
    selection={'policy':POLICY,'limit':recipe['evaluation']['development_cases']} if stage=='development' else None
    cases=select_cases([json.loads(line) for line in (dataset/f'{split}.messages.jsonl').read_text().splitlines()],selection)
    identifiers=[r['example_id'] for r in cases]
    expected={'dataset_manifest_sha256':digest((dataset/'manifest.json').read_bytes()),'split':split,
        'model':config['model'],'model_revision':config['model_revision'],'adapter_files':{},
        'config_sha256':digest((package/'train-config.json').read_bytes()),
        'config_canonical_sha256':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),'seed':config['seed'],
        'code_files':{n:digest((package/n).read_bytes()) for n in ('generate.py','generate.py.lock','prediction_io.py')},
        'selection':selection,'selection_code_sha256':digest((package/'evaluation_selection.py').read_bytes()),
        'decoding':{'do_sample':False,'max_new_tokens':recipe['evaluation']['max_new_tokens'],'enable_thinking':False,
                    'batch_size':recipe['evaluation']['batch_size']},'backend':'transformers NF4, one GPU',
        'scheduled_cases':len(identifiers),'scheduled_ids_sha256':digest(identifiers)}
    if expected['dataset_manifest_sha256']!=manifest['dataset_manifest_sha256']: raise ValueError('Baseline dataset differs')
    request=predictions.with_suffix('.request.json');receipt=predictions.with_suffix('.manifest.json')
    if json.loads(request.read_text())!=expected: raise ValueError('Baseline generation request differs')
    expected_receipt={**expected,'complete':True,'cases':len(identifiers),'predictions_sha256':digest(predictions.read_bytes())}
    if json.loads(receipt.read_text())!=expected_receipt: raise ValueError('Baseline is incomplete or modified')
    saved=[json.loads(line)['example_id'] for line in predictions.read_text().splitlines()]
    if len(saved)!=len(set(saved)) or set(saved)!=set(identifiers): raise ValueError('Baseline case IDs differ')
    return [predictions.name,request.name,receipt.name]
