# DPO round two: collection, training and reproduction

This round starts from the completed 19,774-update SFT adapter, not from the
six-update DPO pilot. The policy and frozen reference initially contain the same
SFT weights. The final adapter includes that SFT starting point: load it once on
the original base, without stacking another SFT adapter.

## Preference collection

| Stage | Requests | Samples per request | Proposed pairs | Approved pairs |
| --- | ---: | ---: | ---: | ---: |
| Initial error-enriched audit | 128 | 4 | 54 before audit refinements | 43 |
| Broad batch 1 | 2,048 | 4 | 311 | 261 |
| Broad batch 2 | 2,048 | 4 | 320 | 267 |
| Broad batch 3 | 2,048 | 4 | 269 | 218 |
| Broad batch 4 | 2,048 | 4 | 278 | 218 |
| Total approved | | | | **1,007** |

The four broad batches contain 8,192 disjoint source-card families and 32,768
completions. The initial audit was enriched for failures; its yield is not a
representative collection rate. We retained one pair per family. The merge
rounded the total down to 1,000 for full batches of eight, preserving all 1,007
approved source pairs in the published source audits. The training set has
675 sampled-Qwen positives and 325 Sol repairs; negatives are actual sampled
Qwen failures. No second recovery pass was required to reach the target.

Six locally authenticated Codex workers use GPT-6.1 Sol at medium effort for
requirement extraction, initial review, repair and repair review. Calls share a
stable cached prefix and group up to nine items. A Luna critic reviews each
proposed pair; every proposed pair then receives a separate, blinded Sol review
at high effort. The final review is required even when initial reviewers agree.
Blinding hides generator, candidate role, source answer and parser result.
These are model labels from correlated models and sessions, not expert human
annotations. A parser pass cannot compensate for changing the user's intent.

The four broad batches' saved stage usage receipts report 60,185,018 input
tokens, including 43,703,936 cached tokens (72.6%) and 16,481,082 uncached tokens.
They report 3,918,690 output tokens, of which 1,020,271 are reasoning output.
These sums exclude the initial audit/refinements, evaluation and prefix setup
not included in those stage receipts. They are subscription/CLI usage records,
not a claim of API billing cost. [Stage-level usage](collection-usage.json)
provides the exact scope and values.

The first sampling allocation exhausted GPU memory at batch size 48 after
1,968 completions. Recovery preserved those rows and generated only missing
candidate slots. Subsequent allocations started at 24 and could halve on CUDA
OOM. This is not a bit-identical replay: batch-derived seeds and exact resume
provenance are recorded. Sampling overlapped the preceding batch's review.

## Training recipe and measured behavior

| Setting | Value |
| --- | --- |
| Base | `Qwen/Qwen3-4B-Instruct-2507` |
| Base revision | `cdbee75f17c01a7cc42f958dc650907174af0554` |
| Initial SFT checkpoint repository | `vishvananda/mtg-oracle-qwen3-4b-checkpoints-20261007` |
| Initial SFT revision | `decd0eb5344719c298649ca4cf2be4950da4b18d` |
| Method | DPO with frozen SFT reference; NF4 QLoRA |
| LoRA | Rank 16, alpha 32; attention and MLP projection modules |
| Pairs / passes / updates | 1,000 / 1 / 125 |
| Effective batch | 8 |
| Learning rate / beta | `5e-6` / `0.1` |
| Maximum sequence length | 4,096; CPU preflight rejects truncation |
| Seed | 17 |
| Saved checkpoints | 25, 50, 62 (midpoint), 75, 100, 125 |
| GPU | A100 80 GB (`a100-large`) |
| Training runtime | 823.4 seconds including training wrapper; trainer: 820.1 seconds |
| Peak allocated GPU memory | 17.37 GiB |
| Mean training loss | 0.57425 |

Average loss was 0.64194 over the first 25 updates and 0.54211 over the last 25.
The corresponding training reward preference accuracy went from 55% to 77%.
Those windows contain different training pairs; neither value measures held-out
card accuracy. Use the measured generation report for that comparison.

