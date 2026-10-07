# Completed 16k pilot — measured results

This report describes the original Qwen3-4B-Instruct-2507 adapter trained on
16,000 examples from 6,126 training-source cards, with 480 validation examples.
It predates explicit name and rarity targets. It is **not** the full-corpus run,
and its validation is **not** the final test.

![Loss and token accuracy](training-curve.png)

| Measure | Before training | After training |
| --- | ---: | ---: |
| Validation completion-token loss | 2.433659 | 0.246460 |
| Validation completion-token accuracy | 74.58% | 93.24% |

Training completed 1,000 optimizer steps / one epoch in 2,791.7 seconds
(46.5 minutes); peak allocated GPU memory was 24.55 GiB. Mean training loss was
0.30162. The last 100 logged batches averaged loss 0.226217 and token accuracy
93.71%. Validation was measured only before and after training. The plot marks
those observations without inventing intermediate values.

Evidence: [run summary](run-summary.json), [raw training CSV](training-curve.csv),
[token statistics](training-curve.json), [SVG figure](training-curve.svg), and
[training configuration](../../configs/qwen3-4b-pilot.json). Training token
accuracy is a mean of logged batch metrics; it is not whole-card accuracy.

## Saved product generations, rescored with standard mtgish

The old prototype's parser omitted parts of the upstream workflow. We rescored
the saved, unchanged outputs with pinned upstream revision
`153451e44d88baabfeb9902092032c328f4d5416`, its standard preprocessing, all alias/
correction/ignore files, and complete-card parsing. These numbers supersede the
old mtgish development counts; no model was called again.

| Historical condition | Card JSON schema | mtgish accepted / all cases |
| --- | ---: | ---: |
| Qwen 16k, no terminology guide | 12/12 | 2/12 (16.7%) |
| Qwen 16k, terminology guide | 12/12 | 1/12 (8.3%) |
| Luna, terminology guide | 12/12 | 6/12 (50.0%) |

These are **12 known development prompts**, including the original planeswalker
and opponent-library requests. The Qwen outputs use the merged Q4_0 CPU serving
model, while the training losses used NF4 on GPU. Luna used a different provider
context and was asked for an art prompt alongside the card. Its declared
`draft`/`image_prompt` envelope was extracted without changing card fields.
This is a small product probe, not a controlled model benchmark, a final-test
result, or a measurement of the later validator-assisted Luna workflow.

No accepted case in this rescore used an upstream text correction. The adapter
still demonstrably loses intent: the planeswalker emits a tap activation and an
invalid “Ultimate” line; the opponent-library case changes which library and
what action is permitted. High token accuracy therefore does not establish
reliable generation. Raw descriptions, model outputs, and request metadata are
in [development-predictions.jsonl](development-predictions.jsonl); the corrected
[metrics](mtgish-standard/metrics.json) and [case diagnostics](mtgish-standard/cases.jsonl)
make every denominator and outcome inspectable.

## Reference-corpus coverage is a separate measurement

For the 2026-10-06 snapshot, the documented mtgish preprocessing leaves 35,019
Oracle entries. Of these, 33,500 parse, two reject, and 1,517 are explicitly
ignored: **99.994% of attempted entries**, or **95.662% of all entries**.
The two failures are playtest cards. Those denominators differ from the 36,163
game-card source IDs because upstream preprocessing groups variants and excludes
some layouts. The reference audit applies 375 Arena replacements and 145 parsed
entries use upstream text corrections; generated-card evaluation does not apply
Arena replacements. All 33,500 accepted adapter representations matched the
unmodified upstream CLI. See [reference coverage evidence](../reference-parser-coverage.json).

Reference coverage measures parser reach on existing cards, not generated-card
accuracy. The full model's final-test score remains [pending](../final/status.json).
