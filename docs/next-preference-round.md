# Next improvement round after the DPO pilot

Recommendation, 2026-10-09: **collect better Sol-reviewed repair/preferences before
another full supervised epoch**. The [first pilot](../reports/dpo-pilot-v1/README.md)
had 47 pairs and six updates. It verified the training machinery, but changed no
Oracle text on its 64-case development panel. That panel is too small, correlated
and easy on intent to establish whether substantial preference training helps.

The user authorized this experiment after the pilot deployment. The implemented
workflow and initial audit are in [preference round two](preference-round-two.md).
No second full SFT epoch is part of this round.

## 1. Establish a useful baseline before generating more labels

Freeze 256 new evaluation requests grouped into disjoint mechanic/card families:
128 development cases for checkpoint decisions and 128 locked release cases.
Keep every paraphrase, detail level and controlled variant of a family together.
Exclude these families from the next preference/repair corpus and do not use the
400-case SFT final test for iterative decisions. Newly held-out requests based on
SFT training cards measure new-request generalization, not unseen-card knowledge;
report that distinction. Keep the existing 64 cases as historical diagnostics.

Cover all major card types, all five colors, colorless and multicolor designs,
with explicit planeswalker, multi-face, linked-exile, opponent-library,
modal/once-per-turn, cost-versus-effect and rare-mechanic slices. Include vague
ideas, spelling mistakes, old wording that still expresses current rules, and
precise requests for name, rarity, loyalty, targets and amounts. Include negative
constraints such as “no other abilities.” Avoid making the panel mostly numeric
variants of a few templates. At least half should be original design requests
rather than descriptions of an identifiable existing card.

Compare the completed SFT and pilot DPO on the **same Q4_0 artifact recipe,
workshop system prompt, terminology guide, decoding and token limit**. Run the
same requests with the frozen NF4 recipe to identify serving/prompt regressions.
Do not change the prompt and the model in the same comparison. Include the
original user regressions as named diagnostic cases, not independent evidence.

## 2. Improve the labels in a small first batch

Start with 128 training-only requests, emphasizing natural errors from the
current generator. Sol extracts an atomic requirement checklist from the request
before viewing any candidate. Separate explicit requirements from optional
defaults. Sol may repair a failed candidate with schema and mtgish feedback, but
must preserve every requested mechanic even when the parser does not support it.

Use a separate blinded review call for the repaired and original outputs. Hide
generator identity, source-card status, parser result, repair rationale and pair
role. The reviewer sees the request and output; deterministic checks enforce
exactly specified name, rarity, stats and starting loyalty. A separate Sol call
is operational separation, **not an independent model or human label**. Use Luna
as an additional critic and explicitly adjudicate disagreements; neither model
gets an automatic veto. Audit a random sample and all critical disagreements.

Calibrate on real mistakes as well as constructed mutations. The pilot's
219/219 synthetic judge agreement failed to predict its many natural false
rejections. Specifically protect equivalent wording, omitted reminder text,
basic-land implicit mana abilities, reasonable defaults and editable names.
Never penalize a broad idea merely for not reconstructing its source card.
Exclude inconsistent or uncertain labels; route repairable cases to repair
instead of silently discarding their useful information.

Measure accepted pairs per request, repair success, reviewer disagreement,
false rejection and cached/uncached input plus output token usage. Reuse the
stable prefix and current batches of nine, with only requests/candidates varying.
Use Sol at medium effort initially; escalate only ambiguous rules cases. Inspect
actual CLI usage receipts rather than inventing a dollar price for subscription
tokens. Finish this audit before scaling the same process.

## 3. Build 1,000–2,000 useful pairs in measured batches

Sample four Qwen outputs per training request. Prefer a faithful Qwen sample as
the positive. When every sample fails, obtain a Sol repair, review it blindly,
and record that its positive came from a teacher. Source Oracle targets may be
positives only after the same request-specific review. Negatives should mostly
be real Qwen failures, with a separately labeled minority of controlled semantic
mutations. Do not manufacture false preferences between two acceptable drafts.

Balance collection across card/mechanic families and detail levels. Start with
one pair per family; cap any later variants to prevent a few repeated abilities
dominating. Retain successful but unsupported mechanics. A parser penalty must
not reward replacing exile with mill, changing the player, or removing an
ultimate. Treat parser infrastructure errors separately from grammar rejection.

The target is **verified pairs**, not a promise that 2,000 prompts will yield
2,000 pairs. The first pilot yielded only 47 pairs from 512 requests. Sol repairs
may improve yield; measure that in the 128-request batch before setting an ETA.
Publish standard TRL rows and provenance/audits on Hugging Face, with code and
small reports on GitHub. Keep eval families out of all teacher/reviewer training
exports, including repair data.

## 4. Train a bounded comparison, then decide

Start from the completed SFT checkpoint again, retaining a frozen SFT reference.
Use the existing pinned QLoRA/DPO environment, beta 0.1 and learning rate 5e-6
for the first larger run, rather than tuning several variables at once. With
effective batch eight, 1,000–2,000 pairs give **125–250 updates for one pass**.
Raise the pilot's 100-step ceiling explicitly; otherwise it truncates that pass.
Save every 25 steps and evaluate a preselected mid-run and final checkpoint on
the development panel. Do not repeatedly select against the locked release panel.

The pilot averaged roughly five training seconds per update, but six updates
on short examples are a weak runtime estimate. Linear extrapolation is about
10–21 minutes of training; allocation startup, reference-logprob caching,
longer examples, evaluation and candidate generation are additional. Benchmark
the larger prepared set before scheduling a maximum-duration job. Candidate
generation and Sol review are likely the larger costs; record them separately.

If all candidates for important abilities fail, preserve reviewed Sol repairs
as a small targeted SFT dataset with diverse original-corpus replay. Compare
targeted SFT and DPO from the same completed SFT starting point. If chosen-answer
likelihood deteriorates, a later RPO ablation can add a supervised term on chosen
answers; the pinned [TRL 0.23 DPO implementation](https://huggingface.co/docs/trl/v0.23.0/dpo_trainer)
supports `rpo_alpha`. This is an option, not part of the first larger recipe.

## 5. Decide from generated cards, not the reward chart

Primary outcome: fraction preserving **all explicit intent constraints**.
Also report schema success, critical semantic error rate, mtgish acceptance,
faithful-and-parsed rate, valid-but-unsupported outputs, abstentions, added
unrequested mechanics, and latency. Separate teacher judgment from deterministic
checks. Bootstrap paired differences by family, keeping correlated variants
together, and show slice counts rather than only aggregate percentages.

Promote only if the deployed Q4_0 comparison improves intent with no new critical
regressions on canaries and no material schema/rare-mechanic regression. A small
or uncertain difference means gather more evidence, not declare a win. Run the
locked panel once after choosing the recipe; retain every output and parser
revision. Report the pilot's 64-case scores and the SFT's 400-case results under
their own denominators, never as interchangeable accuracy measurements.

Consider another full supervised epoch if the broader evaluation shows failure
to express Oracle syntax or mechanics even among multiple samples, and targeted
repairs do not fix it. If good candidates already occur but greedy selection is
wrong, preference data is the more direct next experiment. Another pass over
the same corpus cannot by itself repair mislabeled judgments or a mismatched
serving prompt.