Integrity checks confirm that initial policy/reference logits matched, reference
weights remained unchanged, final policy weights changed, and all planned steps
completed without a budget stop. The training job was
[`6ac9396cfee2c9007017bfd6`](https://huggingface.co/jobs/vishvananda/6ac9396cfee2c9007017bfd6).
Its allocation cost bound is $1.083342. The entire round's conservative GPU cost
bound is **$21.416838**, including the failed sampling allocation. These bounds
use allocated minutes rounded up plus two minutes, capped at the job timeout;
they are not reconciled billing invoices and exclude authenticated CLI usage.
See [compute receipts](compute-costs.json).

## Evaluation protocol

Freeze separate development and locked release panels before viewing model
outputs: 128 requests and 96 families per panel. Half the requests describe
source cards held out of preference collection; half are new design requests
with broad/detailed variants grouped by family. Source cards were present in
SFT, so this measures new-request generalization, not unseen-card knowledge.
The historical 400-card SFT final test was not reused to select this DPO model.

NF4 diagnostics compare SFT, midpoint and final DPO. Production evaluation
compares the SFT and preselected final DPO Q4_0 exports. Both arms use the same
frozen workshop system prompt and terminology guide, greedy decoding, seed 17,
2,048 output tokens and unconstrained JSON generation. There is no best-of-two
selection or repair in these scored generations. The interactive two-candidate
feature uses sampling and is a separate serving mode, not the scored protocol.

The reports distinguish schema validity, explicit metadata failures, Sol-judged
intent, clean Oracle wording, mtgish acceptance, faithful-and-parsed outputs and
reviewer abstentions. mtgish uses its upstream whole-card flow and pinned,
unmodified grammar. Infrastructure errors are counted separately from grammar
rejections. Family-level paired bootstrap intervals use 5,000 resamples.
The final checkpoint is fixed before locked release evaluation; midpoint is
diagnostic only. Keep the release panel frozen for reproduction, not tuning.

On the 128-request greedy development comparison, 59 complete card JSON objects
and 78 Oracle-text strings were unchanged between SFT and DPO. All 59 identical
cards received the same intent result. This rules out contradictory labels on
identical outputs as the explanation for that panel's intent-score difference;
it does not make the judge infallible. [Consistency diagnostic](development-judge-consistency.json)
preserves these counts without changing the frozen scores.

## Artifacts and entry points

The repository holds code, small provenance files, CSVs and plots. Prepared
examples, raw sampled cards, per-case audits and LoRA weights belong on Hugging
Face. Publication excludes raw worker conversations, credentials, optimizer
state, reference adapters and private editor state.

- Dataset: `vishvananda/mtg-oracle-preferences-v2`, standard conversational
  TRL `prompt` / `chosen` / `rejected` rows in `preferences.jsonl`.
- Model: `vishvananda/mtg-oracle-qwen3-4b-dpo-round2-20261009`, final adapter and
  all six saved checkpoints. Immutable publication revisions are recorded in
  the report's publication receipts and root `release-status.json`.
- [Collection and orchestration instructions](../../docs/preference-round-two.md).
- `src/rl_gpu.py` and its `.lock` file define the GPU environment and actual
  training implementation; [exact run config](training-config.json) records
  the settings used here.
- `src/rl_teacher.py` implements the review/repair flow; the rubric and prompts
  are in `prompts/reward-judge-v3.txt`, `intent-checklist-v1.txt` and
  `intent-repair-v1.txt`.
- `src/rl_round_expand.py` keeps source families disjoint and merges approved
  pairs. `src/rl_round_run.py` orchestrates collection through publication.
- `src/export_gguf.py` verifies the saved adapter against the final policy hash,
  safely merges into the pinned BF16 base, exports F16 GGUF and quantizes once.
- `src/rl_local_compare.py` and `src/rl_serving_eval.py` reproduce serving
  generations and blinded scoring. `src/rl_round_publish.py` builds reports,
  explicit publication allowlists, and verifies uploaded file hashes.

For a larger model, reuse the public conversational preference rows with a
matching SFT policy/reference and an appropriate TRL DPO configuration. Do not
load this Qwen 4B adapter into a different architecture. Recompute token lengths
with the new tokenizer and retain the evaluation-family exclusions. The
original SFT description corpus is also available separately for training that
larger starting policy; see the root README.

## Future user preferences

The workshop can generate two local continuations in a single llama.cpp request
(`n=2`), sharing prompt processing and using two decoding slots. The interactive
recipe uses temperature 0.35, top-p 0.9, top-k 40, min-p 0.05, a recorded random
request seed and a 2,048-token limit. llama.cpp offsets the child seed. Local
display order is randomized and saved; model labels and parser checks are visible.
This is not a blinded human evaluation.

Only explicit choices are saved. Automatic application of a preview is not a
vote. Each record includes the original description, presented order, original
drafts/raw outputs, model hash/settings, validator results and chosen version.
No-preference events are retained; identical drafts, missing alternatives and
Luna selections do not become local-vs-local training pairs. Later editor
changes are not silently substituted into the chosen response.

Records remain in local storage under each comparison's `choices/` directory,
separate from the released training corpus. They require review, deduplication
and family-based splitting before training or publication. A preferred design
does not imply the alternative is invalid, and repeated clicks or repeated
requests must not overweight a user or mechanic.

The host's automatic smoke comparisons are listed separately in
`live-head-to-head/excluded-smoke-comparisons.json`; their clicks are test
fixtures and must not enter user-preference training.

## Interactive probes and known failures

These named user regressions are smoke diagnostics, not another representative
test set. Their complete outputs and all validator results are retained in
[serving-probes.json](serving-probes.json). An initial temperature-0.65 probe
produced nonsensical planeswalker text; the interactive default was reduced to
0.35. Its failure is retained in [initial-sampling-probe.json](initial-sampling-probe.json).
Neither change affects the frozen greedy evaluation.

| Request | One greedy draft | Two sampled drafts | mtgish acceptance |
| --- | ---: | ---: | --- |
| Rummaging Giant | 8.16 s | 11.61 s | Single and both alternatives accepted |
| W/U/B creature playing from opponents' libraries | Not measured in this probe | 17.71 s | Both rejected |
| Planeswalker making a 3/3 and exiling opponents' libraries | 17.02 s | 28.28 s | Single and both alternatives rejected |

The two-Giant request took about 42% longer than the single generation on this
host. This is one ordered, cache-affected observation under concurrent evaluation
load, not a controlled throughput benchmark. llama.cpp's shared prompt/child
slot implementation reduces prompt work, but each extra answer still needs
decoding. Some precise requests yield identical drafts and no usable preference.

The library design incorrectly puts an opponent-owned card into the controller's
hand/graveyard instead of implementing permission to play it. Planeswalker
outputs add incorrect shuffle, regeneration or library-return clauses. These
are real remaining semantic failures. Rummaging-Giant variants are coherent
and demonstrate editable unspecified costs/stats. Do not describe successful
training or lower DPO loss as proof that these harder requests are fixed.

The chosen-card artwork path was exercised with a real Luna prompt and generated
image. It records the selected draft hash and immutable image URL, imports only
while that card is still selected, and keeps **Luna Imagen** as the artist.
An explicit button starts that request; switching card versions does not.
