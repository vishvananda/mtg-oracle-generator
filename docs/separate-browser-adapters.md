# Separate browser adapters

The next serving release can reuse an immutable Q4_K_M original base and ship
only the cumulative F16 LoRA when training changes. The measured base is
2,497,278,848 bytes; the current rank-16 adapter is 66,095,328 bytes. Moving from
the existing merged Q4_0 requires one new base download. Later releases reuse
that base; this is a complete replacement adapter, not a delta or a stack of
SFT/DPO adapters.

The [64-case serving comparison](../reports/serving-quantization/README.md)
supports investigating this format for smaller updates, not a claim of improved
quality. Keep evaluating the exact base-plus-adapter representation and browser
runtime before promotion. A different quantized base changes inference even
when the adapter is unchanged.

## Export a checkpoint

First obtain the original base and its verified export receipt using
[`serving_adapter_export.py`](../src/serving_adapter_export.py). Keep that base
and receipt immutable. The receipt pins every original base file, the converted
Q4_K_M file, and the adapter converter. Do not rebuild the base per update.

For each new **cumulative** PEFT checkpoint:

```sh
python src/export_lora_gguf.py \
  --adapter "$CHECKPOINT/adapter" \
  --base "$ORIGINAL_BASE_SNAPSHOT" \
  --base-gguf "$PINNED_EXPORT/models/base-Q4_K_M.gguf" \
  --base-receipt "$PINNED_EXPORT/exports.json" \
  --converter "$LLAMA_SOURCE/convert_lora_to_gguf.py" \
  --output "$RELEASE/separate"
```

Use the same CPU environment as the existing GGUF export, including PyTorch and
the pinned llama.cpp converter dependencies. No GPU job is needed. The prompt
defaults to `adapter/system-prompt.txt`; older checkpoints can supply
`--system-prompt`. The exporter verifies the source files and converter, converts
only the adapter to F16, and writes `adapter-F16.gguf`, `system-prompt.txt`, and
`exports.json`. Reusing an output directory verifies the completed artifact;
changed inputs require a new output directory. Conversion failure produces no
completion receipt. It does not upload or deploy anything.

The bounded SFT follow-up supports a `separate_adapter` object in its review
configuration with `base`, `base_gguf`, `base_receipt`, and `converter` paths.
It writes the extra artifacts to `serving/separate` after verifying the training
receipt. The frozen merged-Q4_0 evaluation remains unchanged; this extra export
does not automatically become a serving candidate or start another GPU job.

## Browser release contract

Publish base shards, adapter, and prompt at content-addressed URLs. Bind the
adapter to the exact base GGUF SHA-256 and the prompt to its SHA-256. Each base
shard must stay below 512,000,000 bytes. Upload and check immutable files before
publishing the release manifest; retain old manifests for rollback.

The browser pins one release per session, caches base and adapter independently,
and verifies downloaded hashes before inference. An adapter is a separately
mounted native LoRA file at scale 1, never another model shard. Missing, corrupt
or mismatched adapters must fail rather than serve the original base silently.

Pinned wllama 3.8.1 already accepts native `lora_paths`; its JavaScript file
mounting needs an extension for separate adapter Blobs. That extension belongs
to the browser application and is guarded by the upstream loader hash. GPU
offload checks still apply. The separate WebLLM float32 compatibility export is
not interchangeable with a GGUF LoRA: prepare and evaluate a matching export or
leave AI mode unavailable on that path. Manual editing can remain available.

Browser WASM integration and cache checks are distinct from device validation.
Measure WebGPU latency, memory use, full offload, and representative card quality
on actual supported devices before selecting this release format for users.
