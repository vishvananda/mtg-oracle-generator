# MTG CardForge

Live at **https://tetrarcum.com/**; also served at
https://tetrarchs.com/forge/. Desktop users can load the card and art models,
describe a card, and generate both locally. The five rotating showcase cards
are handcrafted examples with generated artwork, not model evaluation results.
They open with Vesper, Eclipse Sovereign, followed by a planeswalker, a forest
legend, an ability-copying land and a legendary artificer. The default visible
artist credit is MTG CardForge and remains editable.

**Edit your own card** is available immediately, without downloading models.
Click names, cost, type, rules, stats, artist credit, set symbol, border, frame or artwork to edit.
Names and rules use native inputs directly on the card. Click away or tap **Done**
to save; Enter saves the name, Ctrl/Command+Enter saves rules, and Escape cancels.
The card stays still during selection and typing. Rules preserve newlines;
self-references display the card's name. No model download is needed.
The set picker has 323 named sets with supported symbols, including Forge.
Click the outer border for black/white stock, or the frame around the artwork
for modern/old frames. Both can follow the selected set automatically. Mana
costs offer clickable colored, generic, hybrid, snow and Phyrexian symbols.
The finish menu comes directly from the renderer and includes Cold foil.
Set, frame, border and finish choices are retained with each saved card. Before loading the
models, artwork can be imported from an HTTPS URL or a file. After loading,
the art editor also offers an editable prompt and **Regenerate art**, which
preserves the card text. Image imports are validated in the browser, limited
to 12 MB and 16 megapixels, and cropped to fill the renderer's art window.
Remote image servers must permit CORS; a file import is the fallback.

Mobile devices get the carousel and manual editor without importing model
runtimes or downloading weights. Both models independently crashed during
loading on the user's iPhone; a smaller combined batch would not solve that.
The restriction is conservative device detection, not a measured RAM limit.

The latest 20 creations and their artwork are stored in IndexedDB on this
origin. **Save card** exports JSON with embedded artwork. Browser storage may
be evicted; exported files preserve a copy. There is no account, upload,
server inference fallback, or Oracle validator in this standalone prototype.
It currently supports single-face designs. Music and sound cues are optional;
the page remains fully usable with sound disabled.

## Models and runtime

The page lazily imports `local-models.js` only after the desktop load button
is pressed. It reuses the benchmark's pinned model, runtime, weight URLs and
cache namespaces at `/model-bench/`. See [the benchmark README](../README.md)
for exact revisions, licenses, sizes, hashes and browser prerequisites.
Browser caches are origin-specific: tetrarcum.com and tetrarchs.com cannot
share a browser's cached weights or saved cards.

- Text: Qwen3 4B SFT + round-two DPO Q4_0, about 2.37 GB; temperature 0.7,
  top-p 0.8, top-k 20, random seed, prompt caching, 768 output tokens.
- Art: Bonsai Image 4B ternary, about 3.9 GB; 768 × 512 landscape, four steps,
  guidance 1, random seed. No negative-prompt support in the pinned runtime.
- Generation is sequential: card JSON, then artwork. Completed text survives
  art failure or cancellation. Cancelling artwork unloads its worker; load
  again before the next generation.
- The art prompt is an editable template from the description and card type,
  not a separate language-model pass. It avoids branded card-game references.
- GPU compatibility and combined memory use must be tested on actual devices.

## Standalone production renderer

The renderer is built from the matching Vizier frontend on branch
`codex/standalone-card-preview-20261009`, revision
`e1609d318b4ea71249e35549dae8c6cf317b5892`, rebased onto the editor’s
`a52acb70fef227426599b6ebaa4fb80fe8f95619` renderer.
It includes the production card face, mana, tilt and foil presentation, without
host, account, deckbuilder or game engine modules. Its build checks this module
boundary and enforces 80 KiB gzip JavaScript / 8 KiB gzip CSS budgets.
Fonts and frame textures still use the renderer's pinned external asset sources.
The standalone entry is not a fully offline asset bundle.

```bash
# In a checkout of vishvananda/vizier-frontend at the branch above:
npm ci
npm run build:card-preview

# In this repository:
cd web/client-benchmark
npm ci
npm run build
node prepare-forge.mjs /path/to/vizier-frontend/dist/card-preview
```

`prepare-forge.mjs` verifies the renderer manifest's file hashes before staging
it. Compiled renderer artifacts and model weights are not committed here.
Install the benchmark model configuration, prompt and weights as described in
its README. Serve `dist/forge` as the site root and `dist` at `/model-bench/`.
Both need these headers:

```text
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
Cross-Origin-Resource-Policy: same-origin
X-Content-Type-Options: nosniff
```

The production host stages ordinary static files into timestamped releases,
links the existing weight directories, then atomically swaps
`/srv/mtg-oracle-client/current`. Its Caddy routes serve the same release at
both URLs above. The normal editor and its backend run independently.

## Browser verification

```bash
CHROME_BIN=/path/to/chrome FORGE_URL=https://tetrarcum.com/ \
  FORGE_EVIDENCE=/tmp/forge-check node check-forge.mjs
```

The check uses the real renderer and real image imports for desktop/mobile
editing, persistence, export, sound, and lazy loading. A separate browser
context replaces `local-models.js` with an explicit fixture to test generation,
art regeneration and cancellation without a multi-GB inference run. The
receipt states this limitation; it is **not a GPU quality or speed benchmark**.
Use the actual page and model lab for hardware measurements.

