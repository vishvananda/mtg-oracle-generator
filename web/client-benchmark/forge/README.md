# MTG CardForge

Live at **https://tetrarcum.com/**; also served at
https://tetrarchs.com/forge/. `cardforge.fyi` is reserved for a later Pages + R2 launch. Iteration continues
on the current host. Desktop users can load the card and art models,
describe a card, and generate both locally. The five rotating showcase cards
are handcrafted examples with generated artwork, not model evaluation results.
They open with Vesper, Eclipse Sovereign, followed by a planeswalker, a forest
legend, an ability-copying land and a legendary artificer. The default visible
artist credit is MTG CardForge and remains editable.

**Edit your own card** is available immediately, without downloading models.
Click names, cost, type, rules, stats, artist credit, set symbol, border, frame or artwork to edit.
Names and rules use native inputs directly on the card. Click away or tap **Done**
to save; Enter saves the name, Ctrl/Command+Enter saves rules, and Escape cancels.
The card stays still during selection and typing, including while a mobile
keyboard changes the viewport height. Input font, visual size and spacing match
the printed text; the native control stays at least 16px to avoid iOS focus zoom.
Inputs are transparent; the old ink is hidden while editing. Foil temporarily
uses the textured native face so its baked text does not show through.
Rules preserve newlines;
self-references display the card's name. No model download is needed.
The previous complete card, including foil pixels, remains visible until the next
card’s images, fonts and treatment are ready. Initial loading leaves the card
blank. Editing hints use a subtle hover highlight and a visible keyboard focus
outline.
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

Use **Browse examples** and **Back to your cards** to switch galleries without
changing saved designs. Editing a sample creates a saved copy.
**Delete card** removes the selected saved creation. **Undo** restores its full
design and artwork until the notice is dismissed, another card is deleted or
the page is closed. Deleting the last creation returns to the examples; the
example cards themselves cannot be deleted. Undo needs an available slot in
the 20-card history; it never removes another card to make room. Artist and rarity stay editable
directly on the card, so there is no separate Card details button.

