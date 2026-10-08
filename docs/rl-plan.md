# Oracle intent post-training plan

Start from the completed **one-epoch v2 SFT checkpoint**, then test whether a reward
can choose the better of several generated cards. Do not schedule more SFT epochs
as a prerequisite for RL. Extra SFT is useful if the model still cannot produce a
faithful candidate; preference training is useful when it can, but chooses poorly.
No RL GPU job is authorized, scheduled, or automatically chained to the SFT run.

The near-term recommendation is **Luna intent judgments + deterministic checks +
a small, coverage-aware parser signal**. Use Oracle embeddings to retrieve wording
examples, not as a direct reward for looking like existing cards. The executable
prototype is [rl_rewards.py](../src/rl_rewards.py), with a
[draft pilot recipe](../configs/rl-pilot.json). These are preparation tools, not a
finished DPO/GRPO trainer.

## Which signals help?

| Signal | What it establishes | Failure mode | Proposed use |
| --- | --- | --- | --- |
| JSON schema, required characteristics, explicitly specified stats/cost/colors | Output is editable and respects those exact fields | Does not understand abilities | Deterministic gate |
| mtgish whole-card parse | Supported Oracle syntax | Wrong player, missing cost, or deleted ability can still parse; real cards can be unsupported | At most 10% of positive reward, after intent checks |
| Luna as judge | Whether the described behavior survives translation | False rejections, missed clauses, prompt injection, order and style biases | Main semantic signal, calibrated against independent labels |
| Exact match to a reference / text overlap | Agreement with one known implementation | Penalizes legitimate defaults and alternative designs; copying can score well | Diagnostic for fully specified requests only |
| Card embedding similarity | Related topic and wording | Opposite effects can be close; rewards copying familiar cards | Retrieval and diagnostics; zero direct reward weight |
| Engine simulation / semantic IR | Specific behavior in supported states | Limited coverage; handpicked states miss interactions | Later, high-trust checks on supported subsets |
| Balance, rarity, color-pie judgments | Design plausibility | Subjective; can override the user's request | Separate editorial feedback, not the core objective |

For example, “Draw a card, then discard a card” parses but is wrong for a request
to rummage. Dropping a difficult ultimate can improve parser acceptance. A vague
“midsize red Giant” can legitimately get either 3/3 or 4/4 stats and a proposed
cost, name and rarity. Rewards must distinguish these cases.

## Reward prototype

The versioned rubric checks actors, targets, quantities, costs versus effects,
zones, optionality, timing, duration, restrictions, and relationships between
clauses and faces. Explicit metadata comes only from supplied constraints, never
from all fields of a source card. Type lines and Oracle text are judged
semantically, rather than compared as literal strings.

| Outcome | Prototype reward |
| --- | ---: |
| Invalid JSON/schema or truncated generation | −1.0 |
| Changed explicit metadata or missing required characteristics | −0.8 |
| High-confidence intent failure | −0.6 |
| Unusable rules despite an otherwise positive judgment | −0.5 |
| Faithful candidate | 0.75 + up to 0.15 for Oracle style + up to 0.10 for parsing |
| Uncertain judge or parser infrastructure failure | Exclude, never turn into a positive score |

The parser term is informative only when the reference itself parses. Otherwise
it is a constant 0.05 for that prompt, including prompts without a reference.
Thus a missing grammar feature cannot reward deleting that feature. An empty
Oracle field is valid for a requested vanilla creature, but cannot erase a
requested ability. Parser timeouts are infrastructure errors, not grammar failures.

All candidates for a prompt must have usable assessments. Exclude an uncertain
group rather than silently optimizing its parser score. All-bad and tied groups
produce no preference update; report their frequency and route all-bad examples
to repair/SFT work. A higher average reward by itself is not evidence of improvement.
These weights are starting choices, not optimized or validated coefficients.

Three extensions are worth comparing after this baseline. **Atomic requirement
checks** can score each requested clause, which gives better feedback than a
single pass/fail. Extract the checklist from the request before seeing candidates;
otherwise the candidate can influence what the judge decides was required.
Treat actors, zones and omitted abilities as critical failures even if most other
clauses match. Partial scores may rank repairs but must not outweigh a critical
failure. **Metamorphic checks** compare controlled request changes with output
changes: changing “opponent” to “you” should change the affected player, while a
paraphrase should preserve behavior. **A learned pairwise reward model** can later
distill reviewed Luna decisions, including close semantic negatives, into a cheap
local request/card scorer. It must be calibrated on fresh errors after each policy
change; a generic card embedding is not a substitute for that training.

