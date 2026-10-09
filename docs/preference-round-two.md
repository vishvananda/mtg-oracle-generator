# Preference round two

The six-update pilot verified DPO training but showed no measured Oracle-text
gain. This round collects stronger intent labels before spending on another
full supervised epoch. The workshop continues to serve the existing pilot and
Luna while the experiment runs.

## Initial audit

The 128-request audit reused four SFT samples per request. It deliberately
overrepresented errors, so its pair yield is not a forecast for the broad run.
Sol initially proposed 54 pairs; screening and Luna criticism retained 51.
An agent spot-check excluded two ambiguous contrasts and repaired four weak
positives. A fresh, blinded, high-effort Sol review retained **43 pairs**:
26 sampled positives and 17 Sol repairs. These are machine labels, not human
expert annotations. Six additional pairs were conservatively excluded for
uncertain negatives or imperfect positive wording.

The audit improved the rubric around broad requests, editable defaults,
net card advantage, subtype replacement, unusable requested interactions and
extraneous broken rules. For example, a requested compleated planeswalker's
life/loyalty interaction needs a relevant Phyrexian mana cost. Reminder text,
equivalent wording and parser coverage gaps are not intent failures.

Every expansion pair now receives a high-effort final Sol review. A separate
Luna critic provides disagreement evidence. Neither parser acceptance nor a
single judge verdict can establish official rules correctness or balance.

## Collection and split

Start with 2,048 new source families, four sampled Qwen completions each.
Balance card types, color combinations and description detail; include explicit
name/rarity exercises. If necessary, add disjoint batches until there are
1,000–2,000 accepted pairs, stopping after three batches for review if yield is
insufficient. Each family contributes at most one pair. Keep actual sampled
failures as negatives; prefer good Qwen positives, otherwise use a Sol repair.

Before viewing outputs, freeze two panels of 128 requests/96 families each:
development and locked release. Half the requests come from source cards held
out of all preference collection; half are novel designs at two detail levels.
Related detail levels stay together. Source cards appeared during SFT, so the
panels measure new-request generalization rather than unseen-card knowledge.
The old 400-case SFT final test is not used for iterative decisions.

## Training and evaluation

Start from the completed SFT adapter on pinned Qwen3-4B-Instruct-2507.
Use NF4 QLoRA, a frozen SFT reference, DPO beta 0.1, learning rate 5e-6,
effective batch eight, and one pass (125–250 updates). Round pair count down to
a full batch. A CPU preflight rejects any preference that would be truncated.
Save every 25 updates, at the midpoint, and at completion. Record midpoint and
final development generations; the completed checkpoint is preselected for the
locked release evaluation rather than chosen by searching release scores.

Compare SFT and DPO with identical production prompts, terminology guide,
greedy decoding, 2,048-token output limit and Q4_0 export recipe. Report NF4
development results separately. Metrics distinguish schema validity, explicit
metadata failures, judged intent, clean Oracle wording, mtgish acceptance and
abstentions. Bootstrap paired differences by family. Parser acceptance does
not establish semantic correctness. No automatic workshop promotion occurs.

## Running the workflow

The tools use a host CPU environment with `requirements.txt`, a Transformers
environment for tokenization/export, and a Hugging Face Hub environment for
Jobs/Buckets. Sol/Luna calls use the locally authenticated Codex CLI with cached
prefixes and batches of nine. Six workers review each expansion batch.
The GPU script's dependency lock is `src/rl_gpu.py.lock`.

Preparation and review entry points:

```bash
python src/rl_round_prepare.py --help
python src/rl_round_eval.py --help
python src/rl_teacher.py --help
python src/rl_audit_refine.py --help
python src/rl_round_expand.py --help
```

The run directory contains immutable pilot/evaluation manifests, the serving
system prompt, a job authorization receipt and a hash-bound `audit-quality.json`.
The latter names the reviewed directory and pins its review/preferences files.
After inspecting the audit, run:

```bash
HF_PYTHON=/path/to/hf-environment/bin/python
"$HF_PYTHON" src/rl_round_run.py \
  --root /path/to/round \
  --cpu-python /path/to/cpu-environment/bin/python \
  --export-python /path/to/transformers-environment/bin/python \
  --sft-adapter /path/to/completed-sft/adapter \
  --sft-record /path/to/completed-sft/train-job.json \
  --dataset /path/to/full-oracle-dataset \
  --old-pilot /path/to/previous-pilot-inputs \
  --local-binary /path/to/llama-server \
  --converter /path/to/convert_hf_to_gguf.py \
  --quantizer /path/to/llama-quantize
```

This round has an operator-selected $20 GPU bound, separate from CLI usage.
Each job reserves its full timeout cost, then releases unused reservation from
terminal allocation timestamps. The ledger is a conservative bound, not a
billing invoice. No paid retries occur automatically. Job receipts, status,
logs, raw predictions and usage records live in the run directory. Source code,
inputs, model and parser artifacts are hash-pinned; altered inputs require
inspection rather than silently continuing a different experiment.

The first sampling allocation ran out of GPU memory after 1,968 of 8,192
completions at batch size 48. Recovery retains those exact output rows and pins
their original package/model identity. New allocations start at batch size 24,
halve the batch on an allocation failure, release the failed call's tensors,
and record allocated/reserved/peak GPU memory. A single example that still
cannot fit fails explicitly. The allocator uses
[`expandable_segments`](https://docs.pytorch.org/docs/2.8/notes/cuda.html#optimizing-memory-usage-with-pytorch-cuda-alloc-conf)
to reduce fragmentation from changing allocation sizes.

A recovery package pins the original job and partial-output hash; unrelated
prompts/models and duplicate or unknown candidate slots are rejected. Malformed
model responses are preserved for fair scoring. New batches use deterministic
seeds derived from their candidate IDs. This is a documented continuation with
different batching/RNG, not a bit-identical replay of the interrupted job.
An inspected recovery receipt passed with `--recovery` identifies the replacement
job and code commit; failed receipts and previous code manifests are retained.

The pipeline prepares loss curves and paired validation reports, then publishes
curated datasets, complete LoRA checkpoints and evaluation diagnostics to Hugging
Face. Raw worker conversations, optimizer states, reference adapters and local
credentials are excluded. Bulk datasets stay out of GitHub. Results are pending;
the initial audit is not a claim of model improvement.
