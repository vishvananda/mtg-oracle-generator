# Full training and post-training workflow

The current v2 experiment uses [a small, coverage-protected two-way split](two-way-split.md): 316,372 training descriptions and 400 final-test cases, with no held-out validation split. Earlier v1 and staged-pilot procedures below retain their original three-way protocols.

The recipe is [`configs/full-run.json`](../configs/full-run.json). Each stage is
separate: freeze data → CPU preflight → GPU smoke → training → validation →
freeze the selected model/settings → final test → reports → release/serving
candidate. Preparation never launches GPU jobs or replaces the live model.
Prepared datasets, predictions, and weights belong on Hugging Face; keep them
out of Git. The repository contains code, recipes, documentation, and measured
aggregate plots/reports.

## Start with a checkpoint experiment

One full epoch is one pass through roughly 250,000 descriptions. It is not
necessary to finish it before checking model quality. `configs/staged-run.json`
keeps the full-epoch learning-rate schedule but stops each training segment
after a one-hour training-loop budget (90-minute hard job timeout). Resume its
optimizer checkpoint to continue the same epoch and data order.

The staged experiment has a **$20 aggregate ledger**, one attempt per job, and
no automatic continuation after its first quality report. At the current A100
price, the initial smoke (30-minute cap), training segment (90-minute cap), and
development generation (30-minute cap) reserve approximately **$6.25 total**.
The ledger conservatively counts the full timeout cost even if jobs finish
early. A failed or ambiguous submission retains its reservation for inspection.
The controller refuses an increased live price above its per-job caps.

```bash
python src/setup_mtgish.py
python -m pip install datasets==4.1.1 transformers==4.56.2 tokenizers==0.22.2
TOKENIZERS_PARALLELISM=false python src/prepared_training.py --dataset data/oracle \
  --config configs/qwen3-4b-full.json --output runs/prepared-training --workers 8
python src/run_package.py --dataset data/oracle --recipe configs/staged-run.json \
  --prepared runs/prepared-training --output runs/staged-package
.hf-jobs/bin/python src/run_first_segment.py --package runs/staged-package \
  --dataset data/oracle --output runs/segment-1 --cpu-python .venv/bin/python
```

Only the final controller command submits paid jobs. It runs smoke → one training segment →
paired base/adapter development generation → CPU parser/loss reports, then
stops at `awaiting_quality_review`. Read `status.json` and `budget.json` for
progress and reserved cost. There is no automatic retry or model promotion.
When replacing an experiment, pass `--ledger path/to/existing/budget.json` to
the controller so earlier reservations remain inside the same total budget.

Prepare tokens on a CPU host before allocating the GPU. Preparation uses batched
tokenization across eight processes and checks every completion mask against its
assistant JSON. It keeps the original example order and fixed validation sample.
The compressed cache binds the source dataset hashes, pinned tokenizer revision,
tokenizer library versions, preprocessing code, maximum length, and validation
sample policy. A changed input is rejected; use a new cache directory to rebuild.
Training batch size can change without retokenizing.

