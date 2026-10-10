# Recover quarantined descriptions

Quarantine is a recovery queue. A failed surface check is not proof of a bad
description, and parser recognition is not proof of semantic fidelity.
The recovery process leaves the original generation queues and source snapshots
unchanged, including their original descriptions, verdicts, hashes, and splits.
It can run alongside generation, using a separate coordinator and output directory.
When changing recovery checks, use a new output directory and supply
`prepare --prior "$PREVIOUS_RECOVERY_SNAPSHOT"`. Valid reviews are reused only
when the original row, corrected target, and constraints match exactly. Histories
and rewrite counts are retained; this does not reset the two-rewrite allowance.

```bash
python src/corpus_recovery.py prepare --root "$CORPUS" --output "$RECOVERY"

# First review one batch and inspect its records and receipts.
python src/corpus_recovery.py run --root "$CORPUS" --output "$RECOVERY" \
  --workers 1 --max-batches 1

# Continue; --follow also collects new rows as generation completes.
python src/corpus_recovery.py run --root "$CORPUS" --output "$RECOVERY" \
  --workers 1 --follow

# Freeze an explicit snapshot before exporting or publishing.
python src/corpus_recovery.py snapshot --root "$CORPUS" --output "$RECOVERY" \
  --destination "$RECOVERY_SNAPSHOT"
python src/export_full_corpus.py --root "$CORPUS" --recovery "$RECOVERY_SNAPSHOT" \
  --output "$CORPUS_EXPORT"
```

`prepare`, `refresh`, and `snapshot` are local deterministic operations. `run`
makes authenticated Codex calls using Sol at medium effort. Its default batch
contains 24 descriptions plus eight controls. It uses the same instruction-only
cached-prefix transport as generation; each review starts without previous batch
history. The model cannot use tools, edit targets, or alter dataset membership.

The admission steps are:

1. Recompute surface checks. Check multi-face mana costs per face. Recognize
   permitted type plurals and implied types such as Aura → Enchantment. Token
   names and names appearing in the supplied mechanics are legitimate vocabulary.
   A previously passing independent review can be retained when only a false
   positive in these checks changes and the target is unchanged.
2. Audit every source projection against the pinned raw Oracle snapshot, including
   cards with accepted descriptions. Version 1 corrects abbreviated self names
   being captured by another face's alias. It retains full cross-face references
   and literal named-card filters. Any changed target requires a new review.
   Source-level problems remain excluded; the model is never asked to invent a
   corrected source target. The recovery prompt includes the verified 2025 rules
   change allowing modal double-faced permanents to transform when their other
   face is a permanent; this is not a layout conflict.
3. Reassess remaining descriptions. The reviewer may propose the smallest complete
   replacement that preserves the assigned detail level. Broad descriptions can
   remain general and informal; repairs must not turn them into exhaustive Oracle
   restatements. Harmless colloquialisms can pass unchanged.
4. Review a proposed replacement in a fresh call that sees only the constraints,
   candidate, style, and controls—not the previous verdict or proposed rationale.
   This is independent review context using the same model, not independent model
   families. Permit at most two rewrites. Unresolved descriptions stay quarantined.

Every batch must reject wrong-player, optionality, and duration controls, and pass
four omission/contradiction controls for broad descriptions, plus a positive
modal-transform control. A failed control or transport error stops the
coordinator; inspect the recorded receipts rather than
blindly retrying. Errors are stored separately from exhausted semantic repairs.

`records/` contains the original row, proposed/current row, original hash, target
change flag, every suggested repair and review receipt, and final state.
`progress.json` separates accepted unchanged descriptions, accepted rewrites,
unresolved rows, and previously accepted rows held by the source audit.
`target-audit.json` records every corrected source and source-level hold.

An export explicitly supplied with a recovery snapshot verifies its file hashes,
source manifest, original row hashes, stable IDs and splits, and deterministic
target correction. Pending, failed, exhausted, or source-held rows cannot enter
training. Partial recovery snapshots also hold subsequently generated rows for
affected source cards. Historical printed-wording augmentations for affected
cards remain held for source-level revalidation; this process never rewrites
historical text to satisfy a reviewer. Names and rarity are added afterward by
the existing export steps. Original generation progress counts remain unchanged.

Recovery artifacts, prepared datasets, and model receipts belong outside Git.
Publish only the validated dataset release on Hugging Face; retain recovery
provenance and report both original and recovered acceptance numbers.
