# Second SFT epoch: first quarter

**Running; no new accuracy result or model promotion yet.**

The [HF job](https://huggingface.co/jobs/vishvananda/6acb888cfee2c90070195ff6)
started on 2026-10-11. It continues the original completed SFT checkpoint for
4,944 additional optimizer updates with a new `3e-5` schedule, while preserving
the original full corpus, output serialization and family split. DPO and the
small repair/custom continuations are not starting weights for this run.

The full second-epoch schedule remains 19,774 updates; this job stops at the
quarter mark for review and saves complete optimizer state. The experiment has
a $20 HF budget, with a $16.25013 maximum reserved for its one training job.
Expected training is roughly five hours based on the previous full epoch.

Post-training export and the local three-model generation comparison are
prepared. Loss diagnostics use training examples. The original 400-card final
test stays out of checkpoint selection. See the
[recipe, continuation policy and serving investigation](../../docs/second-sft-epoch.md).

The independent serving experiment checks Q4_0, Q4_K_M, Q5_K_M and Q8_0 exports
of both DPO2 and custom SFT on 64 frozen development cases. Separate F16 LoRA
loading is also under investigation. Results are pending; browser runtime source
support is not evidence of speed or quality on a user's GPU.