The latest 20 creations and their artwork are stored in IndexedDB on this
origin. **Save card** exports JSON with embedded artwork. Browser storage may
be evicted; exported files preserve a copy. There is no account, upload,
server inference fallback, or Oracle validator in this standalone prototype.
It currently supports single-face designs. The footer carries the standard notice from
[Wizards’ Fan Content Policy](https://company.wizards.com/en/legal/fancontentpolicy).
Repeated slogans, captions and editing hints are omitted; device requirements stay
collapsed behind the download note. Music and sound cues are optional;
the page remains fully usable with sound disabled.

The gently spinning header jewel reuses Aurum's pearl materials, spindle-cut
gemstones and refraction shader, baked into a transparent animation. Click it
to compare the default dual tetrahedron with a champagne cube; the choice is
remembered. `?logo=dual` and `?logo=cube` select either directly. Reduced motion
uses a still image. No extra WebGL context or 3D library is loaded by the page.
See [the jewel studio](../logo-studio/README.md) for source and reproduction.
The [jewel and deletion receipt](../forge-jewels-verification-20261010.json)
records desktop/mobile browser checks, artwork-preserving Undo, storage-error
handling, WebKit layout checks and the deployed asset hashes.

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
`1797a4c9e46e0dfbb179a3e7273cadc260dba090`, rebased onto the editor’s
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
node prepare-forge.mjs /path/to/vizier-frontend/dist/card-preview
npm run build
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

App files use `Cache-Control: no-cache` so a reload revalidates changed code and
styles. `npm run build` also versions the small app’s JS/CSS/JSON dependency
URLs from their content and the staged renderer manifest. This forces a new
renderer/style match even if an older Safari cached unversioned files. Stage the
renderer before the final build. Model URLs and model caches stay unchanged.

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

The [mobile and transport receipt](../forge-polish-verification-20261010.json)
records 50 live UI checks, 19 focused mobile/loading checks, eight transport
checks and SHA-256 verification of all published model assets. Crownroot's
Regular frame was compared with Etched at phone and keyboard-sized viewports
in Chromium and WebKit. Its reported iPhone offset did not reproduce; the
receipt does not claim that issue fixed. Physical iOS keyboard behavior and
fresh GPU inference remain device checks.

```bash
CHROME_BIN=/path/to/chrome FORGE_URL=https://tetrarchs.com/forge/ \
  node check-forge-polish.mjs
CHROME_BIN=/path/to/chrome FORGE_URL=https://tetrarchs.com/forge/ \
  node check-forge-regular.mjs
# FORGE_BROWSER=webkit uses an installed Playwright WebKit for the latter.
```

The [editing receipt](../forge-editing-verification-20261010.json) records the
50-flow UI check, 15 scroller checks, seven gesture checks, and 19 inline-editing
plus 11 frame-image checks in each of Chromium and WebKit. The deployed site also
passed the 15 scroller and seven gesture checks. Shared renderer unit tests
(1,216), typechecking and builds pass. Its full-game WebGL gate still reports
the previously seen `special-the` ability-row overlap; it is not a passing gate.

## Symbol and number editing

Symbols and numbers use wheels **on their printed positions**, with neighboring
choices above/below. There is no scroller dialog. Hold outside the neutral zone
(16px with a mouse, 24px on touch) to start scrolling after a 300ms dwell.
Holding farther from the center accelerates it, capped at four notches/second.
Moving upward pulls previous choices downward into the slot.
Returning to center, leaving, releasing touch, blurring or hiding the page stops
repeat scrolling. A short synthesized click plays at each snap when sound is on.
Touch uses 54px spacing to expose choices around the finger. Reduced motion
removes the snap animation. Mouse wheel/trackpad and arrow keys also work.
Trackpad scrolling needs 90px per step and ignores repeated steps within 180ms
to limit jumps from a burst of events; line-mode mouse wheels use three lines.

- Click a printed mana symbol to change it; **+** adds another and **−** removes
  the selected symbol. Committing orders mana and combines generic amounts.
- Sets scroll vertically, rarity horizontally (or Shift+wheel). The center is
  the printed set symbol. Type a set name/code to jump through all 323 choices.
  Incoming symbols have names beside them and an outline/shadow that preserves
  rarity colors. Clicking a name selects that set; hovering a name stays still.
- Click power, toughness or starting loyalty for its number wheel. Click a
  printed loyalty ability cost or rules mana symbol to edit that item directly.
  Signed ability costs include negative values and X; other wording is kept.
- Type words and supertypes have their own wheels and **+** controls. Subtypes
  use a transparent input on the type line and preserve multiple words.
  **× Remove [type]** appears beside the active wheel. Delete/Backspace also
  removes that word. At least one card type is retained, independent of how many
  supertypes are present. Removal never discards the subtypes.
- Click the selected center, **✓**, press Enter or click outside to save. **↶**
  or Escape cancels. The on-card draft changes immediately; the complete card
  frame/color updates after committing, so the wheel cannot move under the hand.
- Clicking the colored frame opens three direct choices: **Match set**,
  **Modern**, **Old frame**. One click applies the selection and closes it.
  The outer border similarly offers Match set, Black and White.
- While typing rules, **+ Mana** inserts at the caret. Close the text input to
  manipulate its printed symbols individually. The carousel pauses while editing.

```bash
CHROME_BIN=/path/to/chrome FORGE_URL=https://tetrarcum.com/ node check-forge-direct.mjs
# FORGE_BROWSER=webkit uses an installed Playwright WebKit.
# Older check-forge-scrollers/gestures entry points forward to this suite.
```

The [direct-editing receipt](../forge-direct-verification-20261010.json) includes
center alignment, pointer speed, stopping, emulated touch, persistence, frame
selection and WebKit font evidence. It does not claim physical Safari alignment
or real GPU model inference.
The [controls and layout receipt](../forge-controls-verification-20261010.json)
records the slower wheel, labeled type removal, named set symbols, Safari sizing
patch, simplified page copy and fan-content footer, including live checks.

Beleren’s source file is already bold, but it had only been registered at normal
weight. WebKit Canvas2D synthesized additional bold at 700 while the DOM inherited
`font-synthesis: none`. The new face declaration covers 400–700 and the texture
loader explicitly waits for 700. The browser font probe now measures identical
ink for both requests in WebKit; Chromium’s output was already identical.
`check-forge-fonts.mjs` captures regular/prismatic before-and-after images and
pixel sums. Use `FORGE_BEFORE` for a prior release and `FORGE_URL` for the new one.
The Safari Regular-frame offset was traced to percentage-height content
participating in the tilt container's intrinsic aspect-ratio sizing. The preview
now positions the premium face absolutely inside that container, keeping the
face and its width-scaled frame images on the same card rectangle. Regular still
uses the DOM face; this is not a canvas substitute. The Mac investigation supplied
the layout fix; local Linux WebKit checks cover geometry and editing, but do not
replace verification on an iPhone. Versioned app URLs fetch the new styles on
reload without changing the model cache.

The typography comparison uses [Toski's KHM printing](https://scryfall.com/card/khm/197/toski-bearer-of-secrets)
at equal card size. Title sizing stays unchanged; the modern type line is about
7% smaller. The demo stag is now **Crownroot, Spring Eternal**. Already-saved
copies retain their user-editable names. `compare-typography.mjs` captures the
reference scan and renderer side by side; reference assets are not distributed.
Regular-frame layers now use explicitly positioned, clipped images instead of
large CSS background sprites. Desktop/mobile Chromium and WebKit captures check
image proportions and bottom alignment; physical iPhone confirmation is still
needed for the reported rendering defect.

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
