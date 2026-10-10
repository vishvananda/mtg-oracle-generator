# Custom-card source and wording pilot

A fixed sample of 500 MSEM custom designs produced **137 complete mtgish parses
(27.4%)** with unchanged rules text, and **151 (30.2%)** after conservative
wording and typography normalization. Fourteen failures were recovered and no
previously accepted card was lost. This is a source feasibility measurement,
not model accuracy or a new training release.

After removing three vanilla cards and two exact parsed duplicates of official
cards, **146 cards with rules text remain as review candidates**. Their original
text, normalized text, source attribution, exact changes, and parser diagnostics
are retained locally. These third-party card records are not checked into Git or
published to the training dataset.

## Source and sampling

The source is MSEM's [published AllSets export](https://mse-modern.com/msem2/notlackey/AllSets.json),
linked from its [joining and review documentation](https://snapdragonfirework.wixsite.com/msem2/join-msem2).
The downloaded file has 21,194 records across 87 sets. Its server Last-Modified
header is November 1, 2025; this is the accessible export snapshot, not a claim
that it includes every current MSEM design. [Source receipt](source.json) pins
its URL, SHA-256, byte count, and HTTP metadata.

The importer excludes 836 multiface records, 1,139 official-name matches and
281 mechanical matches against the October 6, 2026 Oracle snapshot. It collapses
3,969 custom duplicates, leaving **14,969 eligible single-face designs**. Known
printing suffixes such as `_SET` and `(SL123)` are removed only when the base
name independently appears in this source or Oracle; every change is recorded.
Rules text, costs, and type lines are preserved at this stage. Name and text
matching alone cannot detect all functional reprints.

Sampling is uniform without replacement, seed `20261009`, across normal, saga,
leveler, and class layouts. Unknown keywords and subtypes are not filtered out.
The sample covers 80 sets. All 500 scheduled cards remain in every acceptance
denominator. The unchanged upstream parser revision is
`153451e44d88baabfeb9902092032c328f4d5416`, using upstream Scryfall preprocessing,
generated plural grammars, names, find/replace rules, and whole-card parsing.
No Arena text is substituted for custom designs. No accepted card required an
upstream find/replace correction. See [baseline metrics and hashes](summary.json).

## Wording findings

| Input condition, same 500 cards | Accepted |
| --- | ---: |
| Original rules text | 137 / 500 (27.4%) |
| Historical enter/enters wording normalized | 145 / 500 (29.0%) |
| Add quote typography, loyalty minus glyph, and one narrow self-reference pattern | 147 / 500 (29.4%) |
| Add explicit sacrifice and library-placement choices | 151 / 500 (30.2%) |

The final pass changed 46 cards and recovered 14:

| Rewrite family | Newly accepted | Example |
| --- | ---: | --- |
| Shorten enter/enters the battlefield | 8 | Shadowcleft Hideout |
| Explicit sacrifice choice | 2 | Kevthra's Command; Kill This Love |
| Explicit owner's top/bottom choice | 2 | Tidal Cacophony; The Tideshift |
| Curly double quotes to straight quotes | 1 | The Blood Sun Sets |
| Named creature to `this creature` in its renowned condition | 1 | Daring Cavalier |
| ASCII negative loyalty cost to Unicode minus | 0 | Koth's parse advances but its ultimate still fails |

Wizards documents the nonfunctional enter wording change in the
[Bloomburrow release notes](https://media.wizards.com/2024/downloads/BLB_Release_Notes_DgtOCtpFAl/EN_BLB%20Release%20Notes%2020240611.pdf).
The [Foundations update](https://magic.wizards.com/en/news/announcements/foundations-update-bulletin)
documents reduced name self-references and explicit sacrifice/library choices.
The owner-choice rewrite applies only where the owner already chooses; it does
not change a bare instruction in which the resolving effect's controller chooses.
Self-reference cleanup is limited to a named creature's renowned condition,
outside quoted abilities. It is not a global name replacement across zones.

The 2021 changes to mana value, activation restrictions, and shortened shuffle
instructions are also documented in the [Oracle update](https://magic.wizards.com/en/news/announcements/oracle-changes-2021-04-20).
The inspected failures did not contain the old mana-value or activation wording.
These were not added as unmeasured repair rules.

The first 20 passing cards were spot-reviewed: they use familiar rules concepts
and showed no obvious undefined custom mechanic. This review does not establish
balance or complete semantic correctness. Among the first 25 failures, observed
issues included five new concepts/types, four historical enter wordings, three
custom ability-word labels, and thirteen standard-vocabulary wording/composition
failures. These are descriptions of the inspected cases, not guaranteed root
causes or estimates for all failures. [Spot-review record](spot-review.json).

Ten additional selected paraphrase/label probes recovered only Trainer's Eyes
after removing its `Vanguard` label. That remains separate from automatic
cleanup and the 146 candidates pending confirmation of source-set rules.
Other label removals merely exposed a later grammar limitation. `Harmony` and
`Discovery` were deliberately retained: their source text assigns them rules
meaning, so they cannot be treated as decorative labels. Unknown keywords such
as `Engorge` also remain intact. [Probe outcomes](rewrite-probes.json).

All five colors, multicolor, and colorless designs occur in the accepted sample.
After normalization, eight lands pass, compared with none beforehand. None of
the nine planeswalkers passes: eight stop at unfamiliar planeswalker subtypes;
Koth still fails after loyalty typography is corrected. A parser-only filter
would therefore underrepresent these designs. Keep rejected records for further
review rather than labeling them invalid cards.

Finally, comparing accepted mtgish structures with official parsed cards, while
ignoring only the top-level name, identifies Tranquil Stream as Idyllic
Beachfront and Rainforest Spring as Tangled Islet. This catches reprints missed
by raw wording comparison. [Candidate-filter receipt](candidate-filter.json).

## Reproduce

Use Python 3.11+ and the existing standalone parser setup. Obtain the official
Oracle JSONL snapshot using the fetch steps in [dataset documentation](../../docs/dataset.md).
All commands run at the repository root; output directories must be new.

```bash
export CARD_INTENT_ARTIFACTS="$PWD/artifacts"
mkdir -p "$CARD_INTENT_ARTIFACTS/custom-card-pilot"
curl --fail --location https://mse-modern.com/msem2/notlackey/AllSets.json \
  --output "$CARD_INTENT_ARTIFACTS/custom-card-pilot/AllSets.json"
python src/setup_mtgish.py

python src/assay_custom_cards.py \
  --source "$CARD_INTENT_ARTIFACTS/custom-card-pilot/AllSets.json" \
  --official "$CARD_INTENT_ARTIFACTS/full-oracle/oracle-cards-20261006090155.jsonl.gz" \
  --sample-size 500 --seed 20261009 \
  --output "$CARD_INTENT_ARTIFACTS/custom-card-pilot/baseline"

python src/assay_custom_wording.py \
  --cases "$CARD_INTENT_ARTIFACTS/custom-card-pilot/baseline/cases.jsonl" \
  --rules enters quotes loyalty-minus renowned-self sacrifice-choice owner-choice \
  --output "$CARD_INTENT_ARTIFACTS/custom-card-pilot/normalized"

python src/filter_custom_candidates.py \
  --cases "$CARD_INTENT_ARTIFACTS/custom-card-pilot/normalized/cases.jsonl" \
  --official "$CARD_INTENT_ARTIFACTS/full-oracle/oracle-cards-20261006090155.jsonl.gz" \
  --official-parsed "$CARD_INTENT_ARTIFACTS/mtgish-standard-workflow/data/mtgish.lines.ron" \
  --output "$CARD_INTENT_ARTIFACTS/custom-card-pilot/review-candidates"

python -m unittest discover -s tests -p test_custom_card_assay.py -v
```

A changed upstream download will change the sample; check `source.json` before
comparing results. The final JSONL holds parser-accepted review candidates, not
ready-made instruction/completion pairs. Next preparation work is semantic
review, a source reuse decision, then description generation and grouping all
versions of each design together for any train/test split. Historical wording
can be retained as an additional input paired with a reviewed modern target.
