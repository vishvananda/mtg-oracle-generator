# Browser inference benchmark

Live at **https://tetrarchs.com/model-bench/**. The static page runs the merged
Qwen3 4B SFT + round-two DPO Q4_0 model in the browser, using pinned
`@wllama/wllama` and compatibility assets at version 3.8.1. It never calls the
workshop generation API. There is no server inference fallback.

The source GGUF is 2,369,545,056 bytes (2.37 GB / 2.21 GiB), SHA-256
`af725e3116b06145d96c23d1626dad6f512b52021491722cdb6883e7d6b79c09`.
Five shards preserve the existing quantized tensors; there is no further
quantization. The model and all runtime assets are served from the same origin.
The published workshop system prompt and nonthinking chat template are retained.

Choose Auto to prefer WebGPU, or CPU to force WebAssembly inference. GPU use
is reported from the runtime's layer-offload log rather than inferred solely
from browser API availability. The runtime may select its compatibility build
on older browsers. Compatibility and GPU performance require testing on the
actual device; the deployment host's browser test exercises CPU mode.

Click **Load model**, then **Generate card**. The model cache avoids repeat
weight downloads. The default is one generation, greedy decoding, seed 17,
4,096 context tokens, and 512 output tokens. Prompt caching is off by default;
enable it explicitly for warm-prompt experiments. Changing backend, threads,
or context requires unloading the model first. Output limits remain editable.

Measurements distinguish:

- Model load: download/cache lookup plus runtime initialization, with both
  components retained in exported results.
- First token: Generate click to the first visible text fragment.
- Total generation: prompt processing plus decoding and stream completion;
  model loading is excluded.
- Decode tokens/second: the runtime's reported decoding rate and token count,
  not the number of JavaScript callbacks or a text-length estimate.

Output is streamed as plain text. A completed JSON parse checks syntax only;
this page does not run mtgish or create images. Cancellation is supported and
marked separately from completed or output-limited runs. The last 20 results,
including descriptions, outputs, settings, browser information and timing,
are saved only in that browser's local storage. **Download results** exports
them for manual comparison. No telemetry endpoint is configured.

## Build and serve

Download the exact merged GGUF and `system-prompt.txt` using the
[serving guide](../../docs/serve-preference-model.md). Install a Node version
supported by the pinned packages and obtain `llama-gguf-split` from llama.cpp
release `b11443` (the release used for this export).

```bash
cd web/client-benchmark
npm ci --ignore-scripts
npm run build
python3 prepare-model.py \
  --gguf /path/to/model-Q4_0.gguf \
  --system-prompt /path/to/system-prompt.txt \
  --splitter /path/to/llama-gguf-split
```

Serve `dist/` over HTTPS, including binary `.wasm` files as `application/wasm`.
These response headers enable CPU multithreading:

```text
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
Cross-Origin-Resource-Policy: same-origin
```

The model files need correct Content-Length/ETag responses and byte-range
support. Keep model URLs versioned so a future model cannot reuse stale cache
entries. Caddy's static file server supplies these capabilities. Apply isolation
headers only to the benchmark route; existing editor routes keep their policies.

The deployed layout is `/srv/mtg-oracle-client/current` pointing to an immutable
release directory, with shared model files at `models/af725e31/`.
Dependencies and app source are in Git; generated assets, browser profiles,
model files, and local test outputs are ignored.

## Browser smoke check

This runs the real full model in Chromium CPU mode, observes streamed text,
checks complete JSON and timing fields, then cancels a second request. It also
checks that generation makes no network POST or workshop inference request.
It requires several GB of free storage and memory.

```bash
CHROME_BIN=/path/to/chrome \
BENCH_ARTIFACTS=/path/to/browser-smoke \
BENCH_URL=https://tetrarchs.com/model-bench/ \
node smoke.mjs
```

The persisted Chrome profile reuses model files on subsequent smoke runs.
That cache changes load time, not the meaning of the generation timer.

## Deployment validation (2026-10-09)

The actual full model completed the prompt “A 3/3 red Giant creature that costs
{3}{R}, with no abilities.” using the unchanged 777-token workshop prompt.
Chromium 152, CPU mode, 8 threads, 2,048-token context, and cold prompt cache:

| Measurement | Result |
| --- | ---: |
| Cached model load | 14.28 seconds |
| First visible text | 441.96 seconds |
| Total generation | 475.08 seconds |
| Runtime decode rate | 1.27 tokens/second |
| Output tokens | 45 |

It returned complete JSON matching the specified cost, color, type and stats.
This is one functional CPU test on a shared host, not a quality evaluation or
a prediction of WebGPU speed. WebGPU still needs testing on users' devices.
Most elapsed time was prompt processing (440.35 seconds).

A separate short-prompt diagnostic on the same model verified visible streaming,
runtime token counts, cached loading, prompt-cache reuse, and cancellation after
the first text appeared. It was kept separate from the full-card timing above.
Both checks made only static GET requests, with no server inference requests or
browser errors. HTTPS isolation headers, model byte ranges, the workshop route,
and a narrow mobile layout were also checked.

The initial smoke script incorrectly assumed streamed results always include
`usage`; this runtime reports counts in `timings.predicted_n`. The completed
generation and its measurements were preserved, and the assertion now accepts
either field. See [validation.json](validation.json) for the recorded results.

Runtime reference: [wllama documentation](https://github.com/ngxson/wllama).
