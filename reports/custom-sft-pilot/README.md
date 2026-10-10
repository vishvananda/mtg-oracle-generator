# Additional custom-card SFT pilot

[Corrected HF job](https://huggingface.co/jobs/vishvananda/6aca52ccfee2c90070188f39)
was submitted on 2026-10-10. **Results are pending; no quality improvement is claimed.**
The live CardForge model remains the round-two DPO quant.

The [first attempt](https://huggingface.co/jobs/vishvananda/6ac9f6cf095c5780893104f7)
completed both 320-request baseline generations, then stopped before training:
TRL 0.23 re-prepared the already-loaded QLoRA model and froze its adapter. Our
trainability assertion caught this. The corrected pipeline reactivates only
the original SFT adapter after trainer initialization, before creating the
optimizer. A regression test reproduces the real TRL freeze path on a tiny CPU
model, performs an update, and verifies that base weights stay frozen. This is
an API contract test, not a GPU or quality result.

The manually restarted job reuses all 640 baseline outputs after verifying their
hashes, complete case IDs, parent adapters, dataset, generation code, prompts,
and configuration. The dataset and learning recipe are unchanged. The initial
job used 11m 7s of allocation; its conservative cost bound is **$0.584** (elapsed
minutes rounded up plus two minutes, not an invoice). The recovery reserves
at most **$3.751** for 90 minutes on the same A100, sharing the original **$5.01**
experiment ceiling. There is no automatic paid retry.

The frozen dataset contains **4,076 training examples**: 2,038 custom-card
examples from 511 families and 2,038 original-training replay examples, including
192 planeswalker examples. One target and six broad descriptions did not pass
automatic review and were excluded. The new holdout contains 192 descriptions
from 64 families absent from the original SFT training. A separate 128-request
existing development panel checks regressions.

All 576 selected source targets were checked after normalization; their parsed
structures were preserved. Of the 575 retained targets, 64 had reminder text
removed, 506 had none, and five retained reminder syntax required by unchanged
mtgish. Accepted parser structure alone is not proof of Oracle correctness.

Sol at medium reasoning generated and independently reviewed three detail
levels in 97 batches. About **78.9% of reported batch input tokens were cached**.
The [preparation receipt](preparation.json) records usage without inventing a
subscription-dollar cost. Local preparation verified 4,140 completion masks
(4,076 train plus 64 training diagnostic copies) in **8.68 seconds**. The training
set contains 2,643,382 tokens; the longest complete sequence is 1,009 tokens.

The candidate starts from completed SFT, uses a new optimizer, one epoch at
2e-5 learning rate, and approximately 255 updates. The A100 workflow generates
the SFT, served DPO, and candidate NF4 comparisons with identical prompts and
decoding. Combined usage will be reported after completion. Loss curves,
blind intent labels, parser counts, and paired
family-level intervals are produced automatically. Any apparent improvement
still requires confirmation using the deployment Q4_0 quant before promotion.

See [the complete protocol and reproduction commands](../../docs/custom-card-training.md),
[dataset manifest](dataset-manifest.json), and
[training configuration](../../configs/custom-sft-pilot.json).
Prepared records and weights are not committed to Git. This pilot's third-party
custom-card records remain in private staging; there is no automatic publication
or deployment.
