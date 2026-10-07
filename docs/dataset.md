# Dataset generation and formats

The unit of training is **description → editable card JSON**, not a parser AST.
The JSON contains Oracle text plus the requested or proposed name, mana cost,
type line, colors, stats/loyalty, rarity, and linked faces when applicable.
mtgish validates the output; it is not the generation target.

## What is generated

Each eligible Oracle card receives five detail-level projections before any
teacher call, plus two broad descriptions in a separate queue:

| Style | What the input expresses |
| --- | --- |
| `minimal` | Type/subtype and fixed stats when projected; no implied abilities |
| `concept` | Type, colors and qualitative size where available |
| `mechanics` | Every source mechanic, with unspecified characteristics left open |
| `complete` | Full characteristics and mechanics in ordinary language |
| `casual_complete` | The same constraints in a terser player voice |
| `broad_plain` | A general gameplay idea compatible with the source card |
| `broad_shorthand` | A broad idea using ordinary player terminology |
| `printed_wording` | Identity-linked historical printed wording |
| `current_oracle` | Current wording as input, teaching normalization/copying |

For a Hill Giant-like card, descriptions can range from “a 3/3 Giant” through
“a midsize red Giant creature” to a complete description of a 3/3 red Giant for
`{3}{R}` with no abilities. Sparse inputs do not claim omitted mechanics.
For broad gameplay descriptions, the reference is one plausible completion;
omitted numbers are permitted, contradictions are not. The detailed styles
must preserve quantities, targets, controllers, zones, optionality, timing and
restrictions. Targets may propose cost, stats, name and rarity when omitted.

The [player language guide](../prompts/player-language-v1.txt) distinguishes
loot/rummage, blink/bounce, cast/play, ramp/fixing, and other common terms.
It is supplied to both teacher and reviewer. Source text is treated as data.

The historical input policy deliberately retains pre-Oracle vocabulary such as
“comes into play.” It excludes identified mechanical errata and obsolete rules,
including mana burn, using [a versioned policy](../configs/historical-wording-exclusions.json).
It is not an exhaustive historical Oracle revision archive. Identity, language,
face matching and exclusion evidence remain in provenance; automatic admission
is not a guarantee that every old wording is mechanically equivalent.

## Source snapshot and splits

The current run uses the complete 2026-10-06 Scryfall Oracle bulk snapshot:
38,708 Oracle IDs, of which 36,163 were projected as game cards. Art/marketing
fronts and records that could not be projected are inventoried separately.
Eligibility is independent of the editor's supported mechanics.

Source families are grouped by normalized mechanics **before** generating
descriptions. Related faces, rewordings, name variants and rarity variants
inherit the same split. The historical run reserves prior held-out identities;
its full [split map](../configs/oracle-20261006-splits.json) is included.
Fresh runs can omit that map and use deterministic family hashing. Exact
description collisions prefer test, then validation, then training. The release
packager rejects cross-split card identities or source groups and conflicting IDs.
Grouping is a heuristic; it cannot eliminate every semantic similarity or
pretraining exposure to public card text.

Targets normalize self-references to `CARDNAME`, retaining true references to
other named cards. The export adds actual source names as editable proposed
names, plus explicit-name variants. It joins rarity from the pinned reference
printing and adds explicit-rarity requests to a stable subset. Rarity is not an
Oracle-level invariant or a verified balance assessment. Linked face names and
cross-references are preserved.

## Rebuild the corpus

Install `requirements.txt`, Git, and an authenticated Codex CLI with access to
the configured teacher model. All commands run from the repository root on
Linux/macOS (workers use `fcntl` locks). Start with a fresh artifact directory.

```bash
export CARD_INTENT_ARTIFACTS="$PWD/artifacts"
mkdir -p "$CARD_INTENT_ARTIFACTS/full-oracle"

# Omit this copy to fetch today's bulk metadata instead of the research snapshot.
cp configs/oracle-20261006-bulk.json "$CARD_INTENT_ARTIFACTS/full-oracle/bulk-metadata.json"
python src/fetch_oracle.py
python src/full_corpus.py --output "$CARD_INTENT_ARTIFACTS/detailed" \
  --split-map configs/oracle-20261006-splits.json
python src/corpus_workers.py prepare --root "$CARD_INTENT_ARTIFACTS/detailed"
python src/configure_workers.py --root "$CARD_INTENT_ARTIFACTS/detailed" --workers 3

python src/prepare_broad_corpus.py --root "$CARD_INTENT_ARTIFACTS/detailed" \
  --output "$CARD_INTENT_ARTIFACTS/broad"
python src/configure_workers.py --root "$CARD_INTENT_ARTIFACTS/broad" --workers 3

# Two batches first. Inspect results.jsonl and review receipts before continuing.
python src/corpus_workers.py run --root "$CARD_INTENT_ARTIFACTS/detailed" --limit 2 \
  --config "$CARD_INTENT_ARTIFACTS/detailed/generation-config-public.json"
python src/corpus_workers.py run --root "$CARD_INTENT_ARTIFACTS/broad" --limit 2 \
  --config "$CARD_INTENT_ARTIFACTS/broad/generation-config-public.json"
```

