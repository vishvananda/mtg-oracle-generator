"""Stage only inference adapter files and declared provenance, never optimizer checkpoints."""
import argparse
import json
from pathlib import Path
import shutil
from data_utils import digest
from paths import PROJECT

def prepare(adapter,prompt,output,dataset_reference,label,evaluation=None):
    if output.exists():raise ValueError('Use a new output directory')
    summary=json.loads((adapter/'run-summary.json').read_text())
    final_test=False;report=None
    if evaluation:
        report=json.loads(evaluation.read_text())
        actual={n:digest((adapter/n).read_bytes()) for n in ('adapter_config.json','adapter_model.safetensors')}
        if (report['adapter_files']!=actual or report['training_summary_sha256']!=digest((adapter/'run-summary.json').read_bytes())
                or report['dataset_manifest_sha256']!=summary['training_identity']['dataset_manifest_sha256']):
            raise ValueError('Evaluation belongs to another model/dataset')
        final_test=report['final_test'] is True and report['split']=='test'
    output.mkdir(parents=True)
    for name in ['adapter_config.json','adapter_model.safetensors']:
        shutil.copyfile(adapter/name,output/name)
    # Allowlist metrics instead of copying arbitrary run/environment files.
    summary={k:summary[k] for k in ('config','dataset','model_revision','optimizer_steps','training_seconds',
        'peak_allocated_gib','metrics','base_eval','adapter_eval','loss_mask_verified','loss_masks_checked',
        'training_identity','validation_sample','stopped_for_training_budget','max_steps') if k in summary}
    (output/'run-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    shutil.copyfile(prompt,output/'system-prompt.txt')
    shutil.copyfile(PROJECT/'DATA_LICENSE.md',output/'DATA_LICENSE.md')
    base=summary['config']['model']
    evaluation_text=(f'Final-test mtgish acceptance: {100*report["adapter"]["mtgish_acceptance_all_cases"]:.2f}% '
        f'over {report["adapter"]["cases"]:,} scheduled cases. See `comparison.json` for the paired base score and provenance. '
        if final_test else 'Final-test generation evaluation is pending. ')
    if report: shutil.copyfile(evaluation,output/'comparison.json')
    (output/'README.md').write_text(
        f'---\nbase_model: {base}\nlibrary_name: peft\npipeline_tag: text-generation\n'
        'license: apache-2.0\nlanguage:\n  - en\n---\n\n'
        f'# {label}\n\nA rank-{summary["config"]["lora_r"]} QLoRA adapter for `{base}` '
        f'at revision `{summary["model_revision"]}`. Download the base separately.\n\n'
        f'Training dataset: {dataset_reference}. See `run-summary.json` for the exact recipe and counts. '
        f'{evaluation_text}'
        'Parser acceptance and teacher-forced token accuracy must not be read as intent fidelity or whole-card correctness.\n\n'
        'Use the Transformers/PEFT versions pinned in the companion code repository. '
        'Load the exact base with `AutoModelForCausalLM.from_pretrained(base, revision=revision)`, '
        'then `PeftModel.from_pretrained(base_model, adapter_directory)`. Use the base tokenizer '
        'and `system-prompt.txt`, disable thinking, and decode only newly generated tokens. '
        'The `src/generate.py` command in the code repository implements the NF4 evaluation recipe.\n\n'
        f'Base-model attribution: `{base}`; retain its model card and license. '
        'Training data includes third-party card content; see [attribution](DATA_LICENSE.md). '
        'No artwork or base weights are included.\n')
    files={p.name:{'sha256':digest(p.read_bytes()),'bytes':p.stat().st_size} for p in output.iterdir() if p.is_file()}
    manifest={'format_version':1,'label':label,'base_model':base,'base_revision':summary['model_revision'],
              'dataset_reference':dataset_reference,'files':files,'public_uploads':False,'final_test_evaluated':final_test}
    (output/'release-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--adapter',type=Path,required=True)
    p.add_argument('--prompt',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--dataset-reference',required=True,help='Exact dataset repo/revision or immutable snapshot identifier')
    p.add_argument('--label',default='MTG Oracle research adapter')
    p.add_argument('--evaluation',type=Path,help='comparison.json from the matching post-training report')
    a=p.parse_args()
    print(json.dumps(prepare(a.adapter,a.prompt,a.output,a.dataset_reference,a.label,a.evaluation),indent=2))
