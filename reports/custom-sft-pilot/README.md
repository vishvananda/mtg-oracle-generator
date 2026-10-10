# Additional custom-card SFT pilot

[HF job](https://huggingface.co/jobs/vishvananda/6ac9f6cf095c5780893104f7)
started on 2026-10-10. **Results are pending; no quality improvement is claimed.**
The live CardForge model remains the round-two DPO quant.

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
2e-5 learning rate, and approximately 255 updates. One A100 allocation generates
the SFT, served DPO, and candidate NF4 comparisons with identical prompts and
decoding. Its maximum timeout cost is about $5.00; actual usage will be reported
after completion. Loss curves, blind intent labels, parser counts, and paired
family-level intervals are produced automatically. Any apparent improvement
still requires confirmation using the deployment Q4_0 quant before promotion.

See [the complete protocol and reproduction commands](../../docs/custom-card-training.md),
[dataset manifest](dataset-manifest.json), and
[training configuration](../../configs/custom-sft-pilot.json).
Prepared records and weights are not committed to Git. This pilot's third-party
custom-card records remain in private staging; there is no automatic publication
or deployment.