Rerun each `run` command with `--limit 100000` to process remaining batches.
They may run in separate terminals, giving six concurrent workers. This makes
paid/authenticated model calls; none are started merely by preparing a queue.
The default model for both stages is `gpt-6.1-sol`, at medium reasoning effort,
12 cards per batch. Model availability depends on the caller's account.

The cached transport uses a tool-disabled, instruction-only Codex base for each
stage/model/schema and forks each batch independently. It never appends one
batch to another batch's history. Compact labels keep the response schema
stable. Usage receipts subtract inherited base usage before counting each
batch. Cache hits are observed, not guaranteed. This transport requires a CLI
supporting `codex exec fork`; use `--transport direct` when configuring a fresh
queue if it does not. The direct transport uses medium effort and omits shared
prefix sessions. Record `codex --version` and the frozen configuration with any
new run; CLI behavior can change.

Review is a separate call with deterministic field checks, three negative
fidelity controls, and four additional omission/contradiction controls for broad
descriptions. Failed or uncertain rows are quarantined. A failed batch stops new
starts; inspect its receipt before an explicit `retry-review --job-id NNNNN`.
Placing `{}` in `ROOT/drain.request.json` stops new batches after in-flight work
finishes. Neither a failed review nor a timeout triggers an automatic retry.

Use the separate [quarantine recovery pass](recovery.md) to recheck scripted
false positives, audit source conversions, and propose repairs with fresh
independent review. It can follow an active queue without changing original
results. Export its explicit immutable snapshot with `--recovery`; unresolved
items remain excluded from training.

To change concurrency on an existing run, use the host with write access to its
artifact directory. The coordinator reads worker count at startup, so editing
its config alone does not resize a running pool:

```bash
python src/resize_workers.py --root "$CARD_INTENT_ARTIFACTS/detailed" --workers 6
python src/resize_workers.py --root "$CARD_INTENT_ARTIFACTS/detailed" --workers 6 --apply
```

The first command only prints a plan. The second drains current batches, waits
for the existing coordinator and export, preserves the model/prompt settings,
then starts one replacement coordinator with six workers and a new export path.
It requires `active-launch.json` and its recorded launch command. Existing drain
requests and failed/review batches require inspection; the tool does not override
them or reset completed work. Other corpus queues are unaffected.

Historical wording is optional and uses a separately pinned download:

```bash
python src/fetch_printings.py
# Set PRINTINGS_MANIFEST to the manifest path printed by that command.
python src/original_wordings.py --root "$CARD_INTENT_ARTIFACTS/detailed" \
  --printings-manifest "$PRINTINGS_MANIFEST" --output "$CARD_INTENT_ARTIFACTS/wordings"
python src/attach_wordings.py --root "$CARD_INTENT_ARTIFACTS/detailed" \
  --wordings "$CARD_INTENT_ARTIFACTS/wordings"

# Export only completed, reviewed batches; never write into a previous export.
python src/export_full_corpus.py --root "$CARD_INTENT_ARTIFACTS/detailed" \
  --output "$CARD_INTENT_ARTIFACTS/export-detailed"
python src/export_full_corpus.py --root "$CARD_INTENT_ARTIFACTS/broad" \
  --output "$CARD_INTENT_ARTIFACTS/export-broad"
python src/prepare_dataset.py --input "$CARD_INTENT_ARTIFACTS/export-detailed" \
  --input "$CARD_INTENT_ARTIFACTS/export-broad" --output dist/oracle-v1 --release-name oracle-v1
```

Use `--allow-partial` only for an explicitly labeled pilot. Full release assembly
requires completed input queues and a test partition. Before freezing, review
quarantined/excluded rows and ensure the source snapshot, prompts, terminology,
worker configurations and any augmentation hashes are retained. Bulk sources
change; the download hash identifies the bytes used, while replayed model calls
may produce different wording.

The portable projector reproduces all 36,163 identities, mechanic groups and
splits from the research run. It includes later fixes for coherent editable
defaults on sparse requests (for example, omitted Devoid no longer leaves a
colored cost paired with an unexplained colorless body). Consequently, fresh
projection bytes need not match the older frozen projection file. Exact pilot
reproduction uses the separately packaged, unchanged pilot inputs and targets.

## Release files

`train`, `validation`, and, for complete releases, `test` each have three files:

- `SPLIT.messages.jsonl`: `example_id`, `source_group_id`, `style`, and standard
  system/user/assistant `messages`. Use HF's default `messages` configuration.
- `SPLIT.sft.jsonl`: exactly `prompt` and `completion`; the trainer masks out the
  system/user tokens and assistant header.
- `SPLIT.jsonl`: target, description, specified/proposed fields, source identity
  and hashes, generation/reviewer provenance, and wording/name/rarity policy.

`manifest.json` pins every payload file and records counts and overlap checks.
`system-prompt.txt`, `draft-schema.json`, `split-map.json` and the dataset card
make the dataset usable independently of this codebase. The packager merges
all input styles without reweighting; use validation to select any alternative
style mix, and record that sampling policy in the training run.
