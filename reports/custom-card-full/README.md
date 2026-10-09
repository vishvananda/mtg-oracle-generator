# Full MSEM custom-card collection

This expands the [500-design pilot](../custom-card-pilot/README.md) to all
**14,969 eligible unique single-face designs** in the same frozen MSEM export.
The collection is for review before description generation and targeted SFT.
Parser acceptance is not semantic approval or model accuracy.

**3,852 distinct cards with rules text remain as review candidates**, spanning
84 source sets, all five colors, multicolor, and colorless designs.

| Stage | Cards |
| --- | ---: |
| Eligible unique single-face designs checked | 14,969 |
| Complete parse with original rules text | 3,729 (24.91%) |
| Complete parse after conservative cleanup | 3,997 (26.70%) |
| Remove additional exact official-card matches | −63 |
| Collapse additional exact custom duplicates | −9 |
| Exclude remaining cards without rules text | −73 |
| Final review candidates | **3,852** |

Cleanup changes 1,839 cards and recovers 268 complete parses: 184 from enter
wording, 61 from double-quote typography, 18 from explicit sacrifice choice,
three from renowned self-reference, and two from explicit owner choice.
No accepted card is lost. Neither pass encounters a parser infrastructure error,
and no accepted card needs an upstream find/replace correction.

The baseline takes 412.18 seconds and cleanup 55.88 seconds with 12 CPU parser
workers. This timing excludes import, final filtering, and subsequent diagnostics.
All 500 pilot cards reproduce exactly in both passes (draft and parser result,
excluding elapsed timing), despite the different order and parallel execution.
[Baseline receipt](baseline.json), [cleanup receipt](wording-normalization.json),
[duplicate filter](candidate-filter.json), [coverage and integrity checks](coverage.json).

| Candidate card type | Count |
| --- | ---: |
| Creature | 2,108 |
| Artifact creature | 131 |
| Enchantment creature | 44 |
| Instant | 399 |
| Sorcery | 289 |
| Enchantment | 337 |
| Artifact | 294 |
| Land | 249 |
| Enchantment land | 1 |

Counts combine legendary and nonlegendary versions. The pool includes four
Sagas and 57 cards with at least four rules-text lines; line count is a rough
text-length indicator, not a count of distinct mechanics. None of the 264
planeswalkers passes unchanged or after cleanup. All 10,972 rejected cards,
including those planeswalkers, remain in a separate review queue.


## Method and scope

The [source receipt](source.json) pins the downloaded AllSets export, which has
21,194 records across 87 sets. The import removes official-card matches and
duplicate custom printings before parsing. As in the pilot, 836 multiface
records are outside this importer's scope; supported source layouts are normal,
saga, leveler, and class. This is exhaustive over the eligible collection, not
a sample and not a claim to cover every card currently on MSEM.

Twelve local parser workers each own a separate persistent Go subprocess.
Results are written in stable design-ID order. The mtgish revision, grammar,
upstream Scryfall preprocessing, name handling, and whole-card validation are
unchanged from the pilot. The normalization pass reparses only cards with
changes, preserving both versions and both parser outcomes.

