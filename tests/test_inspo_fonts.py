"""Check that bundled inspiration fonts are usable and carry their licenses."""
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "fonts" / "generated"
LICENSES = ROOT / "fonts" / "licenses"
NAMES = ["Cut Ink", "Tall Stamp", "Sunday Sign", "Kalam Bold", "Cabin Sketch"]

for name in NAMES:
    path = FONTS / f"{name}.ttf"
    assert path.exists() and path.stat().st_size > 10_000
    license_path = LICENSES / (name.replace(" ", "") + ".txt")
    assert license_path.exists() and len(license_path.read_bytes()) > 1_000
    font = TTFont(path)
    cmap = font.getBestCmap()
    assert all(ord(char) in cmap for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!?&")
    if name in NAMES[:3]:
        family = font["name"].getDebugName(1)
        assert family == name, (name, family)

print("PASS five typeable inspiration fonts and source licenses")
