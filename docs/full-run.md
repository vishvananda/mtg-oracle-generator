# Full training and post-training workflow

The recipe is [`configs/full-run.json`](../configs/full-run.json). Each stage is
separate: freeze data → CPU preflight → GPU smoke → training → validation →
freeze the selected model/settings → final test → reports → release/serving
candidate. Preparation never launches GPU jobs or replaces the live model.
Prepared datasets, predictions, and weights belong on Hugging Face; keep them
out of Git. The repository contains code, recipes, documentation, and measured
aggregate plots/reports.

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
output limit. Generation flushes each case to persistent storage and resumes
only the identical request. Invalid/truncated outputs are retained. A killed
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
