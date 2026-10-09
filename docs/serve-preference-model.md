# Run the preference model locally

The [round-two report](../reports/preference-round-two/README.md) distinguishes
training measurements, frozen evaluation and interactive sampling. The workshop
is an experimental preview; complex library and planeswalker requests can still
fail. Keep schema and Oracle validation visible to the caller.

## Download the serving quant

The model repository includes `model-Q4_0.gguf`, the exact artifact used in the
workshop and production evaluation. Download it at the published immutable
revision to avoid repeating the merge/export:

```python
import json
from pathlib import Path
from huggingface_hub import hf_hub_download

release = json.loads(Path("release-status.json").read_text())["preference_round_two"]["model"]
for name in ("model-Q4_0.gguf", "system-prompt.txt", "serving-candidate.json"):
    hf_hub_download(release["repo_id"], name, revision=release["revision"], local_dir="runs/dpo-q4")
```

Use the server command below with `runs/dpo-q4/model-Q4_0.gguf`. For the request
example, read the downloaded `runs/dpo-q4/system-prompt.txt`. Its recorded GGUF
SHA-256 is `af725e3116b06145d96c23d1626dad6f512b52021491722cdb6883e7d6b79c09`.

## Rebuild the quant from the adapter

Use the model publication revision in `release-status.json` to reproduce the
released adapter. It already contains the completed SFT starting point plus the
DPO update. Load it once on the original base; do not stack the SFT adapter.
The published intermediate checkpoints are alternatives, not additional layers.

```python
import json
from pathlib import Path
from shutil import copyfile
from huggingface_hub import snapshot_download

release = json.loads(Path("release-status.json").read_text())["preference_round_two"]["model"]
root = Path("runs/dpo-serving")
adapter = root / "adapter"
adapter.mkdir(parents=True, exist_ok=True)
names = ["adapter_config.json", "adapter_model.safetensors",
         "training-summary.json", "identity.json", "phase-complete.json", "system-prompt.txt"]
download = Path(snapshot_download(release["repo_id"], revision=release["revision"], allow_patterns=names))
for name in names:
    copyfile(download / name, (adapter if name.startswith("adapter_") else root) / name)
```

Use a CPU Transformers/PEFT environment compatible with `src/rl_gpu.py.lock`
and the llama.cpp converter and quantizer from release `b11443`. The export tool
records exact tool/source hashes, verifies the trained adapter hash, safely
merges it into the pinned BF16 base, then produces F16 GGUF followed by one
Q4_0 quantization pass. Export takes substantially more RAM and disk than serving.

```bash
python src/export_gguf.py \
  --adapter runs/dpo-serving/adapter --dpo-run runs/dpo-serving \
  --output runs/dpo-q4 \
  --converter /path/to/llama.cpp/convert_hf_to_gguf.py \
  --quantizer /path/to/llama-quantize --quantization Q4_0
cp runs/dpo-serving/system-prompt.txt runs/dpo-q4/system-prompt.txt

/path/to/llama-server -m runs/dpo-q4/model-Q4_0.gguf \
  --host 127.0.0.1 --port 4205 --threads 8 --threads-batch 8 \
  --threads-http 4 --ctx-size 12288 --parallel 2 --jinja --no-webui \
  --cache-ram 256 --chat-template-kwargs '{"enable_thinking":false}'
```

The two slots retain 6,144 tokens each. The standalone API listens on loopback;
the workshop exposes its own application endpoints through Caddy, not the raw
model endpoint. The system prompt downloaded above already includes the frozen
terminology guide; do not append that guide a second time.

## Generate a draft or two alternatives

This example uses the same single-output decoding as the scored evaluation:

```python
import json
from pathlib import Path
from urllib.request import Request, urlopen

payload = {
    "messages": [
        {"role": "system", "content": Path("runs/dpo-q4/system-prompt.txt").read_text()},
        {"role": "user", "content": "a midsize red giant that rummages when it enters"},
    ],
    "temperature": 0, "seed": 17, "max_tokens": 2048,
    "chat_template_kwargs": {"enable_thinking": False},
}
# For two interactive alternatives, enable this block. It is a different mode
# from the greedy benchmark. Record the seed with any saved user preference.
# payload.update(n=2, temperature=0.35, top_p=0.9, top_k=40, min_p=0.05,
#                cache_prompt=True)
request = Request("http://127.0.0.1:4205/v1/chat/completions",
                  data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
with urlopen(request, timeout=300) as response:
    result = json.load(response)
for choice in result["choices"]:
    print(choice["message"]["content"])
```

llama.cpp's `n=2` path processes one prompt and copies its state into the child
slot, offsetting the child's sampling seed. It still decodes both answers and
can produce duplicates. On one measured Giant request, a single greedy draft
took 8.16 seconds and two sampled drafts took 11.61 seconds. This is not a
controlled throughput benchmark. Server aggregate `usage` may describe only the
first completion; do not interpret it as total batch token cost.

## Workshop choices, art and rollback

The editor implementation is on
[`codex/card-generator-preferences-20261009`](https://github.com/vishvananda/vizier-editor/tree/codex/card-generator-preferences-20261009).
`research/card-authoring/src/head_to_head_models.py` implements shared-prompt
inference; `head_to_head_choices.py` persists explicit choices. The public model
and dataset do not require that editor repository.

Automatic draft population records no vote. Explicit choices preserve the
description, candidate outputs, model hash, decoding settings, validator reports,
visible order and selected ID. No-preference events and unusable/duplicate local
pairs are kept separate. Later edits do not overwrite chosen answers. These
anonymous, unblinded signals need curation before training; they are not proofs
of Oracle correctness. Local user choices are excluded from automatic HF uploads.

**Generate art for this version** gives Luna the selected original card JSON to
write an illustration prompt, then calls the image generator. A selection change
alone does not create an image. The source draft hash, prompt and immutable image
URL are retained, and the editor attributes the image to **Luna Imagen**.

On the deployment host, the new model is on loopback port 4205 and its backend
on 4199. The previous backend/model remain on 4197/4204, and the SFT baseline
remains on 4203. To roll back the generator, point the Caddy application proxy
back to 4197 and reload its validated configuration. To roll back this editor
release, atomically point `/srv/vizier-editor/current` at the recorded previous
release in `deployment.json`. Preserve older immutable assets for open tabs.

Before switching, check model identity through application health, a real
same-origin generation, validators, saved choice records and a browser import.
The deployment's public smoke comparisons are explicitly excluded from future
preference curation. The locked development/release benchmarks stay separate
from both interactive sampling and these smoke tests.
