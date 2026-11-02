"""Small, offline image features for visual discovery and camera lookup.

This deliberately needs only Pillow. It compares appearance, not object identity;
the UI must describe weak results as visual neighbors rather than a known match.
"""

from __future__ import annotations

import colorsys
import math
from collections import Counter
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps


def _features(image: Image.Image) -> dict:
    image = ImageOps.exif_transpose(image).convert("RGB")
    color = image.resize((32, 32), Image.Resampling.BILINEAR)
    pixels = list(color.getdata())
    histogram = [0.0] * 40  # 12 hue bins split by brightness, plus four grayscale bins.
    mean = [0.0, 0.0, 0.0]
    for red, green, blue in pixels:
        mean[0] += red
        mean[1] += green
        mean[2] += blue
        hue, saturation, value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        if saturation < .13:
            bucket = 36 + min(3, int(value * 4))
        else:
            bucket = min(11, int(hue * 12)) * 3 + min(2, int(value * 3))
        histogram[bucket] += 1
    mean = [round(channel / len(pixels)) for channel in mean]
    histogram = [v / len(pixels) for v in histogram]

    gray = image.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
    values = list(gray.getdata())
    dhash = 0
    for y in range(8):
        for x in range(8):
            dhash = (dhash << 1) | int(values[y * 9 + x] > values[y * 9 + x + 1])
    return {"hist": histogram, "hash": dhash, "rgb": mean}


@lru_cache(maxsize=1200)
def from_file(filename: str, size: int, mtime_ns: int) -> dict:
    """Cache by file identity; changed or replaced images get fresh features."""
    with Image.open(filename) as image:
        return _features(image)


def file_features(path: Path) -> dict:
    stat = path.stat()
    return from_file(str(path), stat.st_size, stat.st_mtime_ns)


def byte_features(raw: bytes) -> dict:
    with Image.open(BytesIO(raw)) as image:
        if image.width * image.height > 40_000_000:
            raise ValueError("Camera image is too large")
        return _features(image)


def similarity(left: dict, right: dict) -> float:
    color = sum(min(a, b) for a, b in zip(left["hist"], right["hist"]))
    shape = 1 - ((left["hash"] ^ right["hash"]).bit_count() / 64)
    mean = 1 - sum(abs(a - b) for a, b in zip(left["rgb"], right["rgb"])) / 765
    return round(max(0, min(1, .40 * color + .45 * shape + .15 * mean)), 4)


def rank(query: dict, images: list[dict], limit: int = 12) -> list[dict]:
    found = []
    for image in images:
        try:
            feature = file_features(Path(image["path"]))
        except (OSError, ValueError):
            continue
        found.append({"id": image["id"], "similarity": similarity(query, feature)})
    return sorted(found, key=lambda item: (-item["similarity"], item["id"]))[:limit]


def _color_name(rgb: list[int]) -> str:
    hue, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in rgb))
    if value < .22:
        return "Dark tones"
    if saturation < .12:
        return "Light neutrals" if value > .72 else "Soft neutrals"
    names = ["Red", "Orange", "Gold", "Yellow", "Green", "Teal", "Blue", "Indigo", "Purple", "Pink", "Red", "Red"]
    return names[min(11, int(hue * 12))] + (" tones" if saturation < .5 else " colors")


def taste_groups(images: list[dict], max_groups: int = 8) -> list[dict]:
    """Deterministic medoid groups, sized for a personal library rather than ML scale."""
    rows = []
    for image in images:
        try:
            rows.append((image, file_features(Path(image["path"]))))
        except (OSError, ValueError):
            continue
    if not rows:
        return []
    count = min(max_groups, max(1, round(math.sqrt(len(rows) / 3))))
    # Spread seeds apart; then assign each image to the closest real exemplar.
    seeds = [0]
    while len(seeds) < count:
        next_index = max((i for i in range(len(rows)) if i not in seeds),
                         key=lambda i: min(1 - similarity(rows[i][1], rows[s][1]) for s in seeds))
        seeds.append(next_index)
    groups = [[] for _ in seeds]
    for image, feature in rows:
        nearest = max(range(len(seeds)), key=lambda k: similarity(feature, rows[seeds[k]][1]))
        groups[nearest].append((image, feature))
    result = []
    for group in groups:
        if not group:
            continue
        tags = Counter(tag.strip() for image, _ in group for tag in
                       ([image.get("collection", "")] + image.get("tags", [])) if tag.strip())
        mean = [round(sum(feature["rgb"][channel] for _, feature in group) / len(group)) for channel in range(3)]
        title = tags.most_common(1)[0][0] if tags else _color_name(mean)
        result.append({"title": title[:60], "color": "#" + "".join(f"{channel:02x}" for channel in mean),
                       "ids": [image["id"] for image, _ in group], "count": len(group)})
    return sorted(result, key=lambda group: (-group["count"], group["title"]))
