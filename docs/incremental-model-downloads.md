# Smaller browser model updates

Current CardForge releases merge the cumulative adapter into the pinned BF16
base and quantize that merged model. The Q4_0 payload is **2,369,545,056 bytes**.
Website-only releases reuse the existing model URLs and browser caches. A new
merged model has new weight hashes/URLs and normally requires a full download;
splitting it into sub-512 MB chunks does not turn it into an adapter update.

For the custom-SFT pilot, the saved PEFT adapter is **132,187,888 bytes**. A local
llama.cpp F16 LoRA conversion produced **66,095,328 bytes** (504 tensors), about
**97.2% smaller** than the merged Q4_0 model. This measures a real converted file,
not successful browser inference with that file. The base download is additional
on first use. GPU memory and inference time also need measurement.

## Proposed packaging

Cache an immutable, pinned **original base** Q4 model separately from a cumulative
adapter. A small release manifest would bind base revision/hash, tokenizer and
prompt versions, adapter hash, format and required runtime. The next compatible
release would fetch only a new adapter and small metadata. Preserve the prior
manifest/adapter for rollback and change the active release only after the new
adapter is downloaded, checked and GPU loading succeeds. Existing merged-model
users need a one-time base download to migrate.

Do not attach the cumulative custom adapter to an already SFT/DPO-merged model:
that would apply the parent changes twice. The adapter belongs to the original
Qwen base. The same rule applies to cumulative DPO adapters.

## Runtime work required

The installed Wllama **3.8.1** exposes `lora_adapters` paths/scales and forwards
them to llama.cpp. Its public model-blob preparation currently distinguishes
only model shards and multimodal projectors; other blobs become model shards.
There is no verified public route here to stage a separate LoRA file in the
worker filesystem. Add/test an explicit adapter-file input upstream or in a
small pinned runtime patch; do not pass the adapter as another model shard or
depend on private proxy internals. Relevant installed source: `src/wllama.ts`,
`src/utils.ts` (`prepareBlobs`) and `src/types/types.ts`.

The float32 GPU path uses **WebLLM 0.2.85** with different packed weights and a
compiled model library. Its installed public configuration exposes no equivalent
adapter input. It needs a separately validated adapter-capable library/runtime,
or must keep its merged releases initially. The F16 GGUF adapter cannot simply
be supplied to that path.

Finally, `Q4(base) + LoRA` is numerically different from `Q4(base + LoRA)`. Repeat
the held-out quantized comparison, loading/peak-memory checks, and warm-cache
network audit before migration. Confirm real GPU offload; CPU fallback is not
acceptable in the product. No adapter-only packaging has been deployed yet.

## Reproduce the size measurement

Use the completed pilot adapter, the pinned Qwen base configuration and the
llama.cpp b11443 converter, with the CPU export Python environment:

```bash
python /tools/llama.cpp/convert_lora_to_gguf.py /data/custom-sft/gpu/adapter \
  --base /cache/Qwen3-4B-Instruct-2507/cdbee75f17c01a7cc42f958dc650907174af0554 \
  --outtype f16 --outfile /data/custom-sft/review/adapter-f16.gguf
```

Weights and generated datasets stay outside the source repository.
