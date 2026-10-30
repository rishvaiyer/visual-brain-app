#!/usr/bin/env python3
"""On-demand, offline CLIP search over Visual Brain's own active images."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import mlx.core as mx
import numpy as np
from PIL import Image, ImageOps

from vendor.mlx_clip.clip import load


ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("VISUAL_BRAIN_DATA_DIR", ROOT / "data"))
MODEL = DATA / "clip-model"
INDEX = DATA / "clip-index.sqlite3"


def search(query: str, images: list[dict], limit: int = 12) -> list[dict]:
    if not query.strip() or not images:
        return []
    model, tokenizer, processor = load(str(MODEL))
    db = sqlite3.connect(INDEX)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS vectors (id TEXT PRIMARY KEY, filename TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, value BLOB NOT NULL)")
        vectors = []
        for item in images:
            path = Path(item["path"])
            stat = path.stat()
            cached = db.execute("SELECT value FROM vectors WHERE id=? AND filename=? AND size=? AND mtime_ns=?",
                                (item["id"], str(path), stat.st_size, stat.st_mtime_ns)).fetchone()
            if cached:
                vector = np.frombuffer(cached[0], dtype=np.float32).copy()
            else:
                with Image.open(path) as image:
                    rgb = ImageOps.exif_transpose(image).convert("RGB")
                    result = model(pixel_values=processor([rgb]))
                    mx.eval(result.image_embeds)
                    vector = np.asarray(result.image_embeds[0], dtype=np.float32)
                db.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?,?)",
                           (item["id"], str(path), stat.st_size, stat.st_mtime_ns, vector.tobytes()))
            vectors.append((item["id"], vector))
        db.commit()
        result = model(input_ids=tokenizer([query[:120]]))
        mx.eval(result.text_embeds)
        text_vector = np.asarray(result.text_embeds[0], dtype=np.float32)
        ranked = sorted(((image_id, float(np.dot(vector, text_vector))) for image_id, vector in vectors),
                        key=lambda pair: pair[1], reverse=True)
        return [{"id": image_id, "score": round(score, 4)} for image_id, score in ranked[:limit]]
    finally:
        db.close()


def similar(image_id: str, images: list[dict], limit: int = 12) -> list[dict]:
    if not image_id or not images:
        return []
    model, _, processor = load(str(MODEL))
    db = sqlite3.connect(INDEX)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS vectors (id TEXT PRIMARY KEY, filename TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, value BLOB NOT NULL)")
        vectors = []
        for item in images:
            path = Path(item["path"])
            stat = path.stat()
            cached = db.execute("SELECT value FROM vectors WHERE id=? AND filename=? AND size=? AND mtime_ns=?",
                                (item["id"], str(path), stat.st_size, stat.st_mtime_ns)).fetchone()
            if cached:
                vector = np.frombuffer(cached[0], dtype=np.float32).copy()
            else:
                with Image.open(path) as image:
                    rgb = ImageOps.exif_transpose(image).convert("RGB")
                    result = model(pixel_values=processor([rgb]))
                    mx.eval(result.image_embeds)
                    vector = np.asarray(result.image_embeds[0], dtype=np.float32)
                db.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?,?)",
                           (item["id"], str(path), stat.st_size, stat.st_mtime_ns, vector.tobytes()))
            vectors.append((item["id"], vector))
        db.commit()
        reference = next((vector for identifier, vector in vectors if identifier == image_id), None)
        if reference is None:
            return []
        ranked = sorted(((identifier, float(np.dot(vector, reference))) for identifier, vector in vectors
                         if identifier != image_id), key=lambda pair: pair[1], reverse=True)
        return [{"id": identifier, "score": round(score, 4)} for identifier, score in ranked[:limit]]
    finally:
        db.close()


if __name__ == "__main__":
    request = json.load(sys.stdin)
    image_id = str(request.get("image_id", ""))
    if image_id:
        matches = similar(image_id, request.get("images", []))
    else:
        matches = search(str(request.get("query", "")), request.get("images", []))
    print(json.dumps({"matches": matches}))