The same six narrowly scoped cleanup rules apply: shortened enter wording,
double-quote typography, loyalty minus typography, a named creature's own
renowned condition, explicit sacrifice choice, and explicit owner's library
placement choice. The [pilot report](../custom-card-pilot/README.md#wording-findings)
documents their scope and Oracle references. Unknown mechanics and custom
ability labels remain intact. No new repair rules were selected using this run.

After parsing, exact mtgish structures are compared with official cards,
ignoring only the top-level name. The same comparison also collapses custom
duplicates exposed by cleanup or equivalent wording. Every equivalent design's
ID and source attribution remain attached to its representative; full original
records remain in the case files. Named-card references inside rules remain
significant. This conservative equality check does not detect all functional
reprints or closely related designs.

Candidate files contain original and normalized drafts, source/designer
attribution, exact edits, complete parser output, and a structural
`design_family_id`. They have no generated descriptions, semantic approval,
or train/test assignment yet. Raw third-party card records stay outside Git
and have not been uploaded to Hugging Face. Only code and measurement reports
are published here.

## Reproduce

Use the standalone parser setup and Oracle snapshot described in the
[pilot instructions](../custom-card-pilot/README.md#reproduce). Output directories
must be new. `--workers 12` controls CPU parser processes, not model workers.
The commands make no model calls and rent no GPU.

```bash
export CARD_INTENT_ARTIFACTS="$PWD/artifacts"
export CUSTOM_SOURCE="$CARD_INTENT_ARTIFACTS/custom-card-pilot/AllSets.json"
export CUSTOM_OUTPUT="$CARD_INTENT_ARTIFACTS/custom-card-full"
export ORACLE_SOURCE="$CARD_INTENT_ARTIFACTS/full-oracle/oracle-cards-20261006090155.jsonl.gz"

python3 src/assay_custom_cards.py \
  --source "$CUSTOM_SOURCE" --official "$ORACLE_SOURCE" \
  --all --workers 12 --output "$CUSTOM_OUTPUT/baseline"

python3 src/assay_custom_wording.py \
  --cases "$CUSTOM_OUTPUT/baseline/cases.jsonl" \
  --rules enters quotes loyalty-minus renowned-self sacrifice-choice owner-choice \
  --workers 12 --output "$CUSTOM_OUTPUT/normalized"

python3 src/filter_custom_candidates.py \
  --cases "$CUSTOM_OUTPUT/normalized/cases.jsonl" \
  --official "$ORACLE_SOURCE" \
  --official-parsed "$CARD_INTENT_ARTIFACTS/mtgish-standard-workflow/data/mtgish.lines.ron" \
  --deduplicate-custom --output "$CUSTOM_OUTPUT/review-candidates"

python3 src/report_custom_collection.py \
  --baseline "$CUSTOM_OUTPUT/baseline" \
  --normalized "$CUSTOM_OUTPUT/normalized" \
  --candidates "$CUSTOM_OUTPUT/review-candidates" \
  --output "$CUSTOM_OUTPUT/coverage"

python3 -m unittest discover -s tests -p test_custom_card_assay.py -v
```

The coverage command checks hashes, stage relationships, ID uniqueness, and
preservation of every original draft and raw parser result. It also exports
rejected, recovered, and lost-acceptance review queues. Optional
`--pilot-baseline` and `--pilot-normalized` directory arguments compare the
earlier 500 cards against the full run; timings are excluded from that comparison.

The next stage is semantic review and varied descriptions, with all equivalent
versions of each design grouped together for evaluation. Parser-rejected cards
remain available for targeted review, particularly where unsupported types or
mechanics would otherwise leave a coverage gap.

## Planeswalker diagnosis

All **264 planeswalkers** fail complete parsing, including two with an additional
card type. A direct check of mtgish's type grammar finds **251 with unrecognized
subtypes**, 12 with known subtypes, and one without a subtype. The earlier quick
count of 252 treated that subtypeless card as an unknown subtype; 251 is the
audited count. The pinned grammar enumerates 88 planeswalker subtypes.

As a diagnostic only, replacing each unknown subtype list with `Jace`, while
preserving normalized rules, allows three full cards to parse: Seto San, Summer
Blossom; Jenn, the Rising Dawn; and Alonzo, Departed Detective. These modified
controls are **not** included in the 3,852 candidates or acceptance rates.
Allowing custom subtypes would remove an early obstacle, but most cards then
encounter another unsupported construction.

Eight cards were inspected with individual-ability controls. All eight accept
a simple draw ability with a known type, showing that the parser supports
planeswalker cards and loyalty abilities in general.

| Card | What the probes establish |
| --- | --- |
| Seto San; Jenn | Unknown subtype is the remaining blocker after existing typography cleanup; complete rules parse with the diagnostic known subtype. |
| Koth, One True Hero | ASCII negative loyalty costs fail; Unicode minus allows its first two abilities to parse. The ultimate fails at token keywords. Reordering keywords does not help; deleting them passes but changes the card. |
| Tamiyo, Lunar Arcanist | Scry and untap clauses each parse individually, but their combined loyalty ability fails. Changing the period to “, then” also fails. This is a composition gap, not evidence of a new mechanic. |
| Elspeth, Protector of the Meek | An indestructibility ability fails at its next-turn duration, and the ultimate fails at its second quoted emblem ability. Shortening the duration passes but changes the card. |
| Iroshi, Blade of the Old Ways | After subtype substitution, the whole card stops in the loyalty-timing clause; a Ninja-token ability and a damage ability parse individually. |
| Vetas of Distant Groves | Its custom Augur action remains unsupported. The other two abilities also fail independently. |
| Irbek, Crocodile Hunter | Token creation parses independently; copying the named Adventure and animating lands fail. Its source also embeds Adventure text in a record labeled normal, needing import/semantic review. |

The findings combine source inspection and controlled parser outcomes; reported
farthest-match tokens alone are not guaranteed root causes. No abilities were
deleted or weakened in collection candidates. A useful parser extension would
register custom subtype names without replacing their identities, followed by
work on ordinary action composition. It should keep unsupported mechanics
distinct from malformed rules text.

[All-subtype controls and eight-card spot checks](planeswalker-probes.json),
[targeted wording and clause controls](planeswalker-wording-probes.json).
Full original/control drafts and diagnostics remain in local artifact JSONL.
The broad probe is reproducible with:

```bash
python3 src/probe_custom_planeswalkers.py \
  --normalized "$CUSTOM_OUTPUT/normalized" \
  --workers 8 --output "$CUSTOM_OUTPUT/planeswalker-probes"
```
