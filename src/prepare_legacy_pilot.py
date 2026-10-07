"""Package the exact historical 16k dataset without adding new names/rarity targets."""
import argparse
import json
from pathlib import Path
import shutil
from data_utils import digest,write_jsonl
from paths import PROJECT
from prepare_dataset import checked_file,audit
from train_qlora import preflight

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():p.error('Use a new output directory')
    original=json.loads((a.input/'manifest.json').read_text())
    all_rows=[];payload={};prompts=set();files={}
    for split in ('train','validation'):
        rows=[json.loads(line) for line in checked_file(a.input,split+'.jsonl',original).read_text().splitlines()]
        sft=[json.loads(line) for line in checked_file(a.input,split+'.sft.jsonl',original).read_text().splitlines()]
        if len(rows)!=len(sft):raise ValueError('Rows and SFT disagree')
        for row,record in zip(rows,sft):
            if row['split']!=split or row['description']!=record['prompt'][1]['content']:
                raise ValueError('Description alignment failed')
            if row['target']!=json.loads(record['completion'][0]['content']):raise ValueError('Target alignment failed')
            prompts.add(record['prompt'][0]['content'])
        all_rows.extend(rows);payload[split]=(rows,sft)
    if len(prompts)!=1:raise ValueError('Expected one frozen prompt')
    checks=audit(all_rows)
    if original['checks']['description_overlap']:raise ValueError('Description overlap')
    a.output.mkdir(parents=True)
    for split,(rows,sft) in payload.items():
        for suffix in ('.jsonl','.sft.jsonl'):
            name=split+suffix;shutil.copyfile(a.input/name,a.output/name);files[name]=original['files'][name]
        files[split+'.messages.jsonl']=write_jsonl(a.output/(split+'.messages.jsonl'),[
            {'example_id':r['example_id'],'source_group_id':r['source_group_id'],'style':r['style'],
             'messages':v['prompt']+v['completion']} for r,v in zip(rows,sft)])
    (a.output/'system-prompt.txt').write_text(prompts.pop())
    shutil.copyfile(a.input/'draft-schema.json',a.output/'draft-schema.json')
    shutil.copyfile(PROJECT/'DATA_LICENSE.md',a.output/'DATA_LICENSE.md')
    for name in ('system-prompt.txt','draft-schema.json','DATA_LICENSE.md'):
        files[name]={'sha256':digest((a.output/name).read_bytes())}
    result={'format_version':1,'release':'pilot-16k-exact','status':'partial','generation_complete':False,
            'counts':{s:len(v[0]) for s,v in payload.items()},'checks':checks,'files':files,
            'names_and_rarity':False,'original_manifest_sha256':digest((a.input/'manifest.json').read_bytes()),
            'test_evaluated':False,'human_reviewed':False,'public_uploads':False}
    (a.output/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    configs=''
    for name,suffix in [('messages','.messages.jsonl'),('sft','.sft.jsonl'),('records','.jsonl')]:
        configs+=f'  - config_name: {name}\n'+('    default: true\n' if name=='messages' else '')+'    data_files:\n'
        for split in payload:configs+=f'      - split: {split}\n        path: {split}{suffix}\n'
    (a.output/'README.md').write_text(
        '---\nlanguage: [en]\nlicense: other\nlicense_name: mixed-rights-card-data\nlicense_link: DATA_LICENSE.md\n'
        'task_categories: [text-generation]\nconfigs:\n'+configs+'---\n\n# Exact 16k training pilot\n\n'
        'This is the **original** 16,000 train / 480 validation snapshot behind the measured Qwen3 4B pilot. '
        'The original training and provenance files are byte-identical; their hashes are in `manifest.json`. '
        'New `messages` files are an equivalent chat-format view.\n\n'
        'It predates the named/rarity-enriched release and has no final test partition. '
        'Do not use this legacy snapshot to assess whether the new naming/rarity policy works. '
        'Descriptions are automatically generated/reviewed; no human certification is claimed. '
        'Related source groups retain their original split. See [data attribution](DATA_LICENSE.md).\n')
    preflight(a.output,{'model':'legacy','model_revision':'recorded in model card'})
    print(json.dumps({'counts':result['counts'],'checks':checks,'exact_original_files':True},indent=2))
