"""Encode the offline Aurum frames as small, transparent browser-native loops."""
import argparse
import json
from pathlib import Path

from PIL import Image, features

parser = argparse.ArgumentParser()
parser.add_argument("frames", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--duration", type=int, default=24000)
args = parser.parse_args()
assert features.check("webp_anim"), "Pillow must support animated WebP"
args.output.mkdir(parents=True, exist_ok=True)
report = {"duration_ms": args.duration, "designs": {}}
for kind in ("dual", "cube"):
    images = [Image.open(p).convert("RGBA") for p in sorted((args.frames / kind).glob("*.png"))]
    assert len(images) > 1, "Render a full rotation before encoding"
    bounds = [im.getchannel("A").getbbox() for im in images]
    assert all(b and b[0] > 0 and b[1] > 0 and b[2] < im.width and b[3] < im.height
               for b, im in zip(bounds, images)), "A frame is empty or clipped"
    durations = [round((i + 1) * args.duration / len(images)) - round(i * args.duration / len(images))
                 for i in range(len(images))]
    animated = args.output / f"jewel-{kind}.webp"
    still = args.output / f"jewel-{kind}-still.webp"
    images[0].save(animated, save_all=True, append_images=images[1:], duration=durations,
                   loop=0, quality=82, method=6, minimize_size=True)
    images[0].save(still, quality=90, method=6)
    with Image.open(animated) as encoded:
        assert encoded.n_frames == len(images) and encoded.info["loop"] == 0
        assert encoded.mode == "RGBA", "The logo must retain transparency"
    report["designs"][kind] = {"frames": len(images), "width": images[0].width,
        "height": images[0].height, "bytes": animated.stat().st_size,
        "still_bytes": still.stat().st_size, "all_frames_unclipped": True}
    print(f"{kind}: {animated.stat().st_size:,} animated bytes, {still.stat().st_size:,} still bytes")
(args.output / "jewels.json").write_text(json.dumps(report, indent=2) + "\n")
