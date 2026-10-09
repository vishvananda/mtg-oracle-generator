# Browser inference benchmark

Live at **https://tetrarchs.com/model-bench/**. The static page runs the merged
Qwen3 4B SFT + round-two DPO Q4_0 model in the browser, using pinned
`@wllama/wllama` and compatibility assets at version 3.8.1. It never calls the
workshop generation API. There is no server inference fallback.

The source GGUF is 2,369,545,056 bytes (2.37 GB / 2.21 GiB), SHA-256
`af725e3116b06145d96c23d1626dad6f512b52021491722cdb6883e7d6b79c09`.
Five shards preserve the existing quantized tensors; there is no further
quantization. The Qwen model and its runtime assets are served from the same origin.
The published workshop system prompt and nonthinking chat template are retained.

The optional [Bonsai image section](https://tetrarchs.com/model-bench/#art-demo)
on the same page has a separate load button, prompt, step progress, generation
timer, PNG download, and JSON timing export. It runs **Bonsai Image 4B ternary**
locally through WebGPU. No image inference service or API key is required.
Defaults are 768×512 landscape, four denoising steps, guidance 1, and a fresh random seed.
The text model and image model can be loaded independently; unload either to
release its memory. For timings, generate with one model at a time.

"Use card description" makes an editable fantasy-art prompt from the description
and, when available, the generated card's name and type. This is a prompt template,
not a separate prompt-writing model. Step progress is shown during denoising;
the image appears after VAE decoding. Stop terminates the worker and unloads
Bonsai immediately. Load again to resume; cached weights are retained.

The image template describes a detailed fantasy painting with painterly realism,
detailed materials, dramatic lighting, and atmospheric depth, filling the image
edge to edge. The default prompt specifies a wide landscape composition;
"Use card description" matches its composition wording to the selected size.
Both product terms ("collectible card", "art proof") and the explicit Magic:
The Gathering style reference are omitted after user trials produced layouts
and lettering. The former list of unwanted layout elements is also omitted.
The description remains editable: translate mechanics into visible actions or
effects before generating. This template does not itself interpret game rules.

**Negative prompts are unsupported by the pinned WebGPU runtime.** Its option
normalizer accepts one prompt, discards unknown options such as `negative_prompt`,
and rejects guidance values other than 1. A negative-prompt field would have no
effect, so the demo does not expose one. The [Bonsai model documentation](https://huggingface.co/prism-ml/bonsai-image-ternary-4B-mlx-2bit#best-practices)
also specifies guidance 1 without classifier-free guidance and recommends four
steps; extra steps are not necessarily a quality improvement.

Image timing history stays in localStorage, with prompt, actual seed, size,
steps, model/runtime identity, GPU details, load time, step times, and total
generation time. PNGs are kept in memory until replaced or the page closes;
use Download PNG to save them. Prompts and images are not uploaded.

### Bonsai runtime and weights

The model is `prism-ml/bonsai-image-ternary-4B-mlx-2bit` at revision
`2c24c81b934a658ba5590cf39088ba929985b4a8`, approximately 3.9 GB including its text
encoder and decoder. The original weights are served locally without conversion.
All downloaded weights and configurations are checked against the upstream
manifest. Its README entry has a stale hash and is intentionally omitted;
weight verification is not relaxed. The model's Apache-2.0 LICENSE and NOTICE
are included in the served directory.

Upstream currently distributes its WebGPU engine in a compiled
[WebML Community demo](https://huggingface.co/spaces/webml-community/bonsai-image-webgpu).
`bonsai-loader.js` fetches that bundle from Hugging Face on first use, caches it,
and verifies SHA-256 `8e1726c485bfdae81ad7fa479a73a60cc27313a40e5b76b588245d1c9416f0eb`.
It exposes only the pipeline portion of that exact bundle in a module worker.
The upstream landing page and sharing code are not executed. No compiled
third-party bundle is checked into this repository. This is an experimental
adapter to a pinned demo, not a stable upstream library API; changing the bundle
requires rechecking its boundaries and pipeline interface.

The image runtime's first fetch reaches Hugging Face; image weights and our UI
come from tetrarchs.com. CORS and the existing isolation headers permit this.
CacheStorage retains runtime/configuration responses and range-read weight
chunks. WebGPU is required; there is no CPU or server inference fallback for
Bonsai. The worker handles GPU loss and load errors without affecting text
generation.

Cached files survive page reloads, but the loaded model and GPU allocations do
not. Both load buttons initialize the runtime and load weights into memory again;
they reuse cached downloads when available. Loading Bonsai does not unload Qwen.

### Card text controls

Choose Auto to prefer WebGPU, or CPU to force WebAssembly inference. GPU use
is reported from the runtime's layer-offload log rather than inferred solely
from browser API availability. The runtime may select its compatibility build
on older browsers. Compatibility and GPU performance require testing on the
actual device; the deployment host's browser test exercises CPU mode.

Click **Load model**, then **Generate card**. The model cache avoids repeat
weight downloads. The default is one generation, temperature 0.7, top-p 0.8,
top-k 20, a fresh random seed, 4,096 context tokens, and 512 output tokens.
Prompt caching is off by default;
enable it explicitly for warm-prompt experiments. Changing backend, threads,
or context requires unloading the model first. Sampling and output limits
remain editable between requests without reloading the model.

Temperature 0 selects greedy decoding (the original benchmark behavior) and
disables the other sampling controls. Higher temperatures allow more varied
outputs, potentially at the cost of correctness. The advanced controls expose
top-p (probability mass) and top-k (candidate count); 1 and 0 disable their
respective filters. Min-p is explicitly disabled.

Random mode chooses a concrete seed for each request and displays it in the seed
field. Turn random mode off to reuse that seed. History and exported results
include the actual seed, sampling values, and cache setting. A seed assists
repeatability on the same runtime/device; it does not guarantee identical
results across devices or numerical backends. Prompt caching reuses computation
and remains compatible with fresh random sampling.

Do not set `seed` in `loadModel`: wllama 3.8.1 overlays that value on every
completion and overrides the per-request seed. This page sets it per request.

The real-model sampling check used a separate short naming prompt. Two random
seeds produced different names; reusing the second seed reproduced its output
while retaining 32 cached prompt tokens. Temperature 0 and narrow mobile layout
also passed. These are sampling checks, not full-card quality measurements; see
[sampling-validation.json](sampling-validation.json).

Measurements distinguish:

- Model load: download/cache lookup plus runtime initialization, with both
  components retained in exported results.
- First token: Generate click to the first visible text fragment.
- Total generation: prompt processing plus decoding and stream completion;
  model loading is excluded.
- Decode tokens/second: the runtime's reported decoding rate and token count,
  not the number of JavaScript callbacks or a text-length estimate.

Output is streamed as plain text. A completed JSON parse checks syntax only;
the text benchmark does not run mtgish. Image generation is a separate action
in the Bonsai section. Cancellation is supported and
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
python3 prepare-bonsai.py
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
release directory, with shared model files at `models/af725e31/` and
`image-models/bonsai-ternary-2c24c81/`.
Dependencies and app source are in Git; generated assets, browser profiles,
model files, and local test outputs are ignored.

## Browser smoke check

This runs the real full model in Chromium CPU mode, observes streamed text,
checks complete JSON and timing fields, then cancels a second request. It also
checks that generation makes no network POST or workshop inference request.
It explicitly selects temperature 0 to retain the original benchmark settings
and requires several GB of free storage and memory.

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

### Bonsai validation and user benchmark

The user reported successful generation at **768×512, eight steps, approximately
16 seconds** on October 9, 2026. This is a manually reported observation, not an
automated measurement or a promise of performance on other devices. The device,
browser, prompt, and seed were not supplied with this result.

The deployment host has software WebGPU (SwiftShader). The actual pinned engine
passed GPU tensor upload/readback and module-worker initialization. All model
components loaded successfully in 41.02 seconds initially and 13.94 seconds from
cache. A 256×256, one-step generation exceeded the diagnostic's 180-second limit
during denoising; no completed image is claimed for that host test.

Separate UI tests used a fixture image to check display, PNG download, timing
export, cancellation, and mobile layout. Unsupported-GPU recovery, HTTPS
isolation headers, and model byte-range responses also passed. These fixture
tests are distinct from the user's successful real-model generation. See
[bonsai-validation.json](bonsai-validation.json) for the validation receipt.
