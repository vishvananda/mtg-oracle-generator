# Oracle repair SFT pilot

**Training and paired Q4 evaluation are in progress. No model promotion.**

This pilot teaches the browser's existing card + requested change + parser
diagnostic flow. It continues the deployed DPO2 adapter for one pass with a fresh
optimizer and `1e-5` learning rate. The original pinned BF16 base is loaded in
NF4 for QLoRA training; comparisons use separately merged Q4_0 models.

The [recipe and commands](../../docs/oracle-repair-training.md),
[dataset manifest](dataset-manifest.json), and [review counts](corpus-review.json)
are reproducible records. Raw third-party source cards and prepared data remain
in private Hugging Face staging, outside Git.

## Corpus

There are 2,670 training examples: 1,335 repair/edit/control examples plus 1,335
normal-generation examples selected from the original training split. The
repair portion includes 283 planeswalker examples. Replay uses distinct card
families and reserves a planeswalker quota. All repair variants of a family
stay together; the frozen train/evaluation family and prompt overlap is zero.

We built 1,380 synthetic repairs, requested edits, and controls, and sampled 240
real parser rejections from earlier model outputs. Sol proposed repairs using
only the draft and diagnostic, then separate Sol calls reviewed every proposed
target against its original draft. Both use medium effort and cached immutable
instruction prefixes. Each reviewer batch contains an unchanged valid control
and a deliberately destructive quantity change. Teacher uncertainty excludes 42
real cases. Of 1,578 independently reviewed proposals, 1,529 pass the final gates.

An important limitation: only **six real training repairs** both preserved their
mechanics under review and became accepted by mtgish. Another **122 training
cases** are unchanged valid cards with unsupported mechanics. All **32 eligible
real evaluation cases** are also unsupported-valid controls; none is a successful
teacher repair. The experiment can measure synthetic repair learning and
resistance to destructive rewrites, but cannot establish broad accuracy on real
wording errors. Parser rejection alone is a poor source of repair labels.

## Frozen comparison

| Panel | Cases | Purpose |
| --- | ---: | --- |
| Synthetic wording errors | 61 | Punctuation, self-reference, keywords, loyalty formatting |
| Explicit edits | 24 | Power or starting loyalty changes |
| Accepted no-op controls | 12 | Avoid damaging already-valid cards |
| Real unsupported-valid controls | 32 | Preserve valid mechanics despite parser rejection |
| Normal generation | 64 | Detect regressions on the existing development panel |

Both arms use the same production prompt, Q4_0 quantization, greedy decoding,
seed, and output limit. Parser acceptance is reported separately from metadata
and semantic preservation. Unsupported-valid controls succeed only when the
card stays unchanged and passes the semantic review. Identical candidates share
a blind judgment. Paired intervals resample whole card families.

This is a development experiment. Repair families were excluded from this
continuation, but may have occurred in original SFT. Teacher and reviewer labels
are correlated model judgments, not human accuracy. The original locked 400-card
test is untouched. Local CPU inference is a quality check, not browser timing.

## Run

- [HF job](https://huggingface.co/jobs/vishvananda/6acb22e9fee2c900701920ce): A100, 167 planned updates.
- One paid attempt; 120-minute timeout; maximum compute charge $5.01 at the quoted rate.
- CPU preparation took 11.06 seconds; every completion loss mask was checked before GPU launch.
- Loss monitoring uses 64 training examples, explicitly labeled as a training diagnostic.
- Dataset SHA-256: `4ff9c382485725ab48d086e818608598369fdec13980eaf88a828ee9a147dc4f`.
- Frozen package SHA-256: `70042ebc7964d2dc613632b6c7b526926c882d57ae87a989fd1e4829aac9b415`.

The results and loss curves will be added after the completed adapter has passed
the paired comparison. CardForge continues serving DPO2 during the experiment.
