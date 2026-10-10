# Aurum jewel logos

These are offline renders of the user's Aurum v10 jewel scene, supplied as
`aurum-tetrahedron-source-v10.zip` and deployed at `https://tetrarchs.com/aurum/`.
`optics.js` preserves its actual spindle geometry and spectral refraction /
internal-reflection shader. `scene.js` adapts the same studio light panels,
polished gold, nacre bump texture and iridescent pearl materials to two shapes.
No screenshots or remote assets are needed to reproduce the images.

- **Dual tetrahedron:** two regular tetrahedra sharing a triangle. White and
  black pearls at the tips; red, green and blue pearls around the equator.
  Each of the nine spindle stones uses the arithmetic midpoint of its endpoint
  pearl colors **in linear-light RGB**. This sets tint and wavelength-dependent
  absorption; studio highlights and refraction still change the displayed color.
- **Cube:** eight alternating ivory / pale-gold pearls and twelve champagne
  spindle stones. The midpoint color requirement applies only to the dual.

The browser gets a transparent, 192 × 192 animated WebP: 360 frames over a
24-second rotation. It does **not** load Three.js or execute the gem shaders.
This keeps a second WebGL renderer away from the card renderer and local models.
The header only downloads the chosen animation, remembers the choice, and uses
a still image when reduced motion is requested or the logo/page is hidden.
The dual loop is 1.73 MB and the cube is 2.46 MB, fetched only when selected;
the stills are 6.4 / 8.6 KB. This trades a larger download for avoiding live
gemstone ray tracing. It is not a device benchmark against live WebGL, and
the current header does not support interactive rotation. The complete scene
remains available for an interactive version.
Query parameters `?logo=dual` and `?logo=cube` override a stored choice for
comparison. Clicking the logo changes designs; the adjacent wordmark is Home.

From `web/client-benchmark`:

```bash
npm ci
python3 -m venv /tmp/cardforge-logo-venv
/tmp/cardforge-logo-venv/bin/pip install -r logo-studio/requirements.txt
CHROME_BIN=/path/to/chrome LOGO_SIZE=192 LOGO_FRAME_COUNT=360 \
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
