# Browser Oracle validation and revision experiment

The Forge runs the same pinned mtgish Go parser used by the training evaluations
inside a dedicated Web Worker. No card text is sent to a server. The UI says
**Oracle text parses**, rather than certifying correctness or balance. A failure
can indicate unsupported grammar, including an unfamiliar planeswalker subtype.

## Build

Run `python src/setup_mtgish.py` once, following the repository setup instructions.
Then, from the repository root, with Go available:

```sh
python web/mtgish/build.py
cd web/client-benchmark
npm ci
node prepare-forge.mjs /path/to/frontend/dist/card-preview
npm run build
```

The build uses upstream revision `153451e44d88baabfeb9902092032c328f4d5416`.
It embeds the **generated plural grammars**, short names, ignore list, upstream
corrections and all known quoted names. It reuses `src/mtgish-adapter.go`'s complete
English → mtgish → FULL_CARD check. Changes to the copied Go code are limited to
filesystem embedding, a browser callback, more frequent Go garbage collection,
and exposing the existing farthest-match cursor; the grammar and matching algorithm are unchanged. The original
checkout is never modified. The browser adapter implements the single-face
`preprocess_scryfall.cardInfo` flow, including loyalty brackets and CARDNAME
binding. Unsupported multi-face inputs are rejected rather than flattened.

The artifact is about 14.6 MB uncompressed / 2.7 MB gzip, without the full Oracle
card database. Both upstream and Go licenses ship beside it. The build manifest
records grammar, bridge, adapter, runtime and artifact hashes. The app build
includes the parser manifest in its version identifier. Compiled artifacts belong
in the deployed static assets, not Git; `build.py` reproduces them.

The worker has a 20-second initialization limit, an 8-second per-card limit,
a 64-card result cache and serial execution. Edits are debounced, and late
results cannot overwrite the selected card's status. Timeout or unavailable
validation is not reported as an invalid card. Details expose the original
parser error and normalized farthest-match context, explicitly identified as a
hint. Card-specific upstream corrections are disclosed on successful parses.

## Verify parity

`parser-fixtures.json` contains 26 small smoke cases with native reference results:
the five custom demo cards, eight revision inputs/results, a vanilla creature,
a planeswalker with a supported subtype, named-token examples and a known
unsupported sweeper. It is a regression fixture, not a training dataset.

```sh
cd web/client-benchmark
CHROME_BIN=/path/to/chrome FORGE_URL=http://localhost:8793/forge/ \
  node check-forge-validator.mjs
```

It compares status, complete parse, normalized input, generated mtgish, upstream
rewrites and failure context. All 26 matched in Chromium and Playwright WebKit.
This measures implementation parity, not Oracle accuracy or device performance.

## Local model edits

`forge/revision-prompt.js` is shared by browser inference and the repeatable
native-model smoke experiment:

```sh
python web/mtgish/evaluate_revisions.py \
  --endpoint http://127.0.0.1:4205/v1/chat/completions \
  --out artifacts/browser-revision-results.json
```

Run a llama.cpp server with the deployed Qwen3 4B SFT + DPO round-two Q4_0 weights,
using `enable_thinking=false`. Revisions use temperature 0.2, top-p 0.8, top-k 20
and a 768-token limit. The initial native experiment used seed 42; the browser
uses a fresh seed. It is the same quantized model, not a browser timing benchmark.

The initial eight-case smoke test applied **4/4 requested edits** correctly
(keyword, stats, draw amount, name). **2/4 repair attempts** turned rejected text
into accepted text (draw grammar and loot sequencing). The other two retained
unsupported wording; the planeswalker also had an unsupported custom subtype.
These are hand-selected examples, not a statistical quality estimate. The
concrete replacement default prompt also preserved all requested details and
passed mtgish.

The UI creates a revised copy and keeps the original and artwork. Repair can
change only `oracle_text`; model-proposed changes to other fields are ignored,
and a name change is rejected. A failed repair leaves the original intact.
Passing mtgish does not establish that meaning was preserved: users should still
compare the revised wording. There is one attempt per click, no automatic loop.

The browser bridge uses Go `GOGC=25` behavior to collect temporary grammar
allocations earlier. In the local smoke run, initial WASM linear memory fell
from about 438 MiB to 307–310 MiB; it peaked at 444–454 MiB over all 26 cases.
Initialization took about eight seconds on the shared host. These figures
exclude browser/renderer overhead and are not physical-phone measurements.
All 26 native-parity checks still pass in Chromium and WebKit.

A follow-up smoke case used `Smaug the Unkillable` with `Whenever Smaug
attacks, draw a card, then discard a card.` Upstream recognizes custom
comma-separated short names, but this non-comma abbreviation is absent from its
known-name aliases. The original repair prompt repeated the rejected text; a
conditional self-reference hint made the same quant at seed 42 use `CARDNAME`,
which parsed with all other fields unchanged. This is one paired experiment,
not a broader success-rate estimate. The hint appears in Details and in the
repair prompt; validation itself remains unchanged. No automatic alias rewrite
or card renaming is performed.
