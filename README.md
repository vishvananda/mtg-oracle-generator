# MTG Oracle Text Generator

Turn a card idea into editable card JSON with Oracle-style rules text. This
repository contains the description-generation and review pipeline, dataset
preparation tools, a QLoRA training recipe, and evaluation against the unchanged
[mtgish parser](https://github.com/i5jb/mtgish).

**GitHub hosts the code, documentation, examples, and evaluation reports.
Hugging Face hosts the prepared datasets and trained adapters.** Their download
links and pinned revisions are recorded in [release status](release-status.json)
after publication. Local release files in `dist/` are staging copies ignored by Git.

A request such as “a midsize red Giant” can leave cost and stats to the model.
A detailed request must preserve its mechanics. Names, rarity, costs and stats
are editable suggestions when unspecified. Parser acceptance does not establish
intent fidelity, game balance, or official Magic rules correctness.

## Current status

| Artifact | Status |
| --- | --- |
| Full Oracle description corpus | [Public dataset](https://huggingface.co/datasets/vishvananda/mtg-oracle-design-descriptions-v2): 316,372 train / 400 final-test cases; [v2 split policy](docs/two-way-split.md) |
| Names + rarity pilot dataset | Packaged locally: 19,065 train / 572 validation; not uploaded |
| Completed Qwen3 4B training pilot | 16,000 train / 480 validation; predates names and rarity |
| First full-corpus training segment | [963-step development report](reports/full-corpus-step-963/README.md): 254/256 schema-valid outputs; 126/256 accepted by mtgish |
| Revised training run | **One full epoch complete**, 19,774 steps; [loss curves and diagnostic measurements](reports/full-epoch-v2/README.md) |
| Interactive preview | [Card workshop](https://tetrarchs.com/cards/new): experimental 1,000-pair DPO round in Q4_0 alongside GPT-6 Luna; two local drafts, saved choices and chosen-card art |
| Browser inference benchmark | [Run the 2.37 GB model on your device](https://tetrarchs.com/model-bench/): streamed output, WebGPU/CPU selection, first-token and total timings; [source and reproduction](web/client-benchmark/README.md) |
| Preference pilot | [47 reviewed pairs](https://huggingface.co/datasets/vishvananda/mtg-oracle-preference-pilot-v1), [six-update adapter](https://huggingface.co/vishvananda/mtg-oracle-qwen3-4b-dpo-pilot-20261009); [no measured development gain](reports/dpo-pilot-v1/README.md) |
| Larger preference round | 1,007 approved pairs; **1,000 trained for 125 updates**. [Run details and results](reports/preference-round-two/README.md) |
| Custom-card collection | [All 14,969 eligible MSEM designs](reports/custom-card-full/README.md): 3,997 parse after conservative cleanup; **3,852 distinct review candidates** after duplicate and vanilla-card filtering |
| Locked final SFT test | **400/400 schema valid; 260/400 (65.0%) accepted by mtgish**; [paired base comparison](reports/full-epoch-v2/README.md#locked-final-generation-test) |
| Public model checkpoints | [Checkpoint repository](https://huggingface.co/vishvananda/mtg-oracle-qwen3-4b-checkpoints-20261007); weights are published as training saves them |

## Use the data and fine-tune

Python 3.11–3.12 is required for the locked GPU environment. Dataset preparation
and parser evaluation run on CPU; training requires a CUDA GPU. Install
[uv](https://docs.astral.sh/uv/getting-started/installation/) for the locked recipe.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

export MTG_DATASET=vishvananda/mtg-oracle-design-descriptions-v2
export MTG_DATASET_REVISION=7c1bbca04b4259c15c397c5c6a00af5e143ef21e
python src/hub_dataset.py download --repo-id "$MTG_DATASET" \
  --revision "$MTG_DATASET_REVISION" --directory data/oracle

python src/train_qlora.py --dataset data/oracle \
  --config configs/qwen3-4b-two-way.json --preflight

# Start with a 20-step smoke run. Use a fresh output directory for each run.
uv run --frozen --script src/train_qlora.py --dataset data/oracle \
  --config configs/qwen3-4b-two-way.json --output runs/smoke --max-steps 20

# One configured epoch, starting again from the pinned base.
uv run --frozen --script src/train_qlora.py --dataset data/oracle \
  --config configs/qwen3-4b-two-way.json --output runs/full --max-steps -1
```

The development machine has an unpublished staging copy at
`dist/names-rarity-pilot-v1`; it is not included in a Git clone. That partial
dataset has no final test. The command above downloads the complete published
v2 release: training plus one locked 400-case final panel. There is no separate
validation split. All card types and colors are covered; rare abilities stay in
training. See the [split policy and coverage table](docs/two-way-split.md).
Each release supplies standard `messages`, TRL `prompt`/`completion`, and
provenance-rich records. [Dataset format and generation](docs/dataset.md) explains
how to rebuild it; [training](docs/training.md) covers larger models and GPUs.
The [quarantine recovery workflow](docs/recovery.md) rechecks false positives
and repairs descriptions with independent review while preserving original data.

The [full-run workflow](docs/full-run.md) connects dataset freezing, token-length
checks, bounded HF Jobs, resumable checkpoints, paired base/tuned evaluation,
loss and mtgish plots, and model/CPU-serving exports. Its preparation watcher
can wait for the remaining corpus work; GPU submission is an explicit step.
Its staged recipe can stop after a short training segment and evaluate a fixed
development panel before continuing the same epoch, with a shared spending cap.
For remote GPU jobs, use its CPU token-cache preparation step first: eight
workers tokenize and verify the data once, and each GPU phase loads the same
verified Arrow files without repeating full-corpus preprocessing.

## Latest results

The larger DPO round completed 125 updates on 1,000 reviewed pairs, with **no
clear measured quality gain**. On the
locked 128-request production panel, SFT → DPO intent pass counts were
**68 → 67**, and mtgish acceptance was
**47 → 47**. Development intent was 74 → 71 and
parser acceptance 47 → 48. These are new-request panels with model-judged
intent, not the 400-card SFT test. See the [paired intervals, curves and limitations](reports/preference-round-two/README.md).
[Download the adapter or exact Q4 quant](https://huggingface.co/vishvananda/mtg-oracle-qwen3-4b-dpo-round2-20261009)
and follow the [local serving guide](docs/serve-preference-model.md).

The earlier DPO pilot completed without measured development gain. Both SFT and
DPO passed schema checks on 64/64 development requests, mtgish on 48/64 and
Sol's intent judgment on 63/64; Oracle text was unchanged in all 64. These
correlated development cases do not establish representative accuracy. See the
[pilot report and loss curve](reports/dpo-pilot-v1/README.md). The
[next-round plan](docs/next-preference-round.md) prioritizes Sol-reviewed repairs
and a broader evaluation before another full supervised epoch.

The revised full epoch is complete. Its locked 400-case final test produced
100% schema-valid outputs and 65.0% whole-card mtgish acceptance, compared with
9.0% and 0.5% for the pinned untuned base. These are parser and format metrics,
not intent accuracy. See the [full-epoch report](reports/full-epoch-v2/README.md).
The older development result below belongs to the earlier split and checkpoint.

The first one-hour segment on the full corpus reached 6.15% of one epoch.
On the fixed 256-case development panel, schema compliance improved from
**8.59% to 99.22%** and whole-card mtgish acceptance from **0% to 49.22%**
against the untuned Qwen baseline. Complex mechanics still have intent errors.
That run was canceled to improve the split. Its report uses the original split;
the workshop now serves the larger DPO round on the completed revised epoch alongside Luna. The CPU preview uses a merged
Q4_0 model, so the NF4 development scores do not measure that serving artifact.
The panel emphasizes difficult card types and is not a full-corpus accuracy
estimate. See the [loss curves, parser coverage and qualitative review](reports/full-corpus-step-963/README.md).

## Measured pilot results

![Measured loss and token accuracy from the 16k pilot](reports/pilot-16k/training-curve.png)

The 1,000-step pilot reduced validation loss from **2.43366 to 0.24646** and raised
teacher-forced completion-token accuracy from **74.58% to 93.24%**. Training took
46.5 minutes on an A100. Validation was measured before and after training;
there are only two validation points, not a continuous validation curve.
These numbers do not measure successful card generation. Known intent failures
remain. See [results, raw CSV and parser measurements](reports/pilot-16k/README.md).

## Evaluate generated cards

```bash
# Run only after the fixed training endpoint; do not use final test to choose checkpoints.
# Downloads the pinned upstream source and builds only the Go parser in artifacts/.
python src/setup_mtgish.py

uv run --frozen --script src/generate.py --dataset data/oracle \
  --config configs/qwen3-4b-two-way.json --adapter runs/full \
  --split test --output runs/test-predictions.jsonl
python src/evaluate.py --dataset data/oracle \
  --predictions runs/test-predictions.jsonl --split test \
  --output runs/test-metrics
```

[Evaluation](docs/evaluation.md) describes the final-test procedure, whole-card
parsing, failure denominators, and comparison of base versus adapter.
[Publishing](docs/publishing.md) covers GitHub, HF dataset/model cards, hashes,
and adapter staging. No private editor or compiler is required.

[Preference training](docs/rl-plan.md) compares Luna intent judgments, parser
rewards and Oracle embeddings. The first requested DPO pilot sampled four
candidates from the completed SFT epoch for each of 512 training-only
requests. Luna proposed 77 pairs; independent Sol review accepted 20. A follow-up
review checks remaining samples and training Oracle references to recover better
pairs before renting another GPU. Inconsistent judgments are excluded with their
audit records preserved. A frozen
64-request development panel measures the change separately from the 400-case
final SFT test. The pilot has a $10 GPU ceiling and does not deploy automatically.
See the [earlier reward diagnostics](reports/rl-preparation/README.md) for the
motivation and parser-reward limitations.

Run CPU checks with `python -m unittest discover -s tests -v`. Original code and
documentation use [MIT](LICENSE); card content and base models retain their own
[rights and attribution](DATA_LICENSE.md).
