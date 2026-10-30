#!/usr/bin/env python3
"""Generate the Visual Brain brand: a pointillist brain inside a gallery frame.

Writes brand/logo.svg, favicon.svg, brand/mark.svg and PNG icons for the
browser extension. Everything is drawn from one dot layout so every size
matches. Run from the repository root: python3 tools/build_brand.py
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
INK = "#24251f"
CREAM = "#f6f1e7"
MAT = "#fbf8f1"
GOLD = "#c9a56a"
PALETTE = ["#d98b5a", "#e7aa79", "#c96f6f", "#8fa08a", "#b2b9a1", "#6e7a66", "#d2c5ac"]


def inside_brain(x: float, y: float) -> bool:
    """Brain silhouette in a 0..1 box: two lobes, a cerebellum, a short stem."""
    def ell(cx, cy, rx, ry):
        return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1
    lobes = ell(0.50, 0.46, 0.45, 0.34) or ell(0.33, 0.41, 0.29, 0.29) or ell(0.67, 0.41, 0.29, 0.29)
    cerebellum = ell(0.73, 0.73, 0.17, 0.12)
    stem = 0.52 <= x <= 0.61 and 0.72 <= y <= 0.91
    notch = ell(0.24, 0.78, 0.14, 0.12)  # frontal underside is open
    return (lobes or cerebellum or stem) and not notch


def dots(seed: int = 7, step: float = 0.047) -> list[tuple[float, float, float, str]]:
    """Hex-grid pointillism: (x, y, radius, color) in the unit box."""
    rng = random.Random(seed)
    out = []
    row = 0
    y = 0.08
    while y < 0.92:
        offset = (step / 2) if row % 2 else 0
        x = 0.06 + offset
        while x < 0.96:
            jx, jy = x + rng.uniform(-0.008, 0.008), y + rng.uniform(-0.008, 0.008)
            if inside_brain(jx, jy):
                # Larger, warmer dots in the middle; smaller, cooler dots at the edge.
                depth = 1 - math.hypot(jx - 0.5, jy - 0.5) / 0.5
                radius = step * (0.22 + 0.26 * max(0.0, depth)) * rng.uniform(0.85, 1.15)
                tone = PALETTE[min(len(PALETTE) - 1, int((1 - depth) * 4.5 + rng.random() * 2.2))]
                out.append((jx, jy, radius, tone))
            x += step
        y += step * 0.87
        row += 1
    # The fissure between the hemispheres: thin it out so the brain reads as two halves.
    return [d for d in out if not (0.485 < d[0] < 0.515 and d[1] < 0.66) or random.Random(int(d[1] * 1000)).random() < 0.25]


def svg(size: int, framed: bool = True) -> str:
    pad = 0.0 if not framed else 0.12
    inner = size * (1 - 2 * pad)
    ox = oy = size * pad
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">']
    if framed:
        f = size * 0.055  # frame thickness
        parts.append(f'<rect x="{size*0.02:.1f}" y="{size*0.02:.1f}" width="{size*0.96:.1f}" height="{size*0.96:.1f}" rx="{size*0.06:.1f}" fill="{INK}"/>')
        parts.append(f'<rect x="{size*0.02+f*0.55:.1f}" y="{size*0.02+f*0.55:.1f}" width="{size*0.96-f*1.1:.1f}" height="{size*0.96-f*1.1:.1f}" rx="{size*0.04:.1f}" fill="none" stroke="{GOLD}" stroke-width="{max(1.0, size*0.008):.1f}"/>')
        parts.append(f'<rect x="{size*0.02+f:.1f}" y="{size*0.02+f:.1f}" width="{size*0.96-2*f:.1f}" height="{size*0.96-2*f:.1f}" rx="{size*0.025:.1f}" fill="{MAT}"/>')
        for cx, cy in ((0.055, 0.055), (0.945, 0.055), (0.055, 0.945), (0.945, 0.945)):
            parts.append(f'<circle cx="{size*cx:.1f}" cy="{size*cy:.1f}" r="{size*0.012:.1f}" fill="{GOLD}"/>')
    for x, y, r, tone in dots():
        parts.append(f'<circle cx="{ox + x*inner:.1f}" cy="{oy + y*inner:.1f}" r="{r*inner:.1f}" fill="{tone}"/>')
    parts.append("</svg>")
    return "\n".join(parts)


def png(size: int, framed: bool, path: Path, scale: int = 4) -> None:
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = 0.12 if framed else 0.0
    inner = big * (1 - 2 * pad)
    ox = oy = big * pad
    if framed:
        f = big * 0.055
        draw.rounded_rectangle((big*0.02, big*0.02, big*0.98, big*0.98), radius=big*0.06, fill=INK)
        draw.rounded_rectangle((big*0.02+f*0.55, big*0.02+f*0.55, big*0.98-f*0.55, big*0.98-f*0.55), radius=big*0.04, outline=GOLD, width=max(1, int(big*0.008)))
        draw.rounded_rectangle((big*0.02+f, big*0.02+f, big*0.98-f, big*0.98-f), radius=big*0.025, fill=MAT)
    for x, y, r, tone in dots():
        cx, cy, rr = ox + x*inner, oy + y*inner, r*inner
        draw.ellipse((cx-rr, cy-rr, cx+rr, cy+rr), fill=tone)
    img.resize((size, size), Image.LANCZOS).save(path)


if __name__ == "__main__":
    (ROOT / "brand").mkdir(exist_ok=True)
    (ROOT / "brand" / "logo.svg").write_text(svg(512), encoding="utf-8")
    (ROOT / "brand" / "mark.svg").write_text(svg(64), encoding="utf-8")
    (ROOT / "favicon.svg").write_text(svg(64), encoding="utf-8")
    (ROOT / "extension" / "icons").mkdir(exist_ok=True)
    for s in (16, 32, 48, 128):
        png(s, True, ROOT / "extension" / "icons" / f"icon-{s}.png")
    png(512, True, ROOT / "brand" / "logo-512.png")
    print("brand written:", len(dots()), "dots")
