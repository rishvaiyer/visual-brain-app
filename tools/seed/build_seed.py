#!/usr/bin/env python3
"""Build demo-seed/: web-sized pictures plus manifest.json (boards, layout, titles, palettes).

    python3 tools/seed/build_seed.py [--assets-dir DIR]

Pictures are chosen in tools/seed/picks.json and read from a local Freeform library by
default. Each one is re-saved as a plain JPEG, which drops EXIF and GPS metadata. The
server loads the result with seed_sample() in server.py.
"""
import argparse
import hashlib
import json
import math
import random
import shutil
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "demo-seed"
DEFAULT_ASSETS = Path.home() / "Library/Group Containers/group.com.apple.freeform/Boards/Assets"

SERIF = "Georgia, 'Iowan Old Style', 'Times New Roman', serif"
SANS = "Avenir Next, sans-serif"
INK, MUTED = "#24251f", "#626459"
TEXT_Z = 100  # labels sit above every picture, so a picture that drifts over one never hides it

BOARDS = [
    # id, name, picks key, subtitle
    ("board_rooms", "Rooms & Light", "rooms", "Rooms with good light."),
    ("board_pixel", "Pixel Dusk", "pixel", "Small palettes, big horizons."),
    ("board_leather", "Leather & Paper", "leather", "Textures, close up."),
    ("board_objects", "Small Objects", "objects", "Tiny, shiny, a little unserious."),
    ("board_illustrated", "Illustrated", "illustrated", "Drawn, painted, printed."),
]
ROUND_ROBIN = ["Rooms", "Pixel", "Illustration", "Objects", "Texture"]


def load_rgb(path: Path) -> Image.Image:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            im = im.convert("RGBA")
            flat = Image.new("RGB", im.size, (255, 255, 255))
            flat.paste(im, mask=im.getchannel("A"))
            return flat
        return im.convert("RGB")


def clamp_crop(w: int, h: int, lo: float = 0.62, hi: float = 1.65):
    """Crop (fractions) that keeps a picture between a wide and a tall frame, plus the resulting h/w."""
    ratio = h / w
    if ratio > hi:
        keep = hi / ratio
        return {"x": 0, "y": round((1 - keep) / 2, 4), "w": 1, "h": round(keep, 4)}, hi
    if ratio < lo:
        keep = ratio / lo
        return {"x": round((1 - keep) / 2, 4), "y": 0, "w": round(keep, 4), "h": 1}, lo
    return {"x": 0, "y": 0, "w": 1, "h": 1}, ratio


