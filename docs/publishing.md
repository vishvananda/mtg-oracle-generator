# Publishing code, data and adapters

Keep three artifacts separate: GitHub code/docs, HF datasets, and HF model
adapters. `dist/`, `artifacts/`, `data/`, GPU checkpoints and model weights are
ignored by Git. Dataset archives are local upload staging files; distribute
prepared datasets through Hugging Face, not GitHub commits or release assets.
GitHub retains small examples, evaluation reports, and provenance manifests.
The current local preparation is recorded in
[`release-status.json`](../release-status.json); URLs are null until upload.

## Dataset

Use `prepare_dataset.py` on reviewed exports as described in [dataset.md](dataset.md).
Its output already contains an HF dataset card with `messages`, `sft` and
`records` configurations, source provenance and payload SHA-256 hashes.
For the exact historical model, `prepare_legacy_pilot.py` packages the original
16k snapshot without changing its inputs or targets. The later names/rarity
pilot is a separate release, not that model's training data.

```bash
python src/hub_dataset.py verify --directory dist/oracle-v1
python src/hub_dataset.py upload --repo-id "$MTG_DATASET" --directory dist/oracle-v1
```

Upload uses the [HF Hub API](https://huggingface.co/docs/huggingface_hub/guides/upload)
and includes only manifest-listed files plus the dataset card. It prints the
commit SHA; record that SHA in the model card and release status. It creates a
public repository unless `--private` is supplied; it does not change the
visibility of an existing repository. Configure account access separately.

Consumers can use the download command in the root README or standard HF tools:

```python
import os
from datasets import load_dataset

dataset = load_dataset(
    os.environ["MTG_DATASET"], "messages",
    revision=os.environ["MTG_DATASET_REVISION"],
)
# dataset["train"] retains the original source-group split.
```

Do not randomly split individual descriptions. Keep the complete provenance
records available even if training uses only the chat configuration. Full bulk
downloads, worker thread storage, credentials and raw environment logs are not
publication inputs. Snapshot/augmentation hashes belong in release provenance;
no private editor repository is required to consume the data.

## Adapter and training evidence

```bash
python src/prepare_model_release.py --adapter runs/pilot \
  --prompt data/pilot-16k/system-prompt.txt --output dist/pilot-model \
  --dataset-reference "$MTG_DATASET@$MTG_DATASET_REVISION"
```

This stages adapter safetensors/config, the frozen prompt, selected metrics,
attribution and an HF model card. It excludes base weights, optimizer states,
private logs and serving caches. The supplied card describes the existing 16k
pilot; update its release label and limitations when packaging a different run.
Copy final-test evidence only after the final evaluation exists.

For the staged model, upload the explicit release allowlist:

```python
import json
from pathlib import Path
from huggingface_hub import HfApi

root = Path("dist/pilot-model")
manifest = json.loads((root / "release-manifest.json").read_text())
api = HfApi()
repo_id = "YOUR_ACCOUNT/mtg-oracle-qwen3-4b-pilot"
api.create_repo(repo_id, repo_type="model", private=False, exist_ok=True)
commit = api.upload_folder(
    repo_id=repo_id, repo_type="model", folder_path=root,
    allow_patterns=[*manifest["files"], "release-manifest.json"],
)
print(commit.oid)
```

Record model/dataset release commits and direct URLs in `release-status.json`.
Keep the base model ID and revision explicit: an adapter cannot run alone.
Original code is MIT; the base-model license and third-party card attribution
are separate. See [DATA_LICENSE.md](../DATA_LICENSE.md).

## GitHub repository

From this standalone repository, after tests pass:

```bash
git status --short
gh repo create vishvananda/mtg-oracle-generator --public --source=. \
  --description "Natural-language MTG card descriptions, Oracle text fine-tuning, and mtgish evaluation" \
  --remote=origin --push
```

Do not run that command from the parent editor repository. The code repo includes
the measured pilot graph, CSV, training metrics, saved development generations
and parser diagnostics. The full-corpus final report stays explicitly pending
until that run is finished. GitHub Actions runs the portable CPU tests and
regenerates the pilot plot; it does not call teacher models or launch GPU jobs.
