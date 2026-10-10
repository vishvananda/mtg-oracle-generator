# Aurum jewel logos

These are offline renders of the user's Aurum v10 jewel scene, supplied as
`aurum-tetrahedron-source-v10.zip` and deployed at `https://tetrarchs.com/aurum/`.
`optics.js` preserves its actual spindle geometry and spectral refraction /
internal-reflection shader. `scene.js` adapts the same studio light panels,
polished gold, nacre bump texture and iridescent pearl materials to two shapes.
No screenshots or remote assets are needed to reproduce the images.

- **Dual tetrahedron:** two regular tetrahedra sharing a triangle. White and
  black pearls at the tips; red, forest green and sapphire blue pearls around the
  equator. The mana palette uses ivory `#f3ead7`, black `#10121b`, red `#bf392f`,
  green `#145b37` and blue `#174789` before lighting.
  Each of the nine spindle stones uses the arithmetic midpoint of its endpoint
  pearl colors **in linear-light RGB**. This sets tint and wavelength-dependent
  absorption; studio highlights and refraction still change the displayed color.
- **Cube:** eight alternating ivory / pale-gold pearls and twelve champagne
  spindle stones. The midpoint color requirement applies only to the dual.

The browser gets a transparent, 384 × 384 animated WebP: 360 frames over a
24-second rotation. Spindle stones are 75% of their original linear size (50% larger than the preceding version); pearls and their gold cups are 75% of the original size (50% larger than the
preceding half-size version). Spindles remain centered on each gold edge, with
connectors fitted to the pearl cups. The original optical calculations are preserved; `scene.js`
adjusts the output alpha from 0.76 to 0.96 with Fresnel strength so the page
shows through gemstone facets. Pearls and gold stay opaque. There is no added
CSS glow or shadow. The page does **not** load Three.js or execute the gem shaders.
This keeps another WebGL renderer away from the card renderer and local models.

The large jewel above the desktop headline defaults to the cube. Its display is
50% larger than the initial hero version, centered above the load button in a
reserved-height area so it extends upward without shifting the headline or button. Only the chosen
animation is downloaded; reduced motion or a hidden jewel/page uses a still.
Mobile keeps the small gold wireframe header mark and fetches no animation.
The dual loop is 5.09 MB and the cube is 7.20 MB; stills are 19 / 26 KB. Encoding
uses WebP quality 88, method 4, with full alpha quality (stills use quality 94).
This is not a device benchmark against live WebGL. The complete scene remains
available for an interactive version; the baked image cannot be freely rotated.
`?logo=dual` and `?logo=cube` select a design for comparison. Clicking the large
jewel switches designs; the header wordmark remains Home.

From `web/client-benchmark`:

```bash
npm ci
python3 -m venv /tmp/cardforge-logo-venv
/tmp/cardforge-logo-venv/bin/pip install -r logo-studio/requirements.txt
CHROME_BIN=/path/to/chrome LOGO_SIZE=384 LOGO_FRAME_COUNT=360 \
  node logo-studio/render.mjs
/tmp/cardforge-logo-venv/bin/python logo-studio/encode.py \
  /tmp/cardforge-jewel-frames forge/assets
npm run build
```

Three.js is pinned to Aurum's version, 0.180.0 (MIT), as a development dependency.
Pillow is only used for offline encoding. `LOGO_FRAMES` changes the PNG output
directory; use `LOGO_FRAME_COUNT=1 LOGO_SIZE=512` for a still preview. The encoder
rejects empty/clipped frames and verifies the resulting animation and alpha.
`forge/assets/jewels.json` records the shipped dimensions, frame count and sizes;
`designs.json` alongside the intermediate PNGs records endpoint and gem colors.
Generated PNG intermediates stay outside the repository.

Check the actual page with:

```bash
CHROME_BIN=/path/to/chrome FORGE_URL=https://tetrarchs.com/forge/ \
  node check-forge-logo.mjs
```