def palette(files, count=5):
    tiles = [load_rgb(f).resize((48, 48)) for f in files]
    sheet = Image.new("RGB", (48 * len(tiles), 48))
    for i, tile in enumerate(tiles):
        sheet.paste(tile, (i * 48, 0))
    q = sheet.quantize(colors=10, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[:30]
    ranked = [tuple(pal[i * 3:i * 3 + 3]) for _, i in sorted(q.getcolors(), reverse=True)]
    lum = lambda c: (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255
    chosen = []
    for c in ranked:
        if not 0.1 < lum(c) < 0.92:
            continue
        if all(sum(abs(a - b) for a, b in zip(c, o)) > 70 for o in chosen):
            chosen.append(c)
    for c in ranked:  # top up if the filters were too strict
        if len(chosen) >= count:
            break
        if c not in chosen:
            chosen.append(c)
    return ["#%02x%02x%02x" % c for c in chosen[:count]]


def text(x, y, w, h, value, size, color=INK, family=SANS, weight="400", rotation=0):
    return {"x": round(x), "y": round(y), "w": w, "h": h, "text": value, "fontFamily": family, "fontSize": size,
            "fontWeight": weight, "textAlign": "left", "color": color, "rotation": rotation}


# ---- free-form layout -------------------------------------------------------------------
# Pictures are scattered like a Freeform board: mixed sizes, slight tilts, soft overlaps,
# clustered by mood. Everything is seeded, so the same picks always lay out the same way.

def make_rects(entries, rng, sizes):
    rects = []
    for n, e in enumerate(entries):
        crop, ratio = clamp_crop(e["w"], e["h"])
        w = sizes[0] if n == 0 else rng.choice(sizes[1:])
        if w * ratio > 430:
            w = round(430 / ratio)
        rects.append({"e": e, "crop": crop, "w": w, "h": round(w * ratio), "x": 0.0, "y": 0.0,
                      "rot": rng.choice([-3.5, -2, -1, 0, 0, 1, 2, 3.5]), "home": (0, 0)})
    return rects


def spiral(rects, cx, cy, spread):
    for i, r in enumerate(rects):
        rad = spread * math.sqrt((i + .6) / len(rects))
        th = i * 2.399963
        r["x"] = cx + rad * math.cos(th) * 1.3 - r["w"] / 2
        r["y"] = cy + rad * math.sin(th) - r["h"] / 2
        r["home"] = (cx, cy)


def relax(rects, iters=420, allow=0.012, pull=0.004, margin=12):
    """Nudge pictures apart until each has breathing room; keep every one drifting back toward its cluster."""
    for _ in range(iters):
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                dx = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]) + margin
                dy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]) + margin
                if dx <= 0 or dy <= 0:
                    continue
                if dx * dy / min(a["w"] * a["h"], b["w"] * b["h"]) <= allow:
                    continue
                if dx < dy:
                    push = (dx + 2) / 2
                    sign = 1 if a["x"] + a["w"] / 2 < b["x"] + b["w"] / 2 else -1
                    a["x"] -= sign * push; b["x"] += sign * push
                else:
                    push = (dy + 2) / 2
                    sign = 1 if a["y"] + a["h"] / 2 < b["y"] + b["h"] / 2 else -1
                    a["y"] -= sign * push; b["y"] += sign * push
        for r in rects:
            r["x"] += (r["home"][0] - (r["x"] + r["w"] / 2)) * pull
            r["y"] += (r["home"][1] - (r["y"] + r["h"] / 2)) * pull


def label_box(it, pad=6):
    """Where a label or palette dot really paints. A text box is wide; its words are not."""
    st = it["state"]
    width = min(st["w"], len(st["text"]) * st["fontSize"] * .5) if it["kind"] == "text" else st["w"]
    return [st["x"] - pad, st["y"] - pad, st["x"] + width + pad, st["y"] + st["h"] + pad]


def clear_labels(groups, rects, reach=400):
    """Slide each group of labels (a title with its note) the shortest way to a spot where none of it sits on a picture.

    Pictures stay where the layout put them. Their boxes are trimmed a little, since a tilted picture does not fill its corners."""
    pics = [(r["x"] + 10, r["y"] + 10, r["x"] + r["w"] - 10, r["y"] + r["h"] - 10) for r in rects]
    offsets = sorted(((dx, dy) for dy in range(-reach, reach + 1, 10) for dx in range(-reach, reach + 1, 10)), key=lambda o: o[0] ** 2 + o[1] ** 2)
    placed = []
    for group in groups:
        boxes = [label_box(it) for it in group]
        for dx, dy in offsets:
            moved = [(a + dx, b + dy, c + dx, d + dy) for a, b, c, d in boxes]
            if not any(m[0] < p[2] and m[2] > p[0] and m[1] < p[3] and m[3] > p[1] for m in moved for p in pics + placed):
                break
        else:
            continue
        for it in group:
            it["state"]["x"] += dx; it["state"]["y"] += dy
        placed += moved

def bounds(rects):
    return (min(r["x"] for r in rects), min(r["y"] for r in rects),
            max(r["x"] + r["w"] for r in rects), max(r["y"] + r["h"] for r in rects))


