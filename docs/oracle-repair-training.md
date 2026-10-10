# Oracle-error repair training

A focused repair SFT continuation is the next useful experiment. The existing
model was mainly trained on description → card generation; the browser repair
task instead supplies a card, a requested change, and structured parser feedback.
Previous DPO repairs used the original description rather than this conversation
shape. Teach the actual repair task before collecting more preference pairs.

## Prepared now

`src/repair_corpus.py` exports the browser's exact `revisionMessages` prompt and
`designOnly` card projection through the shared `prompts/browser-repair.mjs` protocol implementation. It
requires explicit training-split records, excludes all supplied holdout families,
and keeps one source target per family. It checks both original and corrupted
cards with the unchanged mtgish whole-card flow. Infrastructure failures stop
export; they never become negative examples.

The private synthetic pilot has **128 examples from 86 families**. It contains
47 missing trigger commas, 25 missing activation colons, 20 written-out tap
symbols, 14 draw-grammar changes, 10 legacy enters wordings, and 12 keyword
spelling errors. Six unsupported targets and two non-browser layouts were
skipped. A legacy wording rejected by mtgish is not necessarily an illegal rule.
The target is the original accepted Oracle text; all browser-visible non-Oracle
fields remain unchanged. The pilot includes only one loyalty-bearing example,
so it is not representative of planeswalkers yet.

The [manifest](../reports/oracle-repair-pilot/manifest.json) records source,
holdout, production system prompt, shared browser prompt, and parser hashes.
It is **prepared, not trained**. Raw third-party card data stays outside Git.

```sh
python src/repair_corpus.py \
  --source /data/custom-pilot/dataset/train.jsonl \
  --holdout /data/custom-pilot/dataset/evaluation-cases.jsonl \
  --system /data/served-client/system-prompt.txt \
  --output /data/repair/synthetic-pilot --limit 128
python -m unittest discover -s tests -p 'test_repair_corpus.py' -v
```

Use the exact served system prompt, not a simplified training-only version.
Repeated exports require a fresh directory so receipts remain auditable.

## Current-model probe

Nine fresh probes used the production browser prompt against served round-two
DPO Q4_0: temperature 0.2, seed 42, 768 output tokens. All four small edit probes
parsed and manually followed their requested change. **Three of five repairs**
parsed: draw grammar, loot punctuation, and the Smaug shortened self-reference.
All five repairs preserved non-Oracle metadata. The planeswalker probe still
had malformed scry wording and an unsupported custom subtype; the ambiguous
“exactly two or more colors” probe remained unsupported.

[Probe results](../reports/oracle-repair-pilot/baseline-probes.json) are smoke
checks, not a representative accuracy estimate. Smaug demonstrates the intended
repair: replace its abbreviated self-reference with `CARDNAME`, keeping its
name and abilities. Do not rename the card just to satisfy a parser convention.

## Next dataset

Start with 1,000–2,000 distinct training families. Use three sources:

- Real errors from current-model generation, paired with independently reviewed
  minimal fixes. Capture the original request and exact parser feedback.
- Conservative inverse corruptions of accepted targets, including punctuation,
  self-reference, loyalty formatting, quantities, and older Oracle wording.
- Controls: accepted cards requiring no change, and supported-looking failures
  whose valid mechanic is simply outside the parser. Preserve their mechanics;
  never invent a simpler card to increase parser acceptance.

Separate fixes that only change wording from ambiguous mechanic requests. For
ambiguity, preserve the draft and request clarification through the product's
supported path; do not guess a new mechanic in repair-only mode. Explicitly
cover planeswalkers, modal abilities, triggered/activated/static abilities,
self-references, counters, zones and target restrictions. Balance these categories
rather than allowing typo repairs to dominate.

Split by card/mechanic family before generating corruptions. Keep all variants
and corrected targets together. Exclude the original locked test, custom-card
holdout, and future repair holdout from training and teacher selection. Use a
small, fixed real-error evaluation panel; synthetically undoing one's own
corruptions is not enough evidence of repair ability.

## Training and promotion

Start with a short repair SFT pilot and a fresh optimizer. Use completion-only
loss, a low learning rate, and an initial 50/50 mix of repair examples and normal
card-generation replay. Freeze the budget, steps, family split and evaluation
protocol before running it. Select the parent only after the custom SFT
candidate's Q4 comparison and style-judge audit; preserve all candidate artifacts.
No paid training or automatic deployment is started by the exporter.

Compare the current model and candidate with the exact same deployment quant:
normal generation; generation plus one repair; and direct repair of the fixed
real-error panel. Record schema validity, parser acceptance, preserved mechanics,
unchanged metadata, no-op behavior, introduced errors, truncation and latency.
Have a separate judge compare the original and repaired mechanics, blind to the
model and parser verdict, with a manually reviewed calibration subset.

Parser reward must be gated by semantics. A card that drops an ability, changes
exile to mill, alters targets, or changes costs is a failed repair even if it
parses. For unsupported valid mechanics, a parser rejection should not penalize
preserving the correct card. Use parse success only as a small auxiliary reward
on known-supported cases. Once faithful repair alternatives exist, preference
training can distinguish minimal repairs from unnecessary rewrites; it should
not precede basic task supervision.

The parser's farthest-match token is a diagnostic hint, not a guaranteed cause.
Keep that limitation in both teacher prompts and training examples.