Keep a small supervised replay set or an explicit SFT reference penalty as an
ablation against forgetting rare mechanics. Do not optimize a single aggregate
score without checking those mechanics separately. Execution-based checks should
include counterexample game states, not only a happy-path ability resolution.

## Luna judge and calibration

Judge the actual request and candidate, with generator identity, expected labels,
and parser verdicts hidden. Allow abstention. Treat text and notes as untrusted
data. Do not request hidden reasoning; save short, actionable violation labels.
Pin the rubric, model name, requested thinking effort, transport, parser revision,
input/output hashes and actual usage. A model alias is not an immutable model
revision, so preserve outputs for replay.

Before training, expand the small [reward diagnostic](../reports/rl-preparation/README.md)
to at least 200 independently reviewed cases. Include correct alternatives to
vague requests, multi-face cards, planeswalkers, older wording, parser gaps, and
controlled changes to actor, zone, quantifier, cost, duration, order or one omitted
clause. Add natural errors sampled from the final SFT model; manufactured errors
alone are too easy. Keep reward-development examples separate from the final test.

Repeat a blinded subset in reversed order and with different batches. Compare
Luna to a stronger reviewer on disagreements, and have a person check a random
sample as well as every critical false acceptance. Suggested release gate:
no known critical mutation accepted in the reviewed canaries, at least 95%
agreement on the independently labeled development sample, and explicit reporting
of abstentions and false rejections. These thresholds are proposed gates, not
current evidence of general judge accuracy. LLM judges have documented position,
verbosity and self-preference biases; repeated self-judgment is not independent
verification. [Judge evaluation study](https://arxiv.org/abs/2306.05685).

Use cached instruction-only prefixes and batches of nine, as in the corpus
pipeline. Memoize exact assessments with a key containing request, raw candidate,
rubric, model, parser and retrieved-reference hashes. Never reuse a judgment after
changing its inputs. The current probe records prefix-cache usage; a persistent
assessment cache and retry budget remain trainer-integration work.

## Oracle embeddings

The prototype indexes the **35,744 training source cards**, one vector per card,
using the small CPU-friendly BGE model pinned in the pilot config. Type, colors,
cost, stats and rules are included; names are normalized to CARDNAME and artwork
and flavor are omitted. Long cards are chunked rather than silently truncated.
BGE uses normalized CLS vectors; the small model has 384 dimensions.
[Model card](https://huggingface.co/BAAI/bge-small-en-v1.5).

The index could include the entire source corpus for a reference-only application.
During reward construction it must exclude the **416 held-out source cards**, all
their variants and their rules families. `train-reward` is the default scope;
`all-reference` is explicitly separate and cannot be supplied to judge-context
generation. The split-map and index files are hash checked.

For judge evidence, retrieve examples from both the request and candidate, with
the request taking priority. Test with and without retrieval on identical cases.
Do not let the closest card become the desired answer: a new combination may be
correct even if no close card exists. Source examples are wording evidence, not
binding stats or extra abilities. A later improvement is clause-level hybrid
retrieval (lexical terms plus embeddings) and a reranker trained on mechanical
differences. That is more promising than maximizing whole-card cosine similarity.

## Sequence and compute

1. **Finish SFT and freeze the checkpoint.** Record its hash, tokenizer, prompt,
   inference precision and baseline. Start a separate post-training adapter so the
   SFT result remains reproducible.
2. **Sample and rerank before training.** The prepared pilot has 512 distinct
   training families and four candidates per request: 2,048 planned completions.
   Cover detail styles, card types, colors and planeswalkers. Compare the first
   sampled candidate with the reward-selected candidate; report greedy and
   best-of-four costs separately. Have an independent reviewer verify the gain.
3. **Try offline preferences first.** Export high-confidence faithful/wrong pairs
   in standard TRL `prompt/chosen/rejected` format, at most one pair per family.
   Do not create preferences solely from parser acceptance. A short DPO pilot can
   learn these preferences without calling Luna inside the GPU training loop.
   If too few prompts have a good candidate, collect reviewed repairs and do a
   small additional SFT pass instead. [DPO paper](https://arxiv.org/abs/2305.18290).
4. **Move to online GRPO only after reward selection works.** Sample four fresh
   completions per prompt, score them, and update against group-relative rewards.
   The draft uses a small nonzero KL penalty against the frozen SFT policy,
   `dr_grpo` loss and no per-group standard-deviation scaling. Track all-tied
   groups, reward components, KL, entropy, response length and truncation.
   TRL supports custom rewards and these controls; its generation path must be
   benchmarked separately. [Pinned TRL documentation](https://huggingface.co/docs/trl/v0.23.0/en/grpo_trainer).

For QLoRA, explicitly keep the **SFT policy** as the reference. Disabling every
adapter would compare against the original base model instead. Use a frozen SFT
adapter/reference copy or a carefully verified merged SFT checkpoint; verify the
initial train/reference outputs agree before updating. Keep deployment
quantization out of the first training comparison, then evaluate the serving
quant separately.

Keep Codex/Luna calls on the host. Generate candidates on GPU, stop that job,
judge locally, then launch preference training. Do not place personal Codex
credentials in an HF job or rent a GPU while waiting for external judgments.
Online GRPO would need a measured low-latency judge bridge or a smaller reward
model distilled from reviewed labels; neither is implemented here. Stale offline
samples belong in DPO, not an on-policy GRPO update.

At the current run's observed A100 rate of about **$2.50/hour**, a 30-minute compute
window is about **$1.25**. This is a proposed bounded benchmark, not an estimate of
the whole RL phase. Measure generation tokens/second, judge latency/usage and
update throughput first; multiple samples and external rewards can make RL much
slower than SFT. Re-query HF pricing before launch. Codex token usage is recorded
separately; subscription usage is not an API dollar invoice. Existing permission
to finish the SFT epoch does not authorize new RL spending.

## Evaluation boundary

The current v2 run has one locked 400-case final panel and no separate validation
split. Its scheduled SFT evaluation stays unchanged. Use independent synthetic
and independently reviewed development requests for RL decisions; do not tune
against that final panel or retrieve its reserve examples. Select the RL recipe
and stopping rule on development evidence before running the final comparison.
If the SFT final results influence reward design or selection, disclose that reuse
and create a fresh independent final benchmark; do not call the reused panel an
untouched RL test.

Report schema success, whole-card mtgish acceptance, intent fidelity, and their
intersection separately, always including missing/truncated outputs in the main
denominator. Also report parser-supported-reference subsets, judge abstentions,
explicit field failures, ability omissions, planeswalkers, color/type/style
slices, and a reviewed error sample. Neither parser acceptance nor Luna's own
reward score is “legal-card accuracy.” Compare SFT, reranked SFT, and the
post-trained model with the same generation budget and inference precision.

## Reproduce the preparation

Use the ordinary CPU requirements for rewards and pilot preparation. The optional
embedding tool additionally uses PyTorch 2.8.0 (CPU), Transformers 4.56.2 and NumPy
2.2.6, as recorded in its index manifest. `reward_probe.py --judge-model` requires
an authenticated local Codex CLI; omit that flag for a parser-only diagnostic.

```bash
python src/setup_mtgish.py
python src/reward_probe.py --output artifacts/reward-probe \
  --judge-model gpt-6-luna --effort medium
python src/rl_data.py prepare --dataset data/oracle \
  --output artifacts/rl-pilot --count 512 --candidates 4
python src/oracle_retrieval.py build --dataset data/oracle \
  --oracle data/oracle-cards.jsonl.gz --scope train-reward \
  --revision 5c38ec7c405ec4b44b94cc5a9bb96e735b38267a \
  --output artifacts/oracle-index
python src/oracle_retrieval.py probe --index artifacts/oracle-index \
  --cases artifacts/reward-probe/cases.jsonl --output artifacts/embedding-probe
```

Save generated candidates as JSONL with `case_id` (from `prompts.jsonl`), unique
`candidate_id`, `raw` (the exact generated string), and `hit_token_limit` (boolean).
Do not put source targets in the generation prompt. The host-side scorer runs the
unchanged mtgish flow, then calls Luna only for candidates needing semantic review:

```bash
python src/rl_score.py --pilot artifacts/rl-pilot \
  --candidates runs/candidates.jsonl --output artifacts/scored \
  --judge-model gpt-6-luna --effort medium
python src/rl_data.py export-pairs --pilot artifacts/rl-pilot \
  --scored artifacts/scored/scored.jsonl --output artifacts/preferences
```

After generating and scoring candidates, `rl_data.py export-pairs --pilot ...
--scored ... --output ...` verifies candidate/assessment hashes and writes the
preference file plus an audit sidecar. Its input contract is documented in that
module. Export does not submit training or declare the pairs independently
reviewed. Full datasets, embeddings and eventual weights belong on HF; GitHub
contains code, the rubric, small hand-authored diagnostics and aggregate reports.
