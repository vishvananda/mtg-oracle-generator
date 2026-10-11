# A staged second supervised epoch

This experiment tests more exposure to the original broad corpus, independently
of the small custom-card, repair, and preference experiments. Start from the
completed original SFT adapter, not a cumulative DPO or repair adapter.

The first segment revisits 4,944 optimizer batches (about one quarter of the
19,774-update epoch). It uses all 316,372 original training descriptions through
the unchanged prepared dataset, with a new data-shuffle seed. Targets, JSON
serialization, family split, tokenizer, completion masks, batch size and LoRA
structure remain unchanged. The new learning rate is `3e-5`, with 2% warmup and
linear decay over the **whole second epoch**. A callback stops at the review
checkpoint; setting `max_steps=4944` instead would prematurely finish the
scheduler and would make later continuation a different experiment.

The 256-row loss diagnostic is sampled from training. It is not held-out
validation, and its token accuracy must not be reported as card-generation
accuracy. The original 400-card final test is excluded from this experiment.

## Prepare and launch

Use the existing CPU token cache from the published v2 dataset, or recreate it
with `src/prepared_training.py`. Preparation verifies the pinned dataset,
tokenizer, preprocessing implementation, dependency versions and every cached
completion mask before GPU allocation.

Create a run directory outside Git containing `config.json` (a copy of
[`configs/sft-second-epoch.json`](../configs/sft-second-epoch.json)) and `run.json`:

```json
{
  "user_instruction": "Explicit authorization for the quarter-epoch experiment",
  "maximum_compute_usd": 20,
  "maximum_paid_training_jobs": 1,
  "timeout_minutes": 390,
  "image": "ghcr.io/astral-sh/uv@sha256:28d570c06d150303b39adfa731ada20956c63fc86b1277d7ff2ad63185974092",
  "parent": "/data/original-sft/adapter",
  "parent_record": "/data/original-sft/train-job.json",
  "dataset": "/data/oracle-v2",
  "prepared": "/data/oracle-v2-prepared",
  "cpu_python": "/envs/tokenizer/bin/python"
}
```

The parent record identifies the completed HF job and its private bucket output.
The adapter directory contains its original `run-summary.json`. The launcher
pins all input files and checks the parent job and container image before paying
for compute. Use a Jobs-capable `huggingface_hub` environment for the launcher;
the GPU dependencies are locked separately.

```bash
python src/sft_continuation.py --root /data/epoch2-quarter --action prepare
python src/sft_continuation.py --root /data/epoch2-quarter --action launch
python src/sft_continuation.py --root /data/epoch2-quarter --action watch
```

This is a single submission. It does not retry failures or authorize the rest
of the epoch. At the quoted A100 rate, the 390-minute hard timeout reserves
$16.25013 of the $20 experiment limit. A separate six-hour training-loop limit
leaves shutdown time. Optimizer, scheduler, random state and adapter checkpoints
are saved every 200 updates, retaining two resumable checkpoints; the review
step forces a final checkpoint. Private bucket storage receives the saves.

The GPU trainer explicitly reactivates the existing adapter after TRL's k-bit
preparation, verifies only LoRA parameters are trainable, and checks that weights
really changed. A completed segment must have finite metrics, exactly the
authorized update count, matching saved-adapter hashes and a full resumable
checkpoint. A time-limit stop is retained for inspection, not relabeled complete.

## Review before continuing

The prepared local follow-up uses `src/sft_checkpoint_review.py` to wait for the
verified segment, export Q4_0, and compare original SFT, deployed DPO2 and the new
checkpoint on identical prompts. The frozen panel has 128 existing development
requests plus 32 custom-card families. Neither is the locked final test.

Report schema validity, mtgish recognition and semantic preservation separately,
with panel/type breakdowns and paired uncertainty. Compare the actual serving
export; a lower training loss alone does not justify continuation or deployment.
The observer and local follow-up submit no additional GPU jobs. Inference-only
adapter publication uses the existing checkpoint repository and an immutable tag;
optimizer state remains in private storage. Publication is not model promotion.

An explicitly approved later segment can use `sft_continuation_gpu.py --resume`
with the saved checkpoint, retaining the original full-epoch schedule and data
order. The first segment initializes a fresh optimizer from the original SFT
weights; it does not resume the exhausted first-epoch scheduler.

## Serving investigation

[`src/serving_quantization.py`](../src/serving_quantization.py) varies only the
GGUF quantization of an identical F16 source: Q4_0, Q4_K_M, Q5_K_M and Q8_0.
Both DPO2 and the custom-SFT candidate use the same 64-case development subset,
runtime, tokenizer, prompt, greedy decoding, output budget and context. A
Transformers-to-llama.cpp prompt audit checks both rendered bytes and token IDs.
Each export is quantized once from F16, never from another quantized file.

[`src/serving_adapter_export.py`](../src/serving_adapter_export.py) prepares the
other candidate: a pinned original base in Q4_K_M plus a separate F16 cumulative
LoRA. That avoids rounding the adapter into the quantized base and permits
smaller future adapter downloads. It still changes the training-time NF4 base,
so it must be measured rather than assumed equivalent.

These are native CPU quality experiments. They do not establish browser GPU
speed, memory use or output equivalence. The installed browser runtime accepts
LoRA paths and its pinned WebGPU source supports K-quants. However, the public
JavaScript loader currently mounts model shards and an optional vision projector;
separate adapter Blobs need a loader extension. Actual device testing is still
required before a deployment change. Float32 arithmetic
in the alternate browser backend does not mean float32 model weights: its
weights remain quantized to four bits.

The [completed serving comparison](../reports/serving-quantization/README.md)
contains aggregate results, paired uncertainty, artifact identities and the
browser integration limitations. It did not justify changing the serving model
or increasing its download size.

The next-checkpoint [separate adapter workflow](separate-browser-adapters.md)
reuses that pinned base and adds an F16 adapter export to the local follow-up.
This prepares smaller future browser updates without changing the deployed
model or the frozen evaluation panel.
