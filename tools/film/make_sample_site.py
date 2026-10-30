#!/usr/bin/env python3
"""Build a small fake inspiration website for the demo film: eight poster images and one page.

Usage: python3 make_sample_site.py <output dir> <repo root>
Only the bundled open fonts in <repo root>/fonts/generated are used.
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

out = Path(sys.argv[1]); root = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
fonts = sorted((root / "fonts" / "generated").glob("*.ttf"))
posters = [
    ("Slow mornings", "#e9d7b8", "#24251f", "circle"),
    ("Studio notes", "#8fa08a", "#f6f1e7", "stripe"),
    ("Terracotta", "#d98b5a", "#2a1d14", "arc"),
    ("Quiet type", "#f6f1e7", "#24251f", "grid"),
    ("Sunday sign", "#c96f6f", "#fff3ea", "circle"),
    ("Paper & ink", "#24251f", "#f6f1e7", "stripe"),
    ("Sage garden", "#b2b9a1", "#2d3a2e", "arc"),
    ("Dot matrix", "#d2c5ac", "#4a3a1a", "dots"),
]
for i, (title, bg, fg, motif) in enumerate(posters):
    w, h = 1200, 900
    im = Image.new("RGB", (w, h), bg); d = ImageDraw.Draw(im)
    if motif == "circle": d.ellipse((700, 120, 1120, 540), fill=fg)
    if motif == "stripe":
        for k in range(6): d.rectangle((760 + k * 60, 80, 790 + k * 60, 820), fill=fg)
    if motif == "arc": d.pieslice((620, 60, 1180, 620), 180, 360, fill=fg)
    if motif == "grid":
        for a in range(5):
            for b in range(5): d.rectangle((700 + a * 90, 120 + b * 90, 740 + a * 90, 160 + b * 90), outline=fg, width=4)
    if motif == "dots":
        for a in range(9):
            for b in range(7): d.ellipse((680 + a * 55, 110 + b * 55, 710 + a * 55, 140 + b * 55), fill=fg)
    font = ImageFont.truetype(str(fonts[i % len(fonts)]), 120) if fonts else ImageFont.load_default()
    d.text((80, 560), title, fill=fg, font=font)
    d.text((80, 760), f"№ {i + 1:02d} · open type study", fill=fg, font=ImageFont.truetype(str(fonts[0]), 40) if fonts else None)
    im.save(out / f"poster-{i + 1}.jpg", quality=88)
cards = "\n".join(
    f'<article><img src="poster-{i + 1}.jpg" alt="{t}" width="1200" height="900"><h3>{t}</h3><p>Open type study · № {i + 1:02d}</p></article>'
    for i, (t, *_rest) in enumerate(posters))
(out / "index.html").write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Type & Colour · a daily inspiration feed</title>
<style>body{{margin:0;font:16px/1.5 -apple-system,system-ui,sans-serif;background:#fff;color:#1f1f1c}}header{{padding:28px 40px;border-bottom:1px solid #eee;display:flex;justify-content:space-between;align-items:center}}
header b{{font-size:20px;letter-spacing:-.02em}}header nav a{{margin-left:22px;color:#666;text-decoration:none}}main{{padding:32px 40px;display:grid;grid-template-columns:repeat(3,1fr);gap:28px}}
article img{{width:100%;height:auto;display:block;border-radius:10px}}h3{{margin:12px 0 2px;font-size:17px}}p{{margin:0;color:#777;font-size:14px}}</style></head>
<body><header><b>Type &amp; Colour</b><nav><a>Posters</a><a>Palettes</a><a>Studios</a><a>About</a></nav></header><main>{cards}</main></body></html>""", encoding="utf-8")
print("sample site:", out)
