# Custom-card SFT experiment

This is a small additional supervised pass to test whether new designs improve
translation. It starts from the **completed original SFT adapter**, with a fresh
optimizer, and compares against both that adapter and the served round-two DPO
adapter. It does not assume that more training helps or replace the live model.

The [collection report](../reports/custom-card-full/README.md) describes 3,852
distinct MSEM candidates with rules that parse through unchanged mtgish. They
are not automatically valid training examples. Source records and artwork are
not included in this repository. This experiment stages its records privately;
it does not automatically publish third-party custom-card text or assign a new
license to that text.

## Data and split

The initial selection has 512 training families and 64 held-out families.
Grouping normalizes self-names, numbers, mana symbols, and color words, so close
mechanical variants stay together. It removes families overlapping the original
SFT test/reserve. The new custom holdout also excludes families seen anywhere
in the original SFT training set. Selection covers all five colors, multicolor,
colorless, and every major type present in the parser-filtered source pool.
Neither custom split contains planeswalkers or Battles: this is a source/parser
coverage limitation, not evidence those card types are invalid.

GPT-6.1 Sol, medium reasoning, writes three descriptions per selected card:

* **Broad:** a short gameplay idea with editable defaults.
* **Mechanics:** all rules in player language, leaving printed characteristics
  such as mana cost and stats unspecified.
* **Detailed:** all supplied characteristics and mechanics, including name and
  rarity, stated conversationally.

A separate Sol call checks the target's semantics and each description against
its intended detail level. A deliberate player/effect mismatch tests every
review batch. Both stages use immutable instruction-only cached prefixes and
fresh forks for each batch, following `codex_cached_batch.py`. A rejected
description does not discard its other approved variants. Uncertain or rejected
targets are excluded. These are model reviews, not human expert approval.

Accepted training cards also receive their source wording as a translation
input. Their targets use only the conservative wording normalization already
audited during collection; the teacher cannot change targets. Historical
parenthetical reminder explanations are omitted when mtgish still accepts the
same rules. Some keyword grammar requires reminder syntax (for example, Cycling
on spells); those targets retain their independently reviewed source reminder.
The manifest reports this fallback separately. Actual supervised targets are rechecked with mtgish
after CARDNAME normalization and must preserve the source's parsed structure. Provenance keeps
the original wording, designer/source attribution, normalization changes, and
generation/review receipts in the local corpus artifacts.

Custom examples are mixed approximately 1:1 with examples from the original
**training** split. Replay includes about 10% planeswalker examples and excludes
all new held-out families and the existing evaluation families. Repeated input
pairs are deduplicated; conflicting exact inputs fail export. All variants of a
card stay in one split. At least 384 custom training families and 48 held-out
families must survive review, with required color/type coverage intact.

## Training and comparison

[The fixed recipe](../configs/custom-sft-pilot.json) uses one epoch, NF4 QLoRA,
the existing rank-16 adapter, learning rate 2e-5, batch 8 × accumulation 2,
5% warmup, and completion-only loss. Local CPU preparation verifies every
completion mask and rejects truncation before renting a GPU. Training monitors
64 training examples; those losses are **not held-out validation accuracy**.

One HF A100 job generates identical, unconstrained greedy comparisons for the
original SFT, existing DPO, and custom SFT arms. All use the same production
system prompt and 2,048-token output budget. It trains only the SFT adapter;
the DPO comparator is removed before constructing the optimizer. The candidate
is saved before final generation. Checkpoints are saved every 64 updates.

The initial job has a 120-minute timeout, a 60-minute training-loop limit, and
a $5.01 maximum compute reservation. HF quoted $0.041667/minute for A100-large
when checked on 2026-10-10. The launcher rechecks the live rate and refuses a
larger reservation. This is a ceiling, not an estimated invoice. There is no
automatic paid retry. Codex teacher/reviewer usage is recorded separately.

Afterward, local mtgish checks and blinded Sol judging produce separate panels:

* New custom holdout: up to 192 descriptions from 64 unseen families.
* Existing development: 128 regression requests, including planeswalkers and
  original design concepts. Some source cards were seen during original SFT.

Report JSON/schema validity, parser acceptance, intent fidelity, clean Oracle
style, and faithful **and** parsed outputs. Count failures in the denominator;
an empty or simplified card cannot earn fidelity credit just because it parses.
Paired differences use 5,000 bootstrap samples clustered by card family.
Intervals are descriptive, not adjusted for multiple metrics. This one recipe
is frozen before inspecting held-out generations; further tuning needs a new
evaluation plan. The old locked final SFT test is not regenerated.

GPU results use NF4. A promising candidate still needs the same Q4_0 export and
paired serving check used for previous releases before deployment. A gain on
this parser-filtered custom pool alone does not establish general improvement.

## Reproduce

Use the source candidates from the collection workflow and the exact pinned
original dataset. Keep generated data and run output outside Git.

```bash
python src/custom_training_data.py prepare \
  --candidates /data/custom/review-candidates/candidates.jsonl \
  --official /data/oracle-v2 --output /data/custom-sft/corpus
python src/custom_training_data.py run \
  --root /data/custom-sft/corpus --workers 3
python src/custom_training_data.py status --root /data/custom-sft/corpus
```

Copy `configs/custom-sft-pilot.json` to `/data/custom-sft/config.json`. Create
`run.json` with the following paths and limits. Parent job receipts come from
the completed full-SFT and round-two DPO workflows; both must use your private
HF bucket. `cpu_python` needs the pinned CPU tokenizer dependencies plus
matplotlib; the controller needs current HF Jobs/Buckets support and jsonschema.

```json
{
  "maximum_compute_usd": 5.01,
  "timeout_minutes": 120,
  "sft_adapter": "/data/full-sft/train/adapter",
  "sft_record": "/data/full-sft/train-job.json",
  "dpo_adapter": "/data/round2/train/adapter",
  "dpo_record": "/data/round2/train-job.json",
  "development": "/data/round2/evaluation/development-cases.jsonl",
  "reserved": "/data/round2/reserved-families.json",
  "serving_system": "/data/round2/serving-system.txt",
  "cpu_python": "/path/to/tokenizer-env/bin/python"
}
```

The following command explicitly authorizes the one bounded HF job. It can
start while the corpus workers run; it waits for complete reviews, then exports,
prepares, submits, collects, judges, and plots. It stops for review on failure.

```bash
python src/custom_sft_experiment.py \
  --root /data/custom-sft --run /data/custom-sft/run.json
```

The GPU entry point and dependency lock are `src/custom_sft_gpu.py` and its
adjacent `.lock`. The frozen package binds the exact trainer, dataset, tokenizer
cache, and both parent adapters. `status.json` reports progress. Results are in
`comparison/panels.json`; individual judgments in `comparison/cases.jsonl`;
loss/token-accuracy plots in `plots/`; weights in `gpu/adapter/`; compute
reservations and settled elapsed-time bounds in `budget.json`. Raw generation
and reviewer receipts remain under `corpus/runs/` and `comparison/judge/`.
