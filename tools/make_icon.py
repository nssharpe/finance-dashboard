"""Render app/static/favicon.svg into a multi-size Windows .ico.

The favicon is three primitives (rounded square, polyline, dot), so we redraw
them with PIL rather than pulling in an SVG rasterizer. Everything is drawn at
1024px and downsampled, so the 16px taskbar version stays clean -- drawing at
16px directly, or upscaling from 32px, gives a muddy icon.

    python tools/make_icon.py

Requires Pillow (not in requirements.txt -- this is a one-off dev script).
"""

import pathlib

from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "static" / "finance-dashboard.ico"

S = 1024          # supersampled canvas
K = S / 32.0      # favicon.svg viewBox is 32x32

BLUE = (37, 99, 235, 255)   # #2563EB
WHITE = (255, 255, 255, 255)

# favicon.svg geometry, in viewBox units
RADIUS = 6
LINE = [(4, 24), (10, 16), (16, 20), (22, 10), (28, 14)]
STROKE = 2.5
DOT_C, DOT_R = (28, 14), 2

SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def px(pt):
    return (pt[0] * K, pt[1] * K)


def main():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=RADIUS * K, fill=BLUE)

    w = STROKE * K
    d.line([px(p) for p in LINE], fill=WHITE, width=int(w), joint="curve")

    # joint="curve" rounds the joins but not the two end caps; add those by hand.
    for p in (LINE[0], LINE[-1]):
        x, y = px(p)
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=WHITE)

    x, y = px(DOT_C)
    r = DOT_R * K
    d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE)

    img.resize((256, 256), Image.LANCZOS).save(OUT, format="ICO", sizes=SIZES)
    print(f"wrote {OUT} with sizes {SIZES}")


if __name__ == "__main__":
    main()
