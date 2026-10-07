"""Stage only inference adapter files and declared provenance, never optimizer checkpoints."""
import argparse
import json
from pathlib import Path
import shutil
from data_utils import digest
from paths import PROJECT

def prepare(adapter,prompt,output,dataset_reference,label):
    if output.exists():raise ValueError('Use a new output directory')
    summary=json.loads((adapter/'run-summary.json').read_text())
    output.mkdir(parents=True)
    for name in ['adapter_config.json','adapter_model.safetensors']:
        shutil.copyfile(adapter/name,output/name)
    # Allowlist metrics instead of copying arbitrary run/environment files.
    summary={k:summary[k] for k in ('config','dataset','model_revision','optimizer_steps','training_seconds',
        'peak_allocated_gib','metrics','base_eval','adapter_eval','loss_mask_verified','loss_masks_checked')}
    (output/'run-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    shutil.copyfile(prompt,output/'system-prompt.txt')
    shutil.copyfile(PROJECT/'DATA_LICENSE.md',output/'DATA_LICENSE.md')
    base=summary['config']['model']
    (output/'README.md').write_text(
        f'---\nbase_model: {base}\nlibrary_name: peft\npipeline_tag: text-generation\n'
        'license: apache-2.0\nlanguage:\n  - en\n---\n\n'
        f'# {label}\n\nA rank-{summary["config"]["lora_r"]} QLoRA adapter for `{base}` '
        f'at revision `{summary["model_revision"]}`. Download the base separately.\n\n'
        f'Training dataset: {dataset_reference}. See `run-summary.json` for the exact recipe and counts. '
        'The 16k pilot predates name/rarity enrichment and is not trained on the newer 19k release. '
        'It has known intent-preservation failures. Final-test generation accuracy is pending; '
        'teacher-forced token accuracy must not be read as whole-card correctness.\n\n'
        'Use the Transformers/PEFT versions pinned in the companion code repository. '
        'Load the exact base with `AutoModelForCausalLM.from_pretrained(base, revision=revision)`, '
        'then `PeftModel.from_pretrained(base_model, adapter_directory)`. Use the base tokenizer '
        'and `system-prompt.txt`, disable thinking, and decode only newly generated tokens. '
        'The `src/generate.py` command in the code repository implements the NF4 evaluation recipe.\n\n'
        'Base-model attribution: Qwen team, Qwen3-4B-Instruct-2507, Apache 2.0. '
        'Training data includes third-party card content; see [attribution](DATA_LICENSE.md). '
        'No artwork or base weights are included.\n')
    files={p.name:{'sha256':digest(p.read_bytes()),'bytes':p.stat().st_size} for p in output.iterdir() if p.is_file()}
    manifest={'format_version':1,'label':label,'base_model':base,'base_revision':summary['model_revision'],
              'dataset_reference':dataset_reference,'files':files,'public_uploads':False,'final_test_evaluated':False}
    (output/'release-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--adapter',type=Path,required=True)
    p.add_argument('--prompt',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--dataset-reference',required=True,help='Exact dataset repo/revision or immutable snapshot identifier')
    p.add_argument('--label',default='MTG Oracle Qwen3 4B — 16k pilot')
    a=p.parse_args()
    print(json.dumps(prepare(a.adapter,a.prompt,a.output,a.dataset_reference,a.label),indent=2))
