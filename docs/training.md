# Training approach and larger models

This is supervised fine-tuning with completion-only cross-entropy. Source card
JSON provides the target; model-generated descriptions and historical wording
provide the user input. The teacher does not invent the canonical target during
description generation. mtgish acceptance is an evaluation metric, **not a
differentiable loss term** in this recipe.

## Reproduce the measured 16k recipe

The base is `Qwen/Qwen3-4B-Instruct-2507`, pinned to
`cdbee75f17c01a7cc42f958dc650907174af0554`. The completed pilot used
[this configuration](../configs/qwen3-4b-pilot.json): NF4 with double
quantization, rank 16 / alpha 32, dropout 0.05, one epoch, learning rate 1e-4,
5% warmup, batch 8 × accumulation 2, max length 1,536, seed 17, and no packing.
It used 16,000 training examples (7,654,311 tokens) and 480 validation examples.
The longest training sequence was 1,339 tokens. No final test was evaluated.

Exact dataset contents, not merely the same example count, are needed to
reproduce a run. The later names/rarity release has 19,065 / 572 examples and
must not be described as the dataset behind the pilot graph. The model's card
and release manifest identify this distinction.

The trainer checks every completion mask against the original assistant JSON,
checks the actual collator's labels, and fails if a sequence would be truncated
or the chat prefix does not match. It runs paired base/adapter generations on a
deterministic validation sample. Those are development probes, not final tests.
It saves the adapter, tokenizer, trainer state, config and metrics; original base
weights are not included in an adapter release.

GPU dependencies are pinned in the PEP 723 script and adjacent uv lockfile:
PyTorch 2.8.0, Transformers 4.56.2, TRL 0.23.0, PEFT 0.17.1,
Accelerate 1.10.1, Datasets 4.1.1 and bitsandbytes 0.47.0. Run with Python
3.11 or 3.12. Reproduction can still vary across hardware and kernels.

## Full corpus / larger model

Start with `configs/qwen3-4b-full.json`. It increases max length to 4,096,
uses batch 8 × accumulation 2, and evaluates every 500 steps on a fixed
1,024-example validation sample (evaluation batch size 8). The original pilot
used that training batch size on an A100; the longer full-corpus sequences still
require a fresh smoke run. These are
**starting settings, not a completed full-corpus result**. Long multi-face cards
may exceed that length; the trainer refuses silent truncation. Increase context
or create a documented length-filtered training variant while leaving held-out
evaluation denominators intact.

For a larger causal instruction model, copy that config and change:

1. `model` and `model_revision` to its HF ID and immutable commit SHA.
2. `target_modules` to the architecture's attention/MLP projection modules (or
   PEFT's `"all-linear"` when appropriate). Do not assume every architecture has
   Qwen's module names.
3. `max_length`, batch size and accumulation to fit its tokenizer/context and
   the available GPU. Keep the effective batch explicit.
4. Learning rate, LoRA rank/alpha and evaluation cadence based on validation.
   Change `eos_token` only if the model's chat format needs an explicit override;
   otherwise the trainer uses its tokenizer's EOS token.

Run `--preflight`, then a 20-step smoke run and inspect the loss-mask checks and
unconstrained generations before paying for a full epoch. The trainer uses
`enable_thinking=False` and requires assistant supervision to decode to exactly
the JSON target. Models whose chat templates inject additional supervised text
need an explicit template adaptation; failure is preferable to training on an
incorrect mask. New model architectures may require newer dependency versions;
update and commit the lockfile, then rerun these checks.

The supplied trainer uses **one GPU**, SDPA attention, mixed precision and
gradient checkpointing. It is not a distributed training launcher. Large models
that do not fit need another trainer (for example a separately configured
distributed TRL setup). The `messages` and `sft` data are independent of model
size and can be loaded directly into that trainer. Keep completion-only masking
and the original train/validation/test assignments.

## GPU host or Hugging Face Jobs

Run the same commands from the root README on any compatible CUDA host.
The measured 4B pilot used 24.55 GiB peak allocated GPU memory on an A100 and
46.5 minutes in the training loop, plus setup/evaluation. This is an observation
for that 16k snapshot, not a runtime, price, or memory guarantee for larger runs.

For [HF Jobs](https://huggingface.co/docs/huggingface_hub/guides/jobs), run the
repository and locked script inside a job with a pinned container image,
explicit hardware flavor and timeout. Download the dataset by its release
commit into the job, then run `--preflight` followed by the same `uv run` command.
The successful pilot used uv 0.12.23 in the image recorded in
[`hf-pilot-environment.json`](../configs/hf-pilot-environment.json).
This repository does not automatically submit paid jobs.

The [full-run workflow](full-run.md) supplies the preparation watcher, private
HF Jobs controller, checkpoint resume, paired generation, parser reports, model
release, and separate GGUF serving export. Periodic validation monitors the
fixed sample; post-training generation covers the complete validation/test
splits. It never evaluates the full 30k-plus validation set every 500 steps.

Persist adapters/checkpoints to mounted storage or upload them before the job
exits; container-local output alone will be lost. Pass HF access through the
job's secret mechanism, never a committed command/config. Allow time for final
evaluation, saving and uploading when using `--training-seconds`; it only limits
the training loop. Keep the provider's hard timeout longer and choose the
hardware budget using current prices. Accelerator queue time is separate from
training time. The 4B recipe cannot predict larger-model cost without a smoke
measurement on the intended accelerator.

`--resume runs/previous/checkpoint-N` resumes optimizer/scheduler state into a
fresh output directory. A bare adapter is not a resumable trainer checkpoint.
Resume with the same dataset and config; a different run should start from the
pinned base and receive a new identity.

## Graphs and validation-informed training

```bash
python src/plot_training.py --summary runs/full/run-summary.json \
  --state runs/full/trainer_state.json --output runs/full/plots
```

Plots show raw batch logs, a 50-step training mean, and only measured validation
points. The pilot measured validation only at steps 0 and 1,000; the full-run
config schedules intermediate measurements. Token accuracy is teacher-forced
completion accuracy, not the percentage of generated cards that work.

Future experiments could use parser-assisted rejection sampling, repair pairs,
or preference/RL rewards. They are not implemented here. Any such experiment
must preserve user intent and avoid rewarding empty rules text or removing
unsupported mechanics merely to get a parser pass. Compare using validation,
then freeze the model/prompt before the final test.
