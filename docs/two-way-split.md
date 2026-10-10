# Small, coverage-protected evaluation split

The v2 run uses **training plus one locked final evaluation panel**. There is no
held-out validation set, hyperparameter search, early stopping based on test
scores, or test-based checkpoint selection. The requested endpoint is one epoch.
The operational cost limit can stop it earlier; a partial run is checkpointed
and does not automatically open the final test.

The active experiment now has explicit authorization to finish this one epoch
even if its original time/budget allowance runs out. `finish_fixed_epoch.py`
replaces the initial watcher, leaves the running GPU job alone, and resumes only
verified full optimizer checkpoints after normal time-limit stops or HF timeouts.
Each continuation keeps the frozen dataset, trainer, model config and target step
19,774. The ledger records any necessary allowance increase under the pinned
authorization. Non-timeout failures stop for review; no additional epoch, RL run
or deployment is authorized by this mechanism. Final evaluation still waits for
completion of the epoch.

| Partition | Descriptions | Source cards | Role |
| --- | ---: | ---: | --- |
| Training | 316,372 | 35,744 | Gradient updates; 98.85% of available cards |
| Final test | 400 | 400 | One frozen request per held-out rules family |
| Held-out reserve | 3,285 | Within the 416 held-out source cards | Additional variants archived, excluded from training and headline scoring |

The 400 held-out families contain 416 related source cards. All their detail
levels, historical/current wording, name/rarity variants and related normalized
rules families remain outside training. Exact normalized descriptions do not
cross the boundary. The split changes no descriptions or targets.

Holding out only alternate descriptions of training cards would measure
paraphrase generalization and allow memorization of their answers. This panel
instead tests source families absent from **this fine-tuning corpus**. We cannot
claim that the pretrained base model has never encountered these cards.

## Coverage and selection

Selection uses source metadata, never generated outputs or model scores. Every
recognized card type and every color must appear in the actual test cases.
There are representatives of all **32 exact color combinations**, including
colorless, and **25 planeswalkers**. Cards with multiple types count in more
than one row below.

| Type | Test cases | Type | Test cases |
| --- | ---: | --- | ---: |
| Creature | 207 | Planeswalker | 25 |
| Artifact | 48 | Enchantment | 50 |
| Instant | 36 | Sorcery | 42 |
| Land | 12 | Battle | 1 |
| Kindred | 2 | Conspiracy | 1 |
| Dungeon | 1 | Plane | 1 |
| Phenomenon | 1 | Scheme | 1 |
| Vanguard | 1 | | |

| Color bucket | Cases |
| --- | ---: |
| White only | 66 |
| Blue only | 53 |
| Black only | 61 |
| Red only | 52 |
| Green only | 35 |
| Colorless | 60 |
| Multicolor | 73 |

The nine description styles have 44–45 cases each. Type/color requirements and
the planeswalker quota are filled first; remaining families use a fixed SHA-256
ordering. Families larger than four source cards stay in training, avoiding
removal of large generic families such as vanilla creatures.

Scryfall keywords and recognized ability words, plus documented text patterns
(loyalty costs, looting/rummaging, opponent-library access, replacement effects,
etc.), protect training coverage. An ability feature found in at most ten rules
families stays entirely in training; more common features retain at least 90%
and at least ten families. Type/layout/color strata retain at least one family
in training. Scryfall's keyword field also includes named flavor abilities;
these counts are not a taxonomy of distinct game mechanics. Unnamed semantic
abilities are not exhaustively recognized by this audit.

This is a mixed challenge panel with deliberate coverage, **not** a
prevalence-weighted estimate over every card. Report style/type results alongside
the aggregate; 400 cases cannot give precise per-mechanic estimates. Parser
acceptance is not game legality or intent fidelity, and sparse descriptions can
legitimately yield empty rules text. Separate nonempty-rules results from the
headline parser rate when interpreting performance.

## Reproduce

The prepared dataset is public on
[Hugging Face](https://huggingface.co/datasets/vishvananda/mtg-oracle-design-descriptions-v2).
Pin revision `7c1bbca04b4259c15c397c5c6a00af5e143ef21e`. The original v1 release
remains unchanged and independently reproducible.

```bash
python src/hub_dataset.py download \
  --repo-id vishvananda/mtg-oracle-design-descriptions-v2 \
  --revision 7c1bbca04b4259c15c397c5c6a00af5e143ef21e \
  --directory data/oracle-v2

python src/prepared_training.py --dataset data/oracle-v2 \
  --config configs/qwen3-4b-two-way.json --output data/prepared-v2 --workers 8
python src/run_package.py --dataset data/oracle-v2 --prepared data/prepared-v2 \
  --recipe configs/two-way-run.json --output runs/package-v2
```

Rebuild assignments from the verified v1 dataset and the pinned Scryfall bulk
snapshot with `src/coverage_split.py --source data/oracle-v1 --oracle
oracle-cards-20261006090155.jsonl.gz --output data/oracle-v2`. The output is
immutable. `split-policy.json` contains all feature counts, panel strata and
selection rules; `split-map.json` records whole-card assignment.

`configs/qwen3-4b-two-way.json` selects 256 **training** examples for diagnostic
loss/token-accuracy observations. Those observations are labeled training
diagnostics in the graphs, never validation accuracy. The final-test records and
reserve are absent from the GPU training package, including its token cache.

For an authorized HF run, `src/run_two_way.py` orchestrates a 20-step smoke run,
fresh training from the pinned base, and one paired base/adapter evaluation on
all 400 final-test cases. It requires a shared ledger, verifies a completed
smoke before training, saves/publishes checkpoints every 200 updates, refuses
automatic paid retries, and produces the loss/mtgish report locally after test
generation. It never promotes a serving model automatically. The checked-in
recipe's limits are for this experiment; review total cost and available credit
before submitting your own paid jobs.

Do **not** resume a v1 adapter on this split: it may have already trained on new
test cards. If final-test results guide later recipe or checkpoint choices,
relabel this panel as development data and reserve a fresh final holdout for
those subsequent experiments.