The [2026-10-09 receipt](../forge-verification-20261009.json) records the passing
Forge browser scenarios and exact renderer hashes. The shared frontend passed
1,214 unit tests, type checking and production builds. Its broader native game
browser suite still timed out on a battlefield land-hover scenario on this
host; an earlier WebGL run failed a special-card row-overlap assertion. These
are not recorded as passing gates. They concern the full game UI, which is
absent from this standalone page. Actual GPU inference was previously exercised
in the model lab on the user's M1; the combined Forge flow still needs device
inference testing beyond the explicit UI fixture.

The [2026-10-10 receipt](../forge-verification-20261010.json) records 50 passing
live browser checks, including inline editing checks and a standalone planeswalker
comparison. The shared frontend passed all 1,215 unit tests with four workers,
type checking and production builds within the existing budgets. The combined
`npm run check` hit a five-second Tetranum test timeout under parallel build/test
load; that same test passed in the focused and reduced-concurrency runs. Its
full-game browser gates were therefore skipped, not recorded as passing.

## Planned symbol and number editing

The next pass replaces the remaining modal-style fields with one anchored,
snapping picker. This is a design plan, not functionality in the current release.

- **Set symbol:** vertical carousel of sets, horizontal carousel of rarities;
  every item uses the actual rendered symbol. Keep the centered set name visible
  and provide search for the 323-set list.
- **Mana:** click an individual symbol; scroll symbol families/colors, or numbers
  for generic mana. A nearby **+** inserts a symbol. In rules, it inserts at the
  caret or edits that exact symbol without replacing the surrounding text.
- **Type line:** individually scrollable card-type tokens with **+** for multiple
  types (for example Artifact Creature), toggle chips for supertypes, and a
  searchable subtype picker after the dash. Suggest subtypes for the active card
  types, allow custom text, and preserve multiple subtypes. Return to a normal
  printed type line when finished; do not silently erase incompatible fields.
- **Stats:** independent power/toughness and starting-loyalty scrollers. Loyalty
  ability costs include positive values, zero, negative values, X and −X.
  Keep direct typing for unusual values such as `*` or `1+*`.
- **Gestures:** wheel and trackpad, arrow keys, and pointer displacement from a
  center dead zone. Only an explicitly opened picker scrolls on pointer movement.
  Ramp speed gradually and stop at the dead zone. Touch uses a larger tray offset
  above the finger; lock to the intended axis to avoid accidental rarity changes.
- **Commit:** preview immediately, snap on settling, then commit on release/Done,
  Enter or click-away; Escape cancels. One completed gesture should be one undo
  step. Sort a completed mana cost only after editing settles, maintaining stable
  symbol identities for animation. Do not reorder separate ability costs, tap
  symbols, mana-production alternatives, or ordinary rules text.

Implement and test the picker with set/rarity first, then reuse its gesture and
accessibility behavior for mana and stats. Rules need addressable inline symbol
tokens before the same picker can safely edit individual ability costs.

## Planeswalker comparison

The 2026-10-10 pass compares the blue **Jace, Unraveler of Secrets** with
[its SOI printing](https://scryfall.com/card/soi/69/jace-unraveler-of-secrets).
It fixes an unstyled accessibility copy leaking into the standalone text box,
expands the inset paper to fit its frame, distributes ability spacing, reduces
loyalty cost text, and restores the starting shield's proportions. DOM and canvas
share the layout and badge offsets. The comparison uses current Oracle text,
which includes the later Legendary supertype, so it is not a pixel-identical
reproduction of the historical printing.

```bash
CHROME_BIN=/path/to/chrome FORGE_URL=https://tetrarcum.com/ \
  FORGE_EVIDENCE=/tmp/forge-planeswalker node compare-planeswalker.mjs
# Optional: run Vite in the matching frontend checkout, and also set
# CANVAS_URL=http://127.0.0.1:5173 to include its Canvas2D face beside the DOM.
```

Reference scans and artwork are fetched into the evidence directory only;
they are not bundled with the application or committed as showcase assets.

## Assets and audio

The set-name snapshot in `sets.json` comes from the [Scryfall sets API](https://scryfall.com/docs/api/sets),
filtered to symbols supported by the renderer. Run
`node update-forge-sets.mjs /path/to/vizier-frontend` from the benchmark directory
to refresh it. The picker fetches this snapshot locally; it does not query Scryfall
at runtime. Official set vectors retain the renderer’s pinned Keyrune source
and license.


The background, giant, merfolk, stag and dragon WebP illustrations were generated
for this page with the image generation tool on 2026-10-09. They are bundled
only as lightweight showcase assets. The renderer's `FORGE` set mark is an
original spark-and-anvil SVG, colored by rarity and reused by foil masks.
Cormorant Garamond display fonts retain their OFL in `assets/font-license.txt`.

`assets/opening.m4a` is the existing Adventure opening theme, copied from
Vizier frontend `public/audio/opening.m4a` at the user's request. It retains
its existing asset provenance and rights; it is not a newly generated song
or part of the model dataset. It streams only after a gesture, loops at a
quiet level, pauses in the background, and shares the remembered Sound toggle
with the original synthesized interface cues. Replace that file to try the
user's new track without changing the UI or model code.
