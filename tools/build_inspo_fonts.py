"""Build clearly named inspiration fonts from redistributable open fonts.

Run with: python3 tools/build_inspo_fonts.py
Requires fonttools and requests. Source files are pinned to one Google Fonts commit.
"""

from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path

import requests
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables.ttProgram import Program

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fonts" / "generated"
LICENSES = ROOT / "fonts" / "licenses"
GOOGLE_FONTS_COMMIT = "b5efa9c32e8f9b63005f5cdb1ad5527a77d2cd04"
RAW = f"https://raw.githubusercontent.com/google/fonts/{GOOGLE_FONTS_COMMIT}/"

SOURCES = [
    ("apache/permanentmarker/PermanentMarker-Regular.ttf", "apache/permanentmarker/LICENSE.txt",
     "Cut Ink", "CutInk", "Cut Ink.ttf", .88, 1.06, -.055, 2.6),
    ("ofl/patrickhandsc/PatrickHandSC-Regular.ttf", "ofl/patrickhandsc/OFL.txt",
     "Tall Stamp", "TallStamp", "Tall Stamp.ttf", .81, 1.08, .018, 1.8),
    ("ofl/balsamiqsans/BalsamiqSans-Regular.ttf", "ofl/balsamiqsans/OFL.txt",
     "Sunday Sign", "SundaySign", "Sunday Sign.ttf", 1.01, .98, -.035, 1.4),
]
ALTERNATIVES = [
    ("ofl/kalam/Kalam-Bold.ttf", "ofl/kalam/OFL.txt", "Kalam Bold.ttf"),
    ("ofl/cabinsketch/CabinSketch-Regular.ttf", "ofl/cabinsketch/OFL.txt", "Cabin Sketch.ttf"),
]


def download(path: str) -> bytes:
    response = requests.get(RAW + path, timeout=30)
    response.raise_for_status()
    return response.content


def name_font(font: TTFont, family: str, postscript: str) -> None:
    names = font["name"]
    names.setName(family, 1, 3, 1, 0x409)
    names.setName("Regular", 2, 3, 1, 0x409)
    names.setName(family + " Regular", 4, 3, 1, 0x409)
    names.setName(postscript + "-Regular", 6, 3, 1, 0x409)
    names.setName(family, 16, 3, 1, 0x409)
    names.setName("Regular", 17, 3, 1, 0x409)
    # Keep all original copyright and license records intact.
    for record in list(names.names):
        if record.platformID == 3 and record.nameID in (1, 2, 4, 6, 16, 17):
            continue
        if record.nameID in (1, 2, 4, 6, 16, 17):
            names.names.remove(record)
    font["head"].fontRevision = 1.0


def make_variant(source: bytes, family: str, postscript: str, sx: float, sy: float,
                 skew: float, rotation_degrees: float) -> bytes:
    font = TTFont(io.BytesIO(source))
    glyf = font["glyf"]
    metrics = font["hmtx"].metrics
    for glyph_name in font.getGlyphOrder():
        glyph = glyf[glyph_name]
        if glyph.isComposite():
            for component in glyph.components:
                if hasattr(component, "x"):
                    component.x = round(component.x * sx)
                if hasattr(component, "y"):
                    component.y = round(component.y * sy)
        elif glyph.numberOfContours > 0:
            digest = hashlib.sha256(glyph_name.encode()).digest()
            angle = math.radians(rotation_degrees * ((digest[0] / 255) * 2 - 1))
            ca, sa = math.cos(angle), math.sin(angle)
            advance, _ = metrics[glyph_name]
            center_x = advance / 2
            for index, (x, y) in enumerate(glyph.coordinates):
                x0, y0 = x - center_x, y - 350
                x1 = sx * (ca * x0 - sa * y0) + skew * y0 + sx * center_x
                y1 = sy * (sa * x0 + ca * y0) + sy * 350
                glyph.coordinates[index] = (round(x1), round(y1))
            glyph.program = Program()
        advance, bearing = metrics[glyph_name]
        metrics[glyph_name] = (round(advance * sx), round(bearing * sx))
    font["hhea"].advanceWidthMax = max(a for a, _ in metrics.values())
    font["OS/2"].xAvgCharWidth = round(font["OS/2"].xAvgCharWidth * sx)
    name_font(font, family, postscript)
    font.recalcBBoxes = True
    font.recalcTimestamp = False
    output = io.BytesIO()
    font.save(output)
    return output.getvalue()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    LICENSES.mkdir(parents=True, exist_ok=True)
    for path, license_path, family, postscript, filename, sx, sy, skew, rotation in SOURCES:
        source = download(path)
        (LICENSES / (postscript + ".txt")).write_bytes(download(license_path))
        result = make_variant(source, family, postscript, sx, sy, skew, rotation)
        (OUT / filename).write_bytes(result)
        print(f"Built {filename} ({len(result):,} bytes)")
    for path, license_path, filename in ALTERNATIVES:
        (OUT / filename).write_bytes(download(path))
        (LICENSES / (Path(filename).stem.replace(" ", "") + ".txt")).write_bytes(download(license_path))
        print(f"Added {filename}")


if __name__ == "__main__":
    main()
