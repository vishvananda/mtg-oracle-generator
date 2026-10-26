# Historical experiment archive — 2026-10-26

The inactive Oracle-generation experiments are archived for inspection and inference. Five previously unpublished adapters are on [Hugging Face](https://huggingface.co/vishvananda/mtg-oracle-qwen3-4b-research-adapters); the second-epoch quarter adapter remains in its existing checkpoint repository. `archive-manifest.json` pins the exact commits and file hashes. These experiments were not promoted over DPO2.

| Experiment | Durable model | Evidence and limitations |
| --- | --- | --- |
| Custom-card SFT | HF subfolder `custom-sft` | [Q4 and NF4 comparisons](../../reports/custom-sft-pilot/README.md); small serving holdout gain and development regression |
| Repair SFT | HF subfolder `repair-sft` | [Repair report](../../reports/repair-sft-pilot/README.md); no controlled-repair gain, normal-generation regression |
| Second SFT epoch, first quarter | [Existing pinned release](https://huggingface.co/vishvananda/mtg-oracle-qwen3-4b-checkpoints-20261007/tree/31f85db13719800d05035be8b1eb20333ec709c1) | [160-request comparison](../../reports/sft-second-epoch/README.md); no measured improvement |
| Historical partial-completion pilot | HF subfolder `partial-pilot` | 6,500 training examples, 407 updates; small diagnostics do not establish intent accuracy |
| Early smoke and tiny pilot | HF subfolders `smoke-20-step`, `tiny-pilot-153-step` | Infrastructure/historical evidence; not quality benchmarks |
| Serving precision comparisons | Rebuild from the archived custom adapter, published DPO2 adapter, and pinned Qwen base | [Quantization report](../../reports/serving-quantization/README.md); keep measured file hashes and export recipes |
| Browser benchmark and separate-adapter preparation | Rebuild shards from pinned DPO2/base/adapter inputs | Browser-specific model manifests and diagnostic source are in `source-snapshots/` |

## Restore a saved adapter

Install `huggingface_hub` and use a fresh destination:

```sh
python experiments/archive-20261026/restore_adapter.py \
  --experiment custom-sft --output /path/to/restored-custom-run
```

The utility downloads from the immutable commit in `archive-manifest.json`, verifies every payload's size and SHA-256, and creates `adapter/` plus the original inference-export receipts. A failed verification does not create a completed output directory. It rejects existing destinations and unsafe paths. The preserved cumulative adapters load once over the original base; do not apply the training-parent adapter again.

The pinned base is `Qwen/Qwen3-4B-Instruct-2507` at `cdbee75f17c01a7cc42f958dc650907174af0554`. Tokenizer files and each experiment's training config are included with the adapter. Training and conversion source use the versions in the original run's lockfile and `source-snapshots/`; the main code snapshot is pinned in the manifest. Conversion used llama.cpp `b11443`; receipts record converter and output hashes.

For the published quarter-epoch adapter, download `vishvananda/mtg-oracle-qwen3-4b-checkpoints-20261007` at the immutable commit above. Original run receipts are under `records/sft-epoch2-quarter/receipts/`.

## Rebuild derived model files

Merged safetensors, F16/Q4/Q5/Q8 GGUFs, separate LoRA GGUFs, and browser shards are derived artifacts. They can be rebuilt from the saved cumulative PEFT adapter and original base. See [`export_gguf.py`](../../src/export_gguf.py), [separate browser adapters](../../docs/separate-browser-adapters.md), and the archived per-experiment conversion receipts. The custom/repair restoration layout supports the existing exporters' `--custom-run` receipt checks. Rebuilding may require the locked conversion environment and substantial RAM; no new GPU training is necessary.

Generated files are not necessarily byte-identical across changed conversion libraries or settings. Old output hashes and original source snapshots are preserved for comparison; a newly rebuilt export must be evaluated before serving it. This archive preserves inference models, not optimizer continuation state.

## What remains private/local

Frozen datasets, raw third-party card records, model-review transcripts, evaluation inputs, screenshots, and other uncurated run files remain in their original private/local staging. Public GitHub receives code, configs, receipts and the already-curated reports. Hugging Face receives inference adapters/tokenizers and explicit provenance. Neither receives credentials, environment logs, card artwork, full raw corpora or application source.

The local cleanup removes only archived inference weights and reproducible model exports from experiments that have no live process references. It retains the experiment directories, datasets, reports and local receipts, and records each removed file in a separate cleanup manifest. Active model servers, shared base models, the current application deployment and rollback remain in place.

Tests: `python experiments/archive-20261026/test_restore_adapter.py`. The publication receipt records a complete fresh download and SHA-256 verification of every HF file. Cleanup receipts and the final recovered-space figure are recorded alongside the archive after verification.
