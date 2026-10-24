#!/usr/bin/env python3
"""Generate every app icon and splash image from code -- no binary art to keep in git.

    python3 tools/make_icons.py

Writes:
  frontend/icons/            192/512 + maskable PNGs for the web manifest
  assets/icon.png            1024x1024 source for `npx capacitor-assets generate`
  assets/icon-foreground.png art only, inside Android's 66% adaptive safe zone
  assets/icon-background.png flat background layer for the adaptive icon
  assets/splash.png          2732x2732 launch screen (+ splash-dark.png)

The artwork is a gold ballot box with a ballot going in. Everything is drawn at 4x and
downsampled, which is the cheapest way to get clean antialiased edges out of PIL.
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BG = (15, 20, 32, 255)        # --bg  #0f1420
BG_DEEP = (11, 15, 24, 255)   # topbar #0b0f18
GOLD = (244, 180, 0, 255)     # --accent
GOLD_DARK = (198, 143, 0, 255)
PAPER = (247, 249, 253, 255)
SS = 4                        # supersampling factor


def _art(size: int, draw_bg: bool, scale: float = 1.0) -> Image.Image:
    """The icon at `size` px. `scale` shrinks the artwork for Android's adaptive safe zone."""
    S = size * SS
    img = Image.new("RGBA", (S, S), BG if draw_bg else (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def box(x0, y0, x1, y1, r, fill):
        # Coordinates are fractions of the canvas, centred then scaled.
        a, b, c, e = [(0.5 + (v - 0.5) * scale) * S for v in (x0, y0, x1, y1)]
        # PIL rejects a radius over half the shorter side (its inner rectangle goes degenerate
        # once rounded to ints), which the thin slot hits at small scales. Leave a pixel of slack.
        radius = max(0.0, min(r * scale * S, (c - a) / 2 - 1, (e - b) / 2 - 1))
        d.rounded_rectangle([a, b, c, e], radius=radius, fill=fill)

    # --- ballot slip, drawn on its own layer so it can be rotated ---
    slip = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(slip)
    sx0, sy0, sx1, sy1 = (0.5 + (v - 0.5) * scale for v in (0.345, 0.135, 0.655, 0.545))
    sd.rounded_rectangle([sx0 * S, sy0 * S, sx1 * S, sy1 * S], radius=0.022 * scale * S, fill=PAPER)
    # a gold tick on the ballot
    w = 0.030 * scale * S
    cx, cy = 0.5 * S, (sy0 + (sy1 - sy0) * 0.42) * S
    sd.line([(cx - 0.075 * scale * S, cy), (cx - 0.018 * scale * S, cy + 0.058 * scale * S),
             (cx + 0.082 * scale * S, cy - 0.062 * scale * S)], fill=GOLD, width=int(w), joint="curve")
    # two ruled lines under the tick
    for i, dy in enumerate((0.105, 0.155)):
        half = (0.072 if i == 0 else 0.055) * scale * S
        y = cy + dy * scale * S
        sd.rounded_rectangle([cx - half, y, cx + half, y + 0.016 * scale * S],
                             radius=0.008 * scale * S, fill=(205, 213, 228, 255))
    img.alpha_composite(slip.rotate(-11, resample=Image.BICUBIC, center=(0.5 * S, 0.45 * S)))

    # --- ballot box, drawn over the slip so the paper looks inserted ---
    box(0.185, 0.425, 0.815, 0.815, 0.055, GOLD)
    box(0.185, 0.735, 0.815, 0.815, 0.055, GOLD_DARK)      # shadowed base
    box(0.300, 0.455, 0.700, 0.520, 0.032, BG_DEEP)        # the slot
    return img.resize((size, size), Image.LANCZOS)


def splash(w: int, h: int, bg) -> Image.Image:
    img = Image.new("RGBA", (w, h), bg)
    mark = _art(int(min(w, h) * 0.30), draw_bg=False)
    img.alpha_composite(mark, ((w - mark.width) // 2, (h - mark.height) // 2))
    return img


def main() -> None:
    icons, assets = ROOT / "frontend" / "icons", ROOT / "assets"
    icons.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)

    written = []
    for size in (192, 512):
        p = icons / f"icon-{size}.png"
        _art(size, draw_bg=True).save(p)
        written.append(p)
    # Maskable: Android may crop to a circle, so the art sits inside the 80% safe area.
    p = icons / "icon-512-maskable.png"
    _art(512, draw_bg=True, scale=0.78).save(p)
    written.append(p)

    _art(1024, draw_bg=True).save(assets / "icon.png")
    _art(1024, draw_bg=False, scale=0.66).save(assets / "icon-foreground.png")
    Image.new("RGBA", (1024, 1024), BG).save(assets / "icon-background.png")
    written += [assets / "icon.png", assets / "icon-foreground.png", assets / "icon-background.png"]

    splash(2732, 2732, BG).save(assets / "splash.png")
    splash(2732, 2732, BG_DEEP).save(assets / "splash-dark.png")
    written += [assets / "splash.png", assets / "splash-dark.png"]

    for p in written:
        print(f"  {p.relative_to(ROOT)}  ({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
