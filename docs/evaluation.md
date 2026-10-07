# Validation and the final test

Three different questions must stay separate:

| Measure | What it establishes |
| --- | --- |
| Completion-token loss/accuracy | Predicting reference tokens with the correct preceding target tokens supplied |
| JSON/schema + mtgish acceptance | A generated card fits the output schema and the pinned parser recognizes its complete text/layout |
| Intent fidelity | The output preserves the user's actual request; needs a separate rubric/review |

Exact target-string match is unsuitable as the main score for sparse prompts:
multiple names, costs, stats and implementations can be valid completions.
Neither parser recognition nor low training loss proves balance or official
rules correctness. An empty creature may parse while omitting a requested ability.

## mtgish standard workflow

`setup_mtgish.py` pins upstream revision
`153451e44d88baabfeb9902092032c328f4d5416`. It clones a clean source checkout,
copies parser files into an isolated artifact directory, generates plural
grammars with the upstream script, and compiles only the Go parser. It includes
the upstream short-name table, find/replace rules, ignore list and known names.
No local grammar extension is used. Go 1.18+ and Python are sufficient; the
Vizier/Phase/editor builds are not involved.

`validate_mtgish.py` uses upstream `preprocess_scryfall` for full card layouts
and loyalty formatting, binds `CARDNAME` to each face's name, and submits a
complete card in one request. It retains the original generation and reports
normalized input, upstream text corrections, and available failure diagnostics.
Coordinates are in normalized text and the farthest match is only a hint.
Generated text is **never** replaced with reference Arena wording. Reference
corpus coverage is measured separately using the upstream Arena merge workflow.

A persistent parser process avoids rebuilding/loading grammar for every card.
An ignored or out-of-scope entry is unvalidated, not accepted. An exception or
timeout is reported as a parser error. Parser and grammar hashes accompany each
evaluation. Even an acceptance involving an upstream rewrite is identified
separately, because the raw model text may differ from what the parser accepted.

## Validation during development

The README commands generate on `validation`, then score the saved raw JSONL.
To compare the base with the adapter, run `generate.py` twice with identical
config, split, prompt and generation budget, omitting `--adapter` for the base.
Both use unconstrained greedy generation with thinking disabled and the same
NF4 representation. Merged GGUF CPU serving is a different backend and must be
labeled separately.

`predictions.jsonl` contains one `example_id` and `raw_text` per scheduled case,
plus token count, latency and token-limit information. Its adjacent
`predictions.manifest.json` records model revision, adapter hashes, dataset hash,
split, decoding settings and output hash. Scoring refuses a mismatched receipt,
unknown IDs and duplicates. Missing cases stay in the denominator. No regex
repair, Markdown stripping or target-assisted regeneration occurs during scoring.

The report includes raw failure statuses, counts by description style and
reference type, and counts with/without upstream rewrites. Its primary rate is
accepted cards divided by **every scheduled case**. The attempted-only rate is
secondary. Ignored cards, missing generations, schema failures and parser errors
cannot silently inflate the primary rate. Multi-face designs must parse as a
whole. Retain `cases.jsonl` alongside `metrics.json` for auditability.

## Final test after the model is frozen

The full corpus is published; full-epoch training and final evaluation are pending. The checked-in
[`reports/final/status.json`](../reports/final/status.json) intentionally has no
accuracy value. It must not be populated with pilot or source-corpus scores.

Freeze the complete dataset release, config, prompt, adapter and decoding budget
after using validation for decisions. Record their commit/file hashes, then run:

```bash
uv run --frozen --script src/generate.py --dataset data/oracle \
  --config configs/qwen3-4b-full.json --adapter runs/full \
  --split test --output runs/final-test-predictions.jsonl
python src/evaluate.py --dataset data/oracle --split test \
  --predictions runs/final-test-predictions.jsonl --output runs/final-test
```

The tools refuse a final-test run against a partial release. They pass only the
system/user messages to generation; reference answers are never in the prompt.
Use all test cases, retain failed/truncated outputs, and report the base model
under the same settings. Do not keep selecting new prompts or checkpoints by
the final-test score. If the test informs further tuning, label it development
data and reserve a new test set for the next claim.

Publish the metrics, case-level parser diagnostics, raw predictions, dataset and
adapter hashes, decoding/backend details, and the loss graph from the same run.
A human or separately audited intent review should additionally cover wrong
player/zone, dropped restrictions, planeswalker loyalty, linked faces, sparse
requests and empty-text evasions. That intent score is not implemented by the
parser evaluator and must not be inferred from its results.

## Reproduce the checked-in figures

```bash
python src/plot_training.py --summary reports/pilot-16k/run-summary.json \
  --csv reports/pilot-16k/training-curve.csv --output /tmp/pilot-figure
python src/reevaluate_development.py --output /tmp/pilot-mtgish
```

The latter rescans the 12 saved product prompts per condition without spending
model tokens. Luna's historical product response explicitly wrapped `draft`
with an `image_prompt`; that declared envelope is extracted before applying the
same card schema. The wrapper and raw text remain in the saved predictions.
This product comparison has differing provider contexts and is not a controlled
held-out baseline or a claim about current Luna performance.
