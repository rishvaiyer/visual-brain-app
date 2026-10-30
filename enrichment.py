"""Background, local-first image suggestions. Capture never waits for this module."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("VISUAL_BRAIN_DATA_DIR", str(ROOT / "data"))).resolve()
DB = DATA / "visual-brain.sqlite3"
ASSETS = DATA / "assets"
WAKE = threading.Event()
WORKER_STARTED = False
PROVIDER_URL = os.environ.get("VISUAL_BRAIN_DECISION_URL", "").rstrip("/")
PROVIDER_MODEL = os.environ.get("VISUAL_BRAIN_DECISION_MODEL", "local-decision")
PROVIDER_KEY = os.environ.get("VISUAL_BRAIN_DECISION_KEY", "")


def init_db(db):
    db.execute("""CREATE TABLE IF NOT EXISTS asset_enrichment (
        asset_id TEXT PRIMARY KEY, status TEXT NOT NULL, suggestions TEXT NOT NULL DEFAULT '[]',
        labels TEXT NOT NULL DEFAULT '[]', dhash TEXT, provider TEXT NOT NULL DEFAULT 'local',
        error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE CASCADE
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS enrichment_status ON asset_enrichment(status)")
    db.commit()


def queue(db, asset_id):
    db.execute("INSERT OR IGNORE INTO asset_enrichment(asset_id,status) VALUES(?, 'pending')", (asset_id,))


def wait_for_vision(db, asset_id):
    db.execute("""INSERT INTO asset_enrichment(asset_id,status,suggestions,labels,error) VALUES(?,'waiting','[]','[]','')
                  ON CONFLICT(asset_id) DO UPDATE SET status='waiting',suggestions='[]',labels='[]',error='',updated_at=CURRENT_TIMESTAMP""", (asset_id,))


def reset(db, asset_id):
    db.execute("""INSERT INTO asset_enrichment(asset_id,status,suggestions,labels,error)
                  VALUES(?,'pending','[]','[]','') ON CONFLICT(asset_id) DO UPDATE SET
                  status='pending', suggestions='[]', labels='[]', error='', updated_at=CURRENT_TIMESTAMP""", (asset_id,))
    db.commit()
    WAKE.set()


def _dhash(path):
    with Image.open(path) as source:
        pixels = list(source.convert("L").resize((9, 8)).getdata())
    value = 0
    for y in range(8):
        for x in range(8):
            value = (value << 1) | int(pixels[y * 9 + x] > pixels[y * 9 + x + 1])
    return f"{value:016x}"


def _clean_label(value):
    value = re.sub(r"[^\w\s-]", " ", value.lower()).replace("_", " ")
    return " ".join(value.split())[:40]


def _candidates(asset, labels):
    # Vision supplies visual evidence; source metadata is weaker and marked separately.
    found = {}
    for item in labels:
        confidence = float(item.get("confidence", 0))
        if confidence < 0.12:
            continue
        label = _clean_label(str(item.get("label", "")))
        if label and label not in {"image", "photo", "photograph", "art", "design"}:
            found[label] = {"tag": label, "score": round(confidence, 3), "source": "image"}
    words = re.findall(r"[a-zA-Z][a-zA-Z-]{2,}", " ".join((asset["alt"] or "", asset["title"] or "", (asset["auto_text"] or "")[:500])))
    stop = {"image", "photo", "picture", "saved", "from", "with", "the", "and", "this", "that", "jpg", "png", "webp", "jpeg"}
    for word in words[:24]:
        tag = _clean_label(word)
        if tag not in stop and tag not in found and len(tag) > 2:
            found[tag] = {"tag": tag, "score": 0.3, "source": "page text"}
    return sorted(found.values(), key=lambda value: value["score"], reverse=True)[:12]


def _local_match(candidates, options):
    tags = {word for c in candidates for word in c["tag"].split() if len(word) > 3 and c["score"] >= 0.3}
    matches = [identifier for identifier, name in options if any(word in name.lower().split() for word in tags)]
    return matches[0] if len(matches) == 1 else None


def _decide(asset, candidates, boards, collections):
    """Optional System One provider; only text metadata leaves this process."""
    if not PROVIDER_URL:
        return (candidates, _local_match(candidates, [(b["id"], b["name"]) for b in boards if b["id"] != "main"]),
                _local_match(candidates, [(name, name) for name in collections]), "local")
    if not (PROVIDER_URL.startswith("http://127.0.0.1:") or PROVIDER_URL.startswith("http://localhost:")):
        raise ValueError("Decision URL must be a loopback HTTP service")
    names = [b for b in boards if b["id"] != "main"][:15]
    questions = {f"tag_{i}": {"type": "noul", "instructions": f"Does the image evidence support tag '{c['tag']}'?"}
                 for i, c in enumerate(candidates)}
    if names:
        questions["board"] = {"type": "choice", "instructions": "Which board best fits this image? Choose none if unclear.",
                              "criteria": {**{b["id"]: b["name"] for b in names}, "none": "No clear fit"}}
    if collections:
        questions["collection"] = {"type": "choice", "instructions": "Which existing collection best fits this image? Choose none if unclear.",
                                   "criteria": {**{name: name for name in collections[:15]}, "none": "No clear fit"}}
    if not questions:
        return candidates, None, None, "local"
    headers = {"Authorization": f"Bearer {PROVIDER_KEY}"} if PROVIDER_KEY else {}
    response = requests.post(PROVIDER_URL + "/v1/systemone", json={"model": PROVIDER_MODEL,
        "state": {"visual_labels": candidates, "alt": asset["alt"], "title": asset["title"],
                  "source_host": (asset["source_url"] or "").split("/")[2:3]},
        "questions": questions}, headers=headers, timeout=2)
    response.raise_for_status()
    answers = response.json().get("answers", {})
    reviewed = []
    for i, candidate in enumerate(candidates):
        probability = answers.get(f"tag_{i}", {}).get("noul")
        if isinstance(probability, (int, float)) and probability >= 0.7:
            reviewed.append({**candidate, "score": round(float(probability), 3), "source": candidate["source"] + " + decision"})
    board = answers.get("board", {})
    suggestion = board.get("choice") if board.get("confidence", 0) >= 0.55 else None
    if suggestion not in {b["id"] for b in names}:
        suggestion = None
    collection_answer = answers.get("collection", {})
    collection = collection_answer.get("choice") if collection_answer.get("confidence", 0) >= 0.55 else None
    if collection not in collections:
        collection = None
    return reviewed, suggestion, collection, PROVIDER_MODEL


def _process(db, row):
    asset = db.execute("SELECT * FROM assets WHERE id=? AND trashed_at IS NULL", (row["asset_id"],)).fetchone()
    if not asset:
        db.execute("DELETE FROM asset_enrichment WHERE asset_id=?", (row["asset_id"],))
        db.commit()
        return
    path = ASSETS / asset["filename"]
    labels = [{"label": label, "confidence": 0.5} for label in json.loads(asset["auto_tags"] or "[]")]
    candidates = _candidates(asset, labels)
    boards = db.execute("SELECT id,name FROM boards").fetchall()
    collections = [r[0] for r in db.execute("SELECT DISTINCT collection FROM assets WHERE collection!='' ORDER BY collection COLLATE NOCASE")]
    provider_note = "local"
    board = _local_match(candidates, [(b["id"], b["name"]) for b in boards if b["id"] != "main"])
    collection = _local_match(candidates, [(name, name) for name in collections])
    try:
        candidates, board, collection, provider_note = _decide(asset, candidates, boards, collections)
    except (requests.RequestException, ValueError, KeyError):
        # The optional decision service is never a requirement for saving or local suggestions.
        pass
    suggestions = {"tags": candidates, "board_id": board, "collection": collection}
    db.execute("""UPDATE asset_enrichment SET status='ready',suggestions=?,labels=?,dhash=?,provider=?,
                  error='',updated_at=CURRENT_TIMESTAMP WHERE asset_id=?""",
               (json.dumps(suggestions), json.dumps(labels), _dhash(path), provider_note, row["asset_id"]))
    db.commit()


def _worker():
    while True:
        WAKE.wait(timeout=30)
        WAKE.clear()
        with sqlite3.connect(DB) as db:
            db.row_factory = sqlite3.Row
            init_db(db)
            while row := db.execute("SELECT asset_id FROM asset_enrichment WHERE status='pending' LIMIT 1").fetchone():
                db.execute("UPDATE asset_enrichment SET status='running' WHERE asset_id=?", (row["asset_id"],))
                db.commit()
                try:
                    _process(db, row)
                except Exception as error:
                    db.execute("UPDATE asset_enrichment SET status='error',error=?,updated_at=CURRENT_TIMESTAMP WHERE asset_id=?",
                               (str(error)[:240], row["asset_id"]))
                    db.commit()


def start():
    global WORKER_STARTED
    if WORKER_STARTED:
        return
    WORKER_STARTED = True
    with sqlite3.connect(DB) as db:
        init_db(db)
        db.execute("UPDATE asset_enrichment SET status='pending' WHERE status='running'")
        db.commit()
    threading.Thread(target=_worker, name="image-enrichment", daemon=True).start()
    WAKE.set()