def image_items(board_id, rects, z0=20):
    items = []
    for n, r in enumerate(sorted(rects, key=lambda r: -r["w"] * r["h"])):  # big first, small on top
        items.append({"board": board_id, "kind": "image", "asset": r["e"]["key"], "state": {
            "x": round(r["x"]), "y": round(r["y"]), "w": r["w"], "h": r["h"], "rotation": r["rot"], "z": z0 + n, "crop": r["crop"]}})
    return items


def blob(board_id, rects, fill, z):
    x0, y0, x1, y1 = bounds(rects)
    w, h = (x1 - x0) * 1.1, (y1 - y0) * 1.16
    return {"board": board_id, "kind": "shape", "state": {
        "x": round((x0 + x1) / 2 - w / 2), "y": round((y0 + y1) / 2 - h / 2), "w": round(w), "h": round(h),
        "shape": "ellipse", "fill": fill, "stroke": fill, "strokeWidth": 1, "rotation": 0, "z": z}}


def swatches(board_id, colors, x0, y0, z):
    out = []
    for i, color in enumerate(colors):
        out.append({"board": board_id, "kind": "shape", "state": {
            "x": round(x0 + i * 40), "y": round(y0), "w": 28, "h": 28, "shape": "ellipse", "fill": color, "stroke": color,
            "strokeWidth": 1, "rotation": 0, "z": z + i}})
    out.append({"board": board_id, "kind": "text", "state": {**text(x0 + len(colors) * 40 + 6, y0 + 4, 300, 24, "palette, from these pictures", 13, MUTED), "z": TEXT_Z + z + 9}})
    return out