GPU jobs verify and unpack that cache onto local disk, then memory-map the Arrow
files using `load_from_disk`. They skip JSON ingestion, tokenization, redundant
truncation, and full-corpus target decoding. The actual training collator's
completion-only labels are still checked. Cached lengths also avoid a full
dataset scan when constructing each length-grouped sampler. No final-test data
enters the cache. See the [Datasets processing guide](https://huggingface.co/docs/datasets/process)
for batched multiprocessing and saving/reloading Arrow datasets.

Logs now identify data loading, model loading, collator setup, baseline
generation/evaluation, and the start of training separately. Low GPU use during
CPU preparation is not evidence of an undersized training batch; measure GPU
memory and optimizer-step throughput after training begins.

The first full-corpus smoke spent 850 seconds tokenizing on the A100 host.
Eight local CPU workers prepared and verified the same 250,587 training examples
plus 1,024 monitoring examples in 174 seconds, including archive construction.
Verifying, extracting and loading the 64 MB archive locally took 15 seconds.
These are different hosts and different scopes; the new end-to-end GPU setup
time remains to be measured. See the [benchmark receipt](../reports/preprocessing/benchmark.json).

`configs/qwen3-4b-a100.json` tests a microbatch of 16 with accumulation 1,
instead of 8 with accumulation 2. Both use 16 examples per optimizer update.
The original smoke peaked at 27.7 GiB on the 80 GB A100; the larger batch must
pass its own smoke before the first training segment. Set `training_config`
to this file in the artifact recipe to try it. The existing token cache is
compatible because tokenization and the validation sample have not changed.

The fixed 256-case validation panel spreads examples across description styles
and distinct source groups, reserving up to 32 planeswalker and 16 multi-face
cases. It intentionally emphasizes difficult categories, so its rate is a
development-panel score, **not** an estimate of full-corpus accuracy. The report
also includes 32 raw paired examples in `intent-review.jsonl`; inspect request
fidelity, names, rarity, loyalty, and editable defaults before deciding whether
to spend on another segment. Final-test examples are untouched.

For a later segment, use `hf_jobs.py launch --stage train` with the same package,
the existing successful smoke directory, `--resume-record` pointing to the
previous train job, a complete `--checkpoint checkpoint-N`, and **the same
`--ledger runs/segment-1/budget.json`**. Use fresh job/output paths, then launch
`--stage development` against that adapter and the same ledger. Checkpoint
resumption preserves optimizer/scheduler state; starting a fresh adapter would
discard the prior segment's learning. Do not replace `max_steps=-1` with a small
step count, which would change the learning-rate schedule.

To finish the epoch in one longer job, copy the original package with operational
limits changed explicitly. `--compatible-package` verifies that every code,
dataset, token-cache, prompt, model, optimizer and decoding input is unchanged;
only the training time limits and total spending cap may differ. It permits the
original smoke evidence and resumable checkpoint to be reused.

```bash
python src/run_package.py --continue-package runs/segment-1/package \
  --output runs/epoch-package --training-seconds 68400 \
  --timeout-minutes 1199 --budget-limit-usd 60
.hf-jobs/bin/python src/hf_jobs.py plan --package runs/epoch-package \
  --compatible-package runs/segment-1/package --stage train \
  --smoke runs/segment-1/smoke --live-price
```

These are example limits, not authorization to spend. After an explicit budget
decision, `job_budget.py --ledger ... --expected-limit 20 --new-limit 60
--reason '...'` records the amendment while retaining all prior reservations.
Launch with the planned package, compatible parent, checkpoint and ledger. The
full timeout is reserved, even when the measured remaining training time is lower.
The latest two full Trainer checkpoints are retained; every 200 updates, the
adapter is also saved separately and published under its step tag on HF.

`watch_epoch.py --package ... --record ... --dataset ... --baseline
.../base-validation.jsonl --output ... --cpu-python ...` watches that submitted
job. Only after the complete epoch is verified does it submit one bounded
development evaluation, generate the report, and stop for quality review.
It does not retry failed GPU jobs, evaluate the final test, or change the live model.
The baseline JSONL, request, and completion manifest must all match the frozen
dataset, selected cases, model, generation code and decoding settings. The
verified base arm is reused; only the new adapter needs GPU generation.

For an explicitly requested intermediate workshop preview, `export_gguf.py
--allow-partial` permits a budget-stopped checkpoint and labels it
`partial_epoch_preview`. Smoke adapters remain excluded. This exports a serving
candidate only; test the exact quantized model and serving prompt before changing
the live backend. The workshop's constrained schema can change field order and
behavior relative to raw SFT generation; a labeled checkpoint profile can use its
frozen training prompt and unconstrained JSON output with validation afterward.

## Training and serving precision

The current recipe uses QLoRA: frozen 4-bit NF4 base weights, trainable LoRA
adapters, and BF16 computation on the A100. It is not full-weight BF16 training.
QLoRA reduces memory requirements; its speed advantage over BF16 LoRA is not
assumed. The smoke supplies actual throughput before committing more budget.
Serving merges the adapter into a fresh pinned BF16 base, then quantizes once
to GGUF. The public adapters can also be used with the BF16 base directly.

## Freeze and check the data

After downloading a complete dataset with `hub_dataset.py`, use Python from
the CPU environment described in the README. The tokenizer check additionally
needs `transformers==4.56.2` (it does not need PyTorch or a GPU).

```bash
export MTG_RUN=runs/oracle-full-v1
python src/preflight_run.py --dataset data/oracle \
  --config configs/qwen3-4b-full.json --tokenize --output "$MTG_RUN/preflight.json"
python src/run_package.py --dataset data/oracle \
  --recipe configs/full-run.json --output "$MTG_RUN/package"
```

The preflight counts names, rarity, planeswalkers/loyalty, tokens, percentiles,
and overlength rows. The trainer independently refuses truncation and verifies
completion masks. The package is immutable and hash-pinned. It includes only
training and validation SFT files; test records are mounted only in later
evaluation jobs. The training manifest includes split counts/hashes but no test
examples.

For a corpus still being generated, the preparation-only watcher waits for both
independently reviewed recovery exports. It freezes the merged dataset and
package into `runs/prepared/{dataset,package}`. It uses atomic staging and can
restart after interruption; it does not submit a job:

```bash
python src/run_package.py --recovery artifacts/recovery/broad \
  --recovery artifacts/recovery/detailed --output runs/prepared --watch
```

Quarantined examples remain excluded, with their provenance retained. A completed
corpus does not mean every quarantined description was admitted. The final test
must come from the complete merged release, not an interim preview.

## Run on Hugging Face Jobs

Use a separate controller environment, because the Jobs SDK is newer than the
locked GPU environment:

```bash
python3 -m venv .hf-jobs
.hf-jobs/bin/pip install huggingface-hub==2.1.1
.hf-jobs/bin/hf auth login
.hf-jobs/bin/python src/hf_jobs.py plan --package "$MTG_RUN/package" \
  --stage smoke --live-price
```

`plan` is read-only. The launcher's `--max-cost-usd` must cover the current
hardware price multiplied by the full timeout. This is a hard per-job ceiling,
not an estimate or authorization to spend. Set the budget explicitly after
reviewing the plan. Check current [HF pricing](https://huggingface.co/docs/hub/jobs-pricing).
The recipe's initial A100 limits are configurable proposals; queued time,
training throughput, and inference throughput are separate considerations.

```bash
# Set MTG_SMOKE_BUDGET to the amount you intend to permit for this job.
.hf-jobs/bin/python src/hf_jobs.py launch --package "$MTG_RUN/package" \
  --stage smoke --record "$MTG_RUN/smoke-job.json" --max-cost-usd "$MTG_SMOKE_BUDGET"
.hf-jobs/bin/python src/hf_jobs.py status --record "$MTG_RUN/smoke-job.json"
.hf-jobs/bin/python src/hf_jobs.py download --record "$MTG_RUN/smoke-job.json" \
  --output "$MTG_RUN/smoke"

.hf-jobs/bin/python src/hf_jobs.py plan --package "$MTG_RUN/package" \
  --stage train --smoke "$MTG_RUN/smoke" --live-price
.hf-jobs/bin/python src/hf_jobs.py launch --package "$MTG_RUN/package" \
  --stage train --smoke "$MTG_RUN/smoke" --record "$MTG_RUN/train-job.json" \
  --max-cost-usd "$MTG_TRAIN_BUDGET"
.hf-jobs/bin/python src/hf_jobs.py download --record "$MTG_RUN/train-job.json" \
  --output "$MTG_RUN/train"
```

Full training requires successful smoke evidence from the **exact same package**.
The smoke reports memory, step time, loss masks, and sample generations. Its
length-grouped batches exercise long sequences early; its extrapolated runtime
is only an estimate. A changed batch size, model, or recipe requires a new
package and smoke. The full epoch starts again from the pinned base, not from
the 20-step smoke adapter.

The launcher verifies the pinned image's Linux/AMD64 manifest, current pricing,
and private bucket, then uploads only an explicit file allowlist. It uses one
attempt and persistent output mounts. Tokens go through HF secrets, never saved
job receipts. Checkpoints include optimizer/scheduler state and the dataset and
config identity. Every 200 steps, and on the soft training deadline, the trainer
saves a checkpoint. HF's hard timeout bounds the entire process, including
setup and final evaluation. A stopped run is not labeled a completed epoch.

To continue an interrupted training job, launch a fresh job/output with
`--resume-record previous-train-job.json --checkpoint checkpoint-N`, plus the
same package, smoke evidence, and an explicit budget. Inspect/download the
previous terminal job first to choose a complete checkpoint. Never use a bare
adapter or resume a smoke checkpoint into the full scheduler. Ambiguous API
submission failures leave a `submitting` receipt: inspect HF before retrying.

## Paired generation and parser reports

Validation uses both the pinned base and the adapter under the same NF4,
greedy, no-thinking settings. The recipe batches 16 prompts, with a 2,048-token
output limit. Generation flushes each case locally and resumes only the identical
request. Bucket commits can arrive after HF marks a job terminal: the mount's
[write durability depends on close/flush](https://github.com/huggingface/hf-mount#consistency-model),
so the collector waits for both result manifests and verifies their hashes before
scoring. Invalid/truncated outputs are retained. A killed
write's tail is preserved separately and that unfinished case is regenerated.

```bash
.hf-jobs/bin/python src/hf_jobs.py launch --package "$MTG_RUN/package" \
  --stage validation --dataset data/oracle --adapter-record "$MTG_RUN/train-job.json" \
  --record "$MTG_RUN/validation-job.json" --max-cost-usd "$MTG_EVAL_BUDGET"
.hf-jobs/bin/python src/hf_jobs.py download --record "$MTG_RUN/validation-job.json" \
  --output "$MTG_RUN/validation-generations"
python src/setup_mtgish.py
python src/post_training.py --dataset data/oracle --adapter "$MTG_RUN/train/adapter" \
  --generations "$MTG_RUN/validation-generations" --split validation \
  --output "$MTG_RUN/validation-report"
```

Whole-split generation can exceed a job's timeout. Continue with a fresh job
record and `--resume-record previous-validation-job.json`; the same persistent
output prefix is reused. Completed cases are not replaced. A partly written
batch is recomputed with its original companions to preserve batching, while
retaining completed outputs. Identical settings do not promise bitwise equality
across different GPU kernels. Both arms must finish before the paired report.
Repeated jobs each need an explicit budget; the cap is not a total project cap.

For the staged experiment, retain the shared `--ledger` on every resumed job.
Use `--stage development`, the unchanged package and training receipt, a fresh
job record, and `--resume-record` naming the timed-out development job. The
completed baseline and tuned batches are skipped without changing the original
batch companions or dropping long/truncated cases. A single runaway completion
can hold its entire batch to the token limit; keep that limit fixed for a paired
comparison rather than silently shortening the slower arm.

After submitting the replacement job, finish collection and CPU reporting with:

```bash
.hf-jobs/bin/python src/finish_evaluation.py \
  --record "$MTG_RUN/development-resume-job.json" \
  --dataset data/oracle --adapter "$MTG_RUN/train/adapter" \
  --output "$MTG_RUN/development-resumed" \
  --report "$MTG_RUN/development-report" --status "$MTG_RUN/status.json" \
  --cpu-python .venv/bin/python
```

This watcher submits no jobs. It waits for late bucket uploads, runs the paired
mtgish report and loss plots, and stops for quality review without promoting the
adapter or starting another training segment. Use a fresh download directory
for a different job receipt; an existing snapshot from the failed job may be
incomplete even when more results have since reached the bucket.

Review validation, then freeze the adapter and decoding settings. Repeat the
above commands with `--stage test`, `--split test`, and fresh test job/output
paths. The job writes `evaluation-freeze.json` **before** reading test prompts.
The report requires a completed epoch and matching hashes for the data, config,
model, predictions, and parser in both arms. It produces:

- Training loss/token-accuracy PNG, SVG, and raw CSV, with measured validation
  points and the actual monitoring sample size.
- Base-versus-adapter JSON/schema/mtgish acceptance plots and aggregate JSON.
- All-case denominators, failure diagnostics, and style/card-type breakdowns.
- An immutable report manifest and a concise README.

Parser acceptance is not a legal-card or intent-fidelity score. Review omissions,
wrong players/zones, loyalty, multi-face behavior, and under-specified requests
separately. Do not tune again against the final-test score. If it informs another
training decision, treat it as development data and reserve a new test set.

## Release and serving candidate

To share checkpoints as they are saved, add `publication` to an artifact copy
of the staged recipe **before packaging it**:

```json
{
  "publication": {
    "model_repo": "YOUR_ACCOUNT/mtg-oracle-checkpoints",
    "dataset_reference": "YOUR_ACCOUNT/mtg-oracle-descriptions@IMMUTABLE_COMMIT",
    "tag_prefix": "experiment-1"
  }
}
```

The launcher explicitly creates/checks a public model repository. Each save
preserves an inference-only snapshot in persistent storage and publishes it with
a tag such as `experiment-1-train-step-00000200` or `experiment-1-smoke-step-00000020`.
Choose a new `tag_prefix` when changing the recipe; resume segments keep theirs.
Snapshots contain
adapter weights/config, the prompt, and minimal recipe/provenance/model-card
metadata. Existing tags are immutable: a retry must match their files exactly.
Optimizer state and worker logs stay private. If a Hub upload fails, the snapshot
survives optimizer-checkpoint rotation and the controller retries its upload
after collection. Checkpoint cards explicitly distinguish incomplete training
from a finished, evaluated model.

```bash
python src/prepare_model_release.py --adapter "$MTG_RUN/train/adapter" \
  --prompt data/oracle/system-prompt.txt --output dist/oracle-full-adapter \
  --dataset-reference "$MTG_DATASET@$MTG_DATASET_REVISION" \
  --label 'MTG Oracle full-corpus adapter' \
  --evaluation "$MTG_RUN/test-report/comparison.json"
```

The release includes inference weights, recipe/provenance, prompt, attribution,
and matching evaluation evidence. It excludes optimizer checkpoints and logs.
Without matching final-test evidence the model card explicitly says evaluation
is pending. See [publishing](publishing.md) for HF uploads and GitHub artifacts.

For CPU serving, use a CPU environment with the pinned PyTorch/Transformers/PEFT
versions and the supplied llama.cpp converter's requirements. The project's
existing converter/quantizer are from **llama.cpp b11443**. No engine build is
needed. Point to those tools explicitly; their hashes and Python source hashes
are recorded in the candidate manifest:

```bash
python src/export_gguf.py --adapter "$MTG_RUN/train/adapter" \
  --output "$MTG_RUN/serving-candidate" \
  --converter "$MTG_LLAMA_SOURCE/convert_hf_to_gguf.py" \
  --quantizer "$MTG_LLAMA_BIN/llama-quantize" --quantization Q4_0
```

This downloads the exact BF16 base revision, safely merges the adapter, converts
to F16 GGUF, and quantizes once. Allow RAM and disk space for the base, merged
model, F16 intermediate, and final GGUF. `candidate.json` is the handoff for a
separate editor/head-to-head deployment. Smoke-test generated JSON, names,
rarity, loyalty, latency and parser results on that backend before promotion:
GGUF serving is different from the NF4 evaluation. There is no automatic
replacement of the live model.