CLUSTERS = {  # collection -> (label, note, home on the Everything board, blob colour)
    "Rooms": ("rooms", "with good light", (470, 430), "#eadfc9"),
    "Pixel": ("pixels", "eight colours, big horizons", (1190, 290), "#dde3ec"),
    "Illustration": ("drawn", "painted, printed, imagined", (1830, 440), "#e4e8d6"),
    "Objects": ("small things", "tiny, shiny, a little unserious", (690, 1060), "#f2dcd0"),
    "Texture": ("textures", "close up", (1480, 1070), "#ece4d0"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets-dir", type=Path, default=DEFAULT_ASSETS)
    args = ap.parse_args()
    spec = json.loads((Path(__file__).with_name("picks.json")).read_text(encoding="utf-8"))
    credit = spec["credit"]

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "images").mkdir(parents=True)

    entries, seen = [], set()
    for p in spec["picks"]:
        src = next(iter(args.assets_dir.glob(p["freeform_asset"] + ".*")), None)
        if not src:
            raise SystemExit(f"missing source picture for {p['key']}: {p['freeform_asset']}")
        im = load_rgb(src)
        im.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
        rel = f"images/{p['key']}.jpg"
        im.save(OUT / rel, "JPEG", quality=86, optimize=True, progressive=True)
        digest = hashlib.sha256((OUT / rel).read_bytes()).hexdigest()
        if digest in seen:
            raise SystemExit(f"duplicate picture: {p['key']}")
        seen.add(digest)
        entries.append({**p, "file": rel, "w": im.width, "h": im.height})
    print(f"{len(entries)} pictures written")

    by_collection = {c: [e for e in entries if e["collection"] == c] for c in CLUSTERS}
    interleaved = []
    for i in range(max(len(v) for v in by_collection.values())):
        for c in CLUSTERS:
            if i < len(by_collection[c]):
                interleaved.append(by_collection[c][i])

    manifest = {"version": 1, "credit": credit,
                "folders": [{"id": "folder_moodboards", "name": "Moodboards"}],
                "boards": [{"id": "main", "name": "Everything", "view": {"x": 0, "y": 0, "zoom": 1, "autofit": "width"}}],
                "assets": [], "items": []}
    for board_id, name, _, _ in BOARDS:
        manifest["boards"].append({"id": board_id, "name": name, "folder": "folder_moodboards",
                                   "view": {"x": 0, "y": 0, "zoom": 1, "autofit": True}})
    for e in interleaved:
        manifest["assets"].append({"key": e["key"], "file": e["file"], "title": e["title"], "alt": e["alt"],
                                   "tags": e["tags"], "collection": e["collection"], "favorite": e["favorite"],
                                   "source_url": "", "credit": credit})

    # Everything: five loose clusters on soft colour, labelled by hand, title top left.
    rng = random.Random(11)
    clusters = {}
    for collection, (label, note, home, color) in CLUSTERS.items():
        rects = make_rects(by_collection[collection], rng, [300, 190, 215, 240, 265])
        spiral(rects, home[0], home[1], 100 + 30 * math.sqrt(len(rects)))
        clusters[collection] = rects
    everything = [r for rs in clusters.values() for r in rs]
    relax(everything, iters=500, pull=0.02)
    x0, y0, _, _ = bounds(everything)
    items = [{"board": "main", "kind": "text", "state": {**text(x0, y0 - 250, 1000, 120, "Collected", 96, INK, SERIF, "500"), "z": TEXT_Z + 2}},
             {"board": "main", "kind": "text", "state": {**text(x0 + 6, y0 - 128, 1000, 36, f"{len(entries)} pictures from everywhere. Drag, zoom, or search above.", 22, MUTED), "z": TEXT_Z + 3}}]
    for n, (collection, (label, note, home, color)) in enumerate(CLUSTERS.items()):
        rects = clusters[collection]
        bx0, by0, bx1, by1 = bounds(rects)
        items.append(blob("main", rects, color, 4 + n))
        items.append({"board": "main", "kind": "text", "state": {**text(bx0 - 6, by0 - 118, 620, 76, label, 60, INK, SERIF, "500", -2), "z": TEXT_Z + 12 + n}})
        items.append({"board": "main", "kind": "text", "state": {**text(bx0 - 2, by0 - 44, 620, 32, note, 22, MUTED, SERIF, "400", -2), "z": TEXT_Z + 12 + n}})
    clear_labels([items[:2]] + [items[2 + 3 * n + 1:2 + 3 * n + 3] for n in range(len(CLUSTERS))], everything)
    manifest["items"] += items + image_items("main", everything)

    # Each themed board: one loose pile, title and a palette pulled from the pictures.
    for n, (board_id, name, key, subtitle) in enumerate(BOARDS):
        group = [e for e in entries if e["board"] == key]
        brng = random.Random(40 + n)
        rects = make_rects(group, brng, [360, 230, 260, 290, 320])
        spiral(rects, 900, 640, 210 + 34 * math.sqrt(len(rects)))
        relax(rects, iters=500, pull=0.012)
        bx0, by0, bx1, by1 = bounds(rects)
        labels = [
            {"board": board_id, "kind": "text", "state": {**text(bx0, by0 - 200, 1100, 84, name, 64, INK, SERIF, "500"), "z": TEXT_Z + 2}},
            {"board": board_id, "kind": "text", "state": {**text(bx0 + 4, by0 - 116, 900, 34, subtitle, 20, MUTED), "z": TEXT_Z + 3}}]
        labels += swatches(board_id, palette([OUT / e["file"] for e in group]), bx0 + 4, by0 - 66, 4)
        clear_labels([labels], rects)
        manifest["items"] += labels + image_items(board_id, rects)

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "NOTICE.md").write_text(
        "# Sample pictures\n\n"
        f"{credit}\n\n"
        "These pictures illustrate the sample boards that load into an empty library and the public playground. "
        "They were not made by this project, and their creators are not known. Replace them with pictures you "
        "have the right to share before distributing a copy of this folder more widely.\n\n"
        "Regenerate with `python3 tools/seed/build_seed.py` (see tools/seed/picks.json).\n", encoding="utf-8")
    total = sum(f.stat().st_size for f in (OUT / "images").iterdir())
    print(f"manifest: {len(manifest['boards'])} boards, {len(manifest['assets'])} assets, {len(manifest['items'])} items; images {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
