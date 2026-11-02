#!/usr/bin/env python3
"""Local-only asset and board server for Visual Brain."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import math
import mimetypes
import os
import secrets
import sqlite3
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

import enrichment
import requests
from PIL import Image, ImageOps

import hosting
import visual_features
import free_3d
import outbound_3d

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("VISUAL_BRAIN_DATA_DIR", str(ROOT / "data"))).resolve()
ASSETS = DATA / "assets"
FONTS = DATA / "fonts"
MODELS = DATA / "models"
DB = DATA / "visual-brain.sqlite3"
KEY_FILE = DATA / "pairing-key.txt"
# Loopback by default; hosted deployments use the configured bind address.
HOST = os.environ.get("VISUAL_BRAIN_HOST", "127.0.0.1")
# PORT is what Railway and most platforms set; VISUAL_BRAIN_PORT wins locally.
PORT = int(os.environ.get("VISUAL_BRAIN_PORT") or os.environ.get("PORT") or "8777")
MAX_IMAGE = 25 * 1024 * 1024
MAX_FONT = 8 * 1024 * 1024
MAX_BODY = 36 * 1024 * 1024
# The public demo is shared by strangers, so it takes smaller files and fewer of them.
DEMO_MAX_IMAGE = 8 * 1024 * 1024
DEMO_MAX_ASSETS = 400
DEMO_RESET_HOURS = float(os.environ.get("VISUAL_BRAIN_DEMO_RESET_HOURS", "6") or 6)
IMAGE_MIMES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "image/avif": "avif"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def setup() -> str:
    ASSETS.mkdir(parents=True, exist_ok=True)
    FONTS.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    if not KEY_FILE.exists():
        KEY_FILE.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        KEY_FILE.chmod(0o600)
    with sqlite3.connect(DB) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS assets (
              id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, mime TEXT NOT NULL,
              filename TEXT NOT NULL, source_url TEXT, image_url TEXT, alt TEXT,
              title TEXT, tags TEXT NOT NULL DEFAULT '[]', collection TEXT NOT NULL DEFAULT '',
              favorite INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS boards (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, view TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS items (
              id TEXT PRIMARY KEY, board_id TEXT NOT NULL, kind TEXT NOT NULL,
              asset_id TEXT, state TEXT NOT NULL, updated_at TEXT NOT NULL,
              FOREIGN KEY(board_id) REFERENCES boards(id)
            );
            CREATE TABLE IF NOT EXISTS folders (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fonts (
              id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
              filename TEXT NOT NULL, mime TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS models (
              id TEXT PRIMARY KEY, filename TEXT NOT NULL, source_asset_id TEXT,
              status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              source_crop TEXT NOT NULL DEFAULT '{}',
              source_asset_ids TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS critique_invites (
              board_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS critique_comments (
              id TEXT PRIMARY KEY, board_id TEXT NOT NULL, item_id TEXT NOT NULL,
              x REAL NOT NULL, y REAL NOT NULL, author TEXT NOT NULL,
              body TEXT NOT NULL, created_at TEXT NOT NULL,
              resolved_at TEXT
            );
        """)
        existing_columns = {r[1] for r in db.execute("PRAGMA table_info(assets)")}
        if "pixel_width" not in existing_columns:
            db.execute("ALTER TABLE assets ADD COLUMN pixel_width INTEGER")
        if "pixel_height" not in existing_columns:
            db.execute("ALTER TABLE assets ADD COLUMN pixel_height INTEGER")
        if "trashed_at" not in existing_columns:
            db.execute("ALTER TABLE assets ADD COLUMN trashed_at TEXT")
        if "trash_items" not in existing_columns:
            db.execute("ALTER TABLE assets ADD COLUMN trash_items TEXT NOT NULL DEFAULT '[]'")
        if "auto_tags" not in existing_columns:
            db.execute("ALTER TABLE assets ADD COLUMN auto_tags TEXT NOT NULL DEFAULT '[]'")
        if "auto_text" not in existing_columns:
            db.execute("ALTER TABLE assets ADD COLUMN auto_text TEXT NOT NULL DEFAULT ''")
        if "auto_analyzed_at" not in existing_columns:
            db.execute("ALTER TABLE assets ADD COLUMN auto_analyzed_at TEXT")
        if "credit" not in existing_columns:
            db.execute("ALTER TABLE assets ADD COLUMN credit TEXT NOT NULL DEFAULT ''")
        for column in ("source_title", "source_author", "source_license", "source_license_url"):
            if column not in existing_columns:
                db.execute(f"ALTER TABLE assets ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        board_columns = {r[1] for r in db.execute("PRAGMA table_info(boards)")}
        if "folder_id" not in board_columns:
            db.execute("ALTER TABLE boards ADD COLUMN folder_id TEXT")
        model_columns = {r[1] for r in db.execute("PRAGMA table_info(models)")}
        if "source_crop" not in model_columns:
            db.execute("ALTER TABLE models ADD COLUMN source_crop TEXT NOT NULL DEFAULT '{}'")
        if "source_asset_ids" not in model_columns:
            db.execute("ALTER TABLE models ADD COLUMN source_asset_ids TEXT NOT NULL DEFAULT '[]'")
        db.execute("INSERT OR IGNORE INTO boards(id,name,view,created_at,updated_at) VALUES(?,?,?,?,?)",
                   ("main", "Everything", json.dumps({"x": 0, "y": 0, "zoom": 1}), now(), now()))
        enrichment.init_db(db)
        db.execute("UPDATE boards SET name='Everything', updated_at=? WHERE id='main' AND name='Main board'", (now(),))
        db.execute("UPDATE models SET status='failed',error='Generation was interrupted. Retry this model.' WHERE status IN ('queued','running')")
    return KEY_FILE.read_text(encoding="utf-8").strip()


PAIRING_KEY = setup()
LOCK = threading.RLock()
ANALYSIS_LOCK = threading.Lock()
MODEL_GENERATION_LOCK = threading.Lock()
OUTBOUND_BRIDGE = outbound_3d.WorkerBridge(
    os.environ.get("VISUAL_BRAIN_3D_REMOTE_TOKEN", ""),
    os.environ.get("VISUAL_BRAIN_3D_OUTBOUND") == "1")


def connection():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def row_dict(row):
    d = dict(row)
    if "tags" in d:
        d["tags"] = json.loads(d["tags"])
        d["favorite"] = bool(d["favorite"])
    if "auto_tags" in d:
        d["auto_tags"] = json.loads(d["auto_tags"])
    if "state" in d:
        d["state"] = json.loads(d["state"])
    if "source_asset_ids" in d:
        d["source_asset_ids"] = json.loads(d["source_asset_ids"])
    if "view" in d:
        d["view"] = json.loads(d["view"])
    return d


def safe_http_url(value) -> str:
    url = str(value or "").strip()[:2048]
    try:
        parsed = urlparse(url)
        return url if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password else ""
    except ValueError:
        return ""


def active_image_records() -> list[dict]:
    with connection() as db:
        rows = db.execute("SELECT id,filename,collection,tags FROM assets WHERE trashed_at IS NULL ORDER BY created_at").fetchall()
    return [{"id": row["id"], "path": str(ASSETS / row["filename"]),
             "collection": row["collection"], "tags": json.loads(row["tags"])} for row in rows]


def detect_mime(raw: bytes) -> str | None:
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if len(raw) > 12 and raw[4:8] == b"ftyp" and raw[8:12] in (b"avif", b"avis"):
        return "image/avif"
    return None


def crop_visible_capture(raw: bytes, crop: dict) -> bytes:
    if not isinstance(crop, dict):
        raise ValueError("Invalid visible image crop")
    try:
        x, y, w, h, vw, vh = (float(crop[k]) for k in ("x", "y", "w", "h", "viewport_w", "viewport_h"))
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid visible image crop") from None
    if not all(math.isfinite(n) for n in (x, y, w, h, vw, vh)) or vw <= 0 or vh <= 0 or w < 5 or h < 5 or x < 0 or y < 0:
        raise ValueError("Invalid visible image crop")
    try:
        with Image.open(BytesIO(raw)) as screenshot:
            if screenshot.width * screenshot.height > 40_000_000:
                raise ValueError("Visible screenshot is too large")
            sx, sy = screenshot.width / vw, screenshot.height / vh
            box = (max(0, round(x * sx)), max(0, round(y * sy)),
                   min(screenshot.width, round((x + w) * sx)), min(screenshot.height, round((y + h) * sy)))
            if box[2] - box[0] < 5 or box[3] - box[1] < 5:
                raise ValueError("Visible image is outside the screen")
            output = BytesIO()
            screenshot.crop(box).save(output, "PNG")
            return output.getvalue()
    except OSError:
        raise ValueError("Visible screenshot could not be decoded") from None


def analyze_asset(asset_id: str, filename: str):
    """Use macOS Vision locally; leave human tags untouched."""
    if sys.platform != "darwin":
        with LOCK, connection() as db:
            enrichment.reset(db, asset_id)
        return
    source = ROOT / "vision_labels.swift"
    executable = DATA / "vision-labeler"
    image_path = ASSETS / filename
    if not image_path.is_file():
        return
    try:
        with ANALYSIS_LOCK:
            if not executable.exists() or executable.stat().st_mtime < source.stat().st_mtime:
                subprocess.run(["swiftc", str(source), "-o", str(executable)], check=True, capture_output=True, timeout=120)
            with tempfile.TemporaryDirectory(prefix="vision-", dir=DATA) as temporary:
                preview = Path(temporary) / "preview.png"
                with Image.open(image_path) as original:
                    image = ImageOps.exif_transpose(original).convert("RGB")
                    image.thumbnail((1024, 1024))
                    image.save(preview, "PNG")
                result = subprocess.run([str(executable), str(preview)], check=True, capture_output=True, text=True, timeout=60)
        analysis = json.loads(result.stdout)
        labels = [str(label)[:80] for label in analysis.get("labels", [])[:12]]
        text = str(analysis.get("text", ""))[:2000]
        with LOCK, connection() as db:
            db.execute("UPDATE assets SET auto_tags=?,auto_text=?,auto_analyzed_at=? WHERE id=?",
                       (json.dumps(labels), text, now(), asset_id))
            db.commit()
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        # Search remains usable with human metadata when Vision is unavailable.
        pass
    finally:
        with LOCK, connection() as db:
            enrichment.reset(db, asset_id)


def analyze_existing_assets():
    with connection() as db:
        pending = db.execute("SELECT id,filename FROM assets WHERE auto_analyzed_at IS NULL").fetchall()
    for record in pending:
        analyze_asset(record["id"], record["filename"])
    with LOCK, connection() as db:
        missing = [r[0] for r in db.execute("""SELECT assets.id FROM assets LEFT JOIN asset_enrichment ON assets.id=asset_enrichment.asset_id
            WHERE assets.trashed_at IS NULL AND assets.auto_analyzed_at IS NOT NULL
            AND (asset_enrichment.asset_id IS NULL OR asset_enrichment.status='waiting')""")]
        for asset_id in missing:
            enrichment.reset(db, asset_id)


DEMO_RESET_AT = ""
SEED = ROOT / "demo-seed"


def sample_available() -> bool:
    """True when the bundled sample boards, pictures included, are present in this copy."""
    manifest = SEED / "manifest.json"
    if not manifest.is_file():
        return False
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))["assets"]
    except (OSError, ValueError, KeyError):
        return False
    return bool(entries) and all((SEED / entry["file"]).is_file() for entry in entries)


def seed_sample() -> dict:
    """Fill an empty library with the bundled sample boards, so a first visit opens on a real board.

    The pictures are open-licence works listed, with their credits, in demo-seed/manifest.json.
    """
    if not sample_available():
        raise ValueError("No sample boards are bundled with this copy.")
    manifest = json.loads((SEED / "manifest.json").read_text(encoding="utf-8"))
    base = datetime.now(timezone.utc)

    def stamp(offset: int = 0) -> str:
        return (base + timedelta(seconds=offset)).isoformat(timespec="seconds")

    asset_ids: dict[str, str] = {}
    analyze: list[tuple[str, str]] = []
    with LOCK, connection() as db:
        if db.execute("SELECT COUNT(*) FROM assets").fetchone()[0]:
            raise ValueError("Sample boards only load into an empty library.")
        for folder in manifest.get("folders", []):
            db.execute("INSERT OR IGNORE INTO folders(id,name,created_at) VALUES(?,?,?)", (folder["id"], folder["name"], stamp()))
        for n, board in enumerate(manifest["boards"]):
            db.execute("""INSERT INTO boards(id,name,view,created_at,updated_at,folder_id) VALUES(?,?,?,?,?,?)
                          ON CONFLICT(id) DO UPDATE SET name=excluded.name, view=excluded.view,
                          folder_id=excluded.folder_id, updated_at=excluded.updated_at""",
                       (board["id"], board["name"], json.dumps(board.get("view") or {"x": 0, "y": 0, "zoom": 1}),
                        stamp(n), stamp(), board.get("folder")))
        total = len(manifest["assets"])
        for n, entry in enumerate(manifest["assets"]):
            raw = (SEED / entry["file"]).read_bytes()
            mime = detect_mime(raw)
            if mime not in IMAGE_MIMES:
                raise ValueError(f"Sample image {entry['file']} is not a supported picture.")
            digest = hashlib.sha256(raw).hexdigest()
            asset_id = "img_" + digest[:12]
            created = stamp(total - n)  # first entry is the newest, so the Library opens on it
            rel = f"{created[:4]}/{created[5:7]}/{asset_id}.{IMAGE_MIMES[mime]}"
            path = ASSETS / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            with Image.open(BytesIO(raw)) as decoded:
                width, height = decoded.size
            db.execute("""INSERT INTO assets(id,sha256,mime,filename,source_url,image_url,alt,title,tags,collection,favorite,
                          created_at,pixel_width,pixel_height,credit) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (asset_id, digest, mime, rel, entry.get("source_url", ""), "", entry.get("alt", ""),
                        entry.get("title", ""), json.dumps(entry.get("tags", [])), entry.get("collection", ""),
                        int(bool(entry.get("favorite"))), created, width, height, entry.get("credit", "")))
            asset_ids[entry["key"]] = asset_id
            analyze.append((asset_id, rel))
        for n, entry in enumerate(manifest["items"]):
            state = dict(entry["state"])
            asset_id = None
            if entry["kind"] == "image":
                asset_id = asset_ids[entry["asset"]]
                state.setdefault("crop", {"x": 0, "y": 0, "w": 1, "h": 1})
            state.setdefault("rotation", 0)
            state.setdefault("locked", False)
            item_id = "item_" + hashlib.sha1(f"{entry['board']}:{n}".encode()).hexdigest()[:12]
            db.execute("INSERT INTO items VALUES(?,?,?,?,?,?)",
                       (item_id, entry["board"], entry["kind"], asset_id, json.dumps(state), stamp()))
        db.commit()
    if sys.platform == "darwin" and not hosting.hosted():
        # Apple Vision adds labels and visible text, one picture at a time, in the background.
        threading.Thread(target=lambda: [analyze_asset(a, f) for a, f in analyze], daemon=True).start()
    return {"assets": len(asset_ids), "boards": len(manifest["boards"]), "items": len(manifest["items"])}


def reset_demo_data():
    """Wipe every board, image and font, then lay the sample boards back out so the public playground starts fresh."""
    global DEMO_RESET_AT
    with LOCK, connection() as db:
        for table in ("critique_comments", "critique_invites", "items", "asset_enrichment", "models", "assets", "fonts", "boards", "folders"):
            db.execute(f"DELETE FROM {table}")
        db.execute("INSERT INTO boards(id,name,view,created_at,updated_at) VALUES(?,?,?,?,?)",
                   ("main", "Everything", json.dumps({"x": 0, "y": 0, "zoom": 1}), now(), now()))
        db.commit()
        for folder in (ASSETS, FONTS, MODELS):
            for file in folder.iterdir():
                if file.is_file():
                    file.unlink(missing_ok=True)
        (DATA / "clip-index.sqlite3").unlink(missing_ok=True)
        # Still under the lock: a page that loads mid-reset waits a moment instead of opening on an empty board.
        if sample_available():
            seed_sample()
    DEMO_RESET_AT =datetime.fromtimestamp(time.time() + DEMO_RESET_HOURS * 3600, timezone.utc).isoformat(timespec="seconds")


def demo_reset_loop():
    global DEMO_RESET_AT
    DEMO_RESET_AT = datetime.fromtimestamp(time.time() + DEMO_RESET_HOURS * 3600, timezone.utc).isoformat(timespec="seconds")
    while True:
        time.sleep(DEMO_RESET_HOURS * 3600)
        try:
            reset_demo_data()
        except Exception as exc:  # a failed reset must not kill the demo
            print("demo reset failed:", exc, file=sys.stderr, flush=True)


def local_visual_search_available():
    return (sys.platform == "darwin" and not hosting.hosted()
            and (DATA / "clip-venv/bin/python").is_file()
            and (DATA / "clip-model/model.safetensors").is_file()
            and (DATA / "clip-model/source.json").is_file())


def capabilities():
    """Describe actions this running instance can actually perform."""
    mac_local = sys.platform == "darwin" and not hosting.hosted()
    return {
        "runtime": "hosted" if hosting.hosted() else "local",
        "demo": hosting.DEMO,
        "demo_reset_at": DEMO_RESET_AT,
        "vision_labels": mac_local,
        "local_visual_search": local_visual_search_available(),
        "similar_images": True,
        "free_3d": generation_available(),
        "max_3d_images": 2 if hosting.DEMO else 8,
        "free_3d_multi": generation_available(multi=True),
        "min_3d_multi_images": free_3d.MIN_MULTI_PHOTOS,
        "max_3d_multi_images": free_3d.MAX_MULTI_PHOTOS,
        "model_import": True,
        "backup_export": True,
        "extension_capture": not hosting.hosted(),
        "sample_board": sample_available(),
    }


def generation_available(multi: bool = False) -> bool:
    if OUTBOUND_BRIDGE.enabled:
        return OUTBOUND_BRIDGE.connected(multi)
    return free_3d.multi_worker_available() if multi else free_3d.worker_available()


def forget_local_visual_vectors(asset_ids):
    index = DATA / "clip-index.sqlite3"
    if not asset_ids or not index.exists():
        return
    with sqlite3.connect(index, timeout=10) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vectors'").fetchone():
            db.executemany("DELETE FROM vectors WHERE id=?", [(asset_id,) for asset_id in asset_ids])


def local_visual_search(query: str):
    if not local_visual_search_available():
        raise ValueError("Local visual search is not installed")
    query = query.strip()[:120]
    if len(query) < 2:
        return {"matches": []}
    with connection() as db:
        images = [{"id": row["id"], "path": str(ASSETS / row["filename"])} for row in
                  db.execute("SELECT id,filename FROM assets WHERE trashed_at IS NULL")]
    try:
        result = subprocess.run([str(DATA / "clip-venv/bin/python"), str(ROOT / "local_clip.py")],
                                input=json.dumps({"query": query, "images": images}), text=True,
                                capture_output=True, timeout=120, env={**os.environ, "VISUAL_BRAIN_DATA_DIR": str(DATA), "HF_HUB_OFFLINE": "1"})
        if result.returncode:
            raise ValueError("Local visual search failed")
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise ValueError("Local visual search is temporarily unavailable") from exc


def local_visual_similarity(asset_id: str):
    with connection() as db:
        assets = [dict(row) for row in db.execute(
            "SELECT id,filename FROM assets WHERE trashed_at IS NULL ORDER BY created_at DESC")]
    target = next((item for item in assets if item["id"] == asset_id), None)
    if not target:
        raise ValueError("Saved image not found")
    images = [{"id": item["id"], "path": str(ASSETS / item["filename"])} for item in assets]

    if local_visual_search_available():
        try:
            result = subprocess.run([str(DATA / "clip-venv/bin/python"), str(ROOT / "local_clip.py")],
                                    input=json.dumps({"image_id": asset_id, "images": images}), text=True,
                                    capture_output=True, timeout=120,
                                    env={**os.environ, "VISUAL_BRAIN_DATA_DIR": str(DATA), "HF_HUB_OFFLINE": "1"})
            if result.returncode == 0:
                matches = json.loads(result.stdout).get("matches", [])
                return {"matches": matches, "method": "local CLIP"}
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pass

    target_path = ASSETS / target["filename"]
    try:
        target_hash = int(enrichment._dhash(target_path), 16)
    except (OSError, ValueError):
        raise ValueError("This image could not be analyzed locally") from None
    ranked = []
    for item in assets:
        if item["id"] == asset_id:
            continue
        try:
            candidate_hash = int(enrichment._dhash(ASSETS / item["filename"]), 16)
        except (OSError, ValueError):
            continue
        distance = (target_hash ^ candidate_hash).bit_count()
        if distance <= 20:
            ranked.append((item["id"], 1 - distance / 64))
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return {"matches": [{"id": identifier, "score": round(score, 4)} for identifier, score in ranked[:12]],
            "method": "local visual hash"}


def add_font(payload: dict):
    data_url = payload.get("font_data_url")
    if not isinstance(data_url, str) or not data_url.startswith("data:") or "," not in data_url:
        raise ValueError("Choose a TTF, OTF, WOFF, or WOFF2 font file")
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except (ValueError, base64.binascii.Error):
        raise ValueError("Invalid font file") from None
    if not raw or len(raw) > MAX_FONT:
        raise ValueError("Font must be smaller than 8 MB")
    formats = [(b"OTTO", "otf", "font/otf"), (b"\x00\x01\x00\x00", "ttf", "font/ttf"),
               (b"wOFF", "woff", "font/woff"), (b"wOF2", "woff2", "font/woff2")]
    kind = next(((ext, mime) for signature, ext, mime in formats if raw.startswith(signature)), None)
    if not kind:
        raise ValueError("Unsupported font format. Choose TTF, OTF, WOFF, or WOFF2")
    digest = hashlib.sha256(raw).hexdigest()
    name = Path(str(payload.get("name") or "Saved font")).stem.strip()[:100] or "Saved font"
    with LOCK, connection() as db:
        prior = db.execute("SELECT * FROM fonts WHERE sha256=?", (digest,)).fetchone()
        if prior:
            font = dict(prior)
        else:
            font_id = "font_" + uuid.uuid4().hex[:12]
            filename = f"{font_id}.{kind[0]}"
            (FONTS / filename).write_bytes(raw)
            db.execute("INSERT INTO fonts(id,sha256,name,filename,mime,created_at) VALUES(?,?,?,?,?,?)",
                       (font_id, digest, name, filename, kind[1], now()))
            font = dict(db.execute("SELECT * FROM fonts WHERE id=?", (font_id,)).fetchone())
        board = db.execute("SELECT * FROM boards WHERE name='Font Inspo' COLLATE NOCASE LIMIT 1").fetchone()
        if board:
            board_id = board["id"]
        else:
            board_id = "board_" + uuid.uuid4().hex[:10]
            db.execute("INSERT INTO boards(id,name,view,created_at,updated_at) VALUES(?,?,?,?,?)",
                       (board_id, "Font Inspo", json.dumps({"x":0,"y":0,"zoom":1}), now(), now()))
        count = db.execute("SELECT COUNT(*) FROM items WHERE board_id=?", (board_id,)).fetchone()[0]
        specimen = {"x":80+(count%3)*320,"y":90+(count//3)*270,"w":270,"h":180,
                    "text":f"{font['name']}\nAa Bb Cc 123\nThe quick brown fox", "fontFamily":"VBFont_"+font["id"],
                    "fontSize":26,"fontWeight":"400","textAlign":"left","color":"#30382f",
                    "rotation":0,"z":count+1,"locked":False}
        item_id = "item_" + uuid.uuid4().hex[:12]
        db.execute("INSERT INTO items VALUES(?,?,?,?,?,?)", (item_id,board_id,"text",None,json.dumps(specimen),now()))
        db.commit()
    return {"font":font,"board_id":board_id,"item_id":item_id,"duplicate":prior is not None}


def add_image(payload: dict):
    data_url = payload.get("image_data_url")
    if not isinstance(data_url, str) or not data_url.startswith("data:image/") or "," not in data_url:
        raise ValueError("Image bytes are required. Try saving the image again or upload a file.")
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except (ValueError, base64.binascii.Error):
        raise ValueError("Invalid image data") from None
    limit = DEMO_MAX_IMAGE if hosting.DEMO else MAX_IMAGE
    if not raw or len(raw) > limit:
        raise ValueError(f"Image must be between 1 byte and {limit // (1024 * 1024)} MB")
    if payload.get("screen_crop") is not None:
        raw = crop_visible_capture(raw, payload["screen_crop"])
        if len(raw) > MAX_IMAGE:
            raise ValueError("Visible image clip is too large")
    mime = detect_mime(raw)
    if mime not in IMAGE_MIMES:
        raise ValueError("Supported images: JPEG, PNG, WebP, GIF, AVIF")
    digest = hashlib.sha256(raw).hexdigest()
    with LOCK, connection() as db:
        existing = db.execute("SELECT * FROM assets WHERE sha256=?", (digest,)).fetchone()
        duplicate = existing is not None
        if not duplicate and hosting.DEMO and db.execute("SELECT COUNT(*) FROM assets").fetchone()[0] >= DEMO_MAX_ASSETS:
            raise ValueError("The public demo is full right now. It clears itself at the next reset.")
        if existing:
            if existing["trashed_at"]:
                for saved in json.loads(existing["trash_items"]):
                    if db.execute("SELECT 1 FROM boards WHERE id=?", (saved["board_id"],)).fetchone():
                        db.execute("INSERT OR IGNORE INTO items VALUES(?,?,?,?,?,?)", (saved["id"],saved["board_id"],saved["kind"],saved["asset_id"],json.dumps(saved["state"]),saved["updated_at"]))
                db.execute("UPDATE assets SET trashed_at=NULL, trash_items='[]' WHERE id=?", (existing["id"],))
            asset = row_dict(existing)
            asset["trashed_at"] = None
        else:
            asset_id = "img_" + uuid.uuid4().hex[:12]
            created = now()
            rel = f"{created[:4]}/{created[5:7]}/{asset_id}.{IMAGE_MIMES[mime]}"
            path = ASSETS / rel
            source_url = safe_http_url(payload.get("source_url"))
            image_url = safe_http_url(payload.get("image_url"))
            try:
                with Image.open(BytesIO(raw)) as decoded:
                    pixel_width, pixel_height = decoded.size
                if pixel_width * pixel_height > 40_000_000:
                    raise ValueError("Image dimensions exceed 40 million pixels")
            except OSError:
                raise ValueError("Image bytes could not be decoded") from None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            fields = (asset_id, digest, mime, rel, source_url,
                      image_url, str(payload.get("alt") or "")[:500],
                      str(payload.get("title") or payload.get("alt") or "")[:500], json.dumps([]), "",
                      int(bool(payload.get("favorite"))), created, pixel_width, pixel_height)
            db.execute("INSERT INTO assets(id,sha256,mime,filename,source_url,image_url,alt,title,tags,collection,favorite,created_at,pixel_width,pixel_height) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", fields)
            db.execute("UPDATE assets SET source_title=?,source_author=?,source_license=?,source_license_url=? WHERE id=?",
                       (str(payload.get("source_title") or "")[:500], str(payload.get("source_author") or "")[:200],
                        str(payload.get("source_license") or "")[:200],
                        safe_http_url(payload.get("source_license_url")), asset_id))
            asset = row_dict(db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone())
        board_id = str(payload.get("board_id") or "main")
        if not db.execute("SELECT 1 FROM boards WHERE id=?", (board_id,)).fetchone():
            raise ValueError("Selected board no longer exists")
        count = db.execute("SELECT COUNT(*) FROM items WHERE board_id=?", (board_id,)).fetchone()[0]
        pixel_width = asset.get("pixel_width") or 1
        pixel_height = asset.get("pixel_height") or 1
        item_width = 260
        item_height = max(80, min(520, round(item_width * pixel_height / max(1, pixel_width))))
        x = 80 + (count % 5) * 300
        y = 90 + (count // 5) * 330
        position = payload.get("position")
        if position is not None:
            if not isinstance(position, dict):
                raise ValueError("Invalid image placement")
            try:
                center_x, center_y = float(position["center_x"]), float(position["center_y"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("Invalid image placement") from None
            if not all(math.isfinite(value) and abs(value) <= 10_000_000 for value in (center_x, center_y)):
                raise ValueError("Invalid image placement")
            x, y = center_x - item_width / 2, center_y - item_height / 2
        item = {"id": "item_" + uuid.uuid4().hex[:12], "board_id": board_id,
                "kind": "image", "asset_id": asset["id"],
                "state": {"x": x, "y": y, "w": item_width, "h": item_height, "rotation": 0,
                          "z": count + 1, "locked": False, "crop": {"x": 0, "y": 0, "w": 1, "h": 1}},
                "updated_at": now()}
        db.execute("INSERT INTO items VALUES(?,?,?,?,?,?)", (item["id"], board_id, "image", asset["id"],
                   json.dumps(item["state"]), item["updated_at"]))
        if not duplicate:
            enrichment.wait_for_vision(db, asset["id"])
        db.commit()
    if not duplicate:
        threading.Thread(target=analyze_asset, args=(asset["id"], asset["filename"]), daemon=True).start()
    return {"asset": asset, "item": item, "duplicate": duplicate}


def fetch_public_image_url(url: str) -> tuple[bytes, str, str]:
    """Fetch a user-entered public image URL without following unsafe targets."""
    current = str(url or "").strip()
    if not current or len(current) > 2048:
        raise ValueError("Enter an image URL under 2,048 characters.")

    for _ in range(5):
        parsed = urlparse(current)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Use a direct http or https image URL without sign-in credentials.")
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError("For privacy, image URLs must point to a public website.")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addresses = {item[4][0].split("%", 1)[0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        except (OSError, ValueError):
            raise ValueError("That image host could not be reached.") from None
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("For privacy, image URLs must point to a public website.")

        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(current, stream=True, allow_redirects=False, timeout=(4, 12),
                                       headers={"Accept": "image/*", "User-Agent": "VisualBrain/1.0"})
                with response:
                    if response.is_redirect:
                        location = response.headers.get("Location")
                        if not location:
                            raise ValueError("The image URL returned an invalid redirect.")
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise ValueError(f"The image host returned HTTP {response.status_code}.")
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if content_type and not content_type.startswith("image/") and content_type != "application/octet-stream":
                        raise ValueError("That URL does not return an image file.")
                    try:
                        declared_length = int(response.headers.get("Content-Length", "0"))
                    except ValueError:
                        declared_length = 0
                    if declared_length > MAX_IMAGE:
                        raise ValueError("Images must be 25 MB or smaller.")
                    chunks = []
                    size = 0
                    for chunk in response.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > MAX_IMAGE:
                            raise ValueError("Images must be 25 MB or smaller.")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    mime = detect_mime(raw)
                    if mime not in IMAGE_MIMES:
                        raise ValueError("Supported image URLs: JPEG, PNG, WebP, GIF, and AVIF.")
                    return raw, mime, current
        except requests.RequestException as exc:
            raise ValueError("The image could not be downloaded. Check the URL and try again.") from exc
    raise ValueError("The image URL redirected too many times.")


def add_image_from_url(payload: dict):
    url = str(payload.get("url") or "").strip()
    raw, mime, final_url = fetch_public_image_url(url)
    filename = Path(urlparse(final_url).path).name
    title = filename or urlparse(final_url).hostname or "Image from URL"
    data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    return add_image({"image_data_url": data_url, "source_url": url, "image_url": final_url,
                      "title": title[:500], "board_id": payload.get("board_id"), "position": payload.get("position")})


def generate_board_models(jobs: list[tuple[str, str | list[str], dict]]) -> None:
    """Serialize bounded CPU batches so separate requests cannot overload the worker."""
    with MODEL_GENERATION_LOCK:
        _generate_board_models(jobs)


def _generate_board_models(jobs: list[tuple[str, str | list[str], dict]]) -> None:
    for model_id, source_filename, crop in jobs:
        with LOCK, connection() as db:
            if not db.execute("SELECT 1 FROM models WHERE id=?", (model_id,)).fetchone():
                continue
            db.execute("UPDATE models SET status='running',updated_at=? WHERE id=?", (now(), model_id))
            db.commit()
        output = MODELS / f"{model_id}.glb"
        try:
            with tempfile.TemporaryDirectory(prefix="visual-brain-3d-source-") as temporary:
                if isinstance(source_filename, list):
                    sources = [free_3d.prepared_multi_source(ASSETS / filename,
                               Path(temporary) / f"photo_{index:03}.jpg")
                               for index, filename in enumerate(source_filename)]
                    if OUTBOUND_BRIDGE.enabled:
                        OUTBOUND_BRIDGE.send_job(model_id, "multi", free_3d.multi_archive(sources))
                        continue  # the Ryzen worker posts the finished model to the callback
                    if free_3d.multi_local_available():
                        free_3d.generate_multi(sources, output)
                    else:
                        free_3d.dispatch_remote_multi(model_id, sources)
                        continue  # the worker posts the finished GLB to the callback route
                else:
                    source = free_3d.cropped_source(ASSETS / source_filename, Path(temporary) / "source.jpg", crop)
                    if OUTBOUND_BRIDGE.enabled:
                        OUTBOUND_BRIDGE.send_job(model_id, "single", free_3d.remote_image_bytes(source))
                        continue  # the Ryzen worker posts the finished model to the callback
                    if free_3d.local_available():
                        free_3d.generate(source, output)
                    else:
                        free_3d.dispatch_remote(model_id, source)
                        continue  # the worker posts the finished GLB to the callback route
            status, error = "ready", ""
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            output.unlink(missing_ok=True)
            status, error = "failed", str(exc)[:250]
        with LOCK, connection() as db:
            if db.execute("SELECT 1 FROM models WHERE id=?", (model_id,)).fetchone():
                db.execute("UPDATE models SET status=?,error=?,updated_at=? WHERE id=?",
                           (status, error, now(), model_id))
                db.commit()
            else:
                output.unlink(missing_ok=True)


def remove_unused_models(db) -> list[str]:
    used = {json.loads(row[0]).get("model_id") for row in db.execute("SELECT state FROM items WHERE kind='model'")}
    unused = [row for row in db.execute("SELECT id,filename FROM models") if row["id"] not in used]
    for row in unused:
        db.execute("DELETE FROM models WHERE id=?", (row["id"],))
    return [row["filename"] for row in unused]


def critique_board_for_token(token: str) -> str | None:
    if not 32 <= len(token) <= 80 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in token):
        return None
    with connection() as db:
        row = db.execute("SELECT board_id FROM critique_invites WHERE token_hash=?",
                         (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return row["board_id"] if row else None


def critique_snapshot(board_id: str, shared: bool = False) -> dict:
    with connection() as db:
        board = db.execute("SELECT id,name FROM boards WHERE id=?", (board_id,)).fetchone()
        if not board:
            raise ValueError("Board not found")
        items = [row_dict(row) for row in db.execute("SELECT * FROM items WHERE board_id=? ORDER BY updated_at", (board_id,))]
        image_ids = {item["asset_id"] for item in items if item["kind"] == "image" and item["asset_id"]}
        model_ids = {item["state"].get("model_id") for item in items if item["kind"] == "model"}
        images = [{"id": row["id"], "title": row["title"], "alt": row["alt"]} for row in
                  db.execute("SELECT id,title,alt FROM assets WHERE trashed_at IS NULL") if row["id"] in image_ids]
        models = [{"id": row["id"], "status": row["status"], "source_asset_id": row["source_asset_id"]} for row in
                  db.execute("SELECT id,status,source_asset_id FROM models") if row["id"] in model_ids]
        comments = [dict(row) for row in db.execute(
            "SELECT * FROM critique_comments WHERE board_id=? ORDER BY created_at", (board_id,))]
        invite_active = db.execute("SELECT 1 FROM critique_invites WHERE board_id=?", (board_id,)).fetchone() is not None
    result = {"board": dict(board), "items": items, "images": images, "models": models, "comments": comments}
    if not shared:
        result["invite_active"] = invite_active
    return result


def add_critique_comment(board_id: str, payload: dict) -> dict:
    item_id = str(payload.get("item_id") or "")
    author = str(payload.get("author") or "Guest").strip()[:60] or "Guest"
    body = str(payload.get("body") or "").strip()[:1000]
    try:
        x, y = float(payload.get("x")), float(payload.get("y"))
    except (TypeError, ValueError):
        raise ValueError("Place the note on an image or model") from None
    if not body or not all(math.isfinite(value) and 0 <= value <= 1 for value in (x, y)):
        raise ValueError("Write a note and place it on the object")
    with LOCK, connection() as db:
        row = db.execute("SELECT kind FROM items WHERE id=? AND board_id=?", (item_id, board_id)).fetchone()
        if not row or row["kind"] not in ("image", "model"):
            raise ValueError("That object is no longer on this board")
        if db.execute("SELECT COUNT(*) FROM critique_comments WHERE board_id=?", (board_id,)).fetchone()[0] >= 500:
            raise ValueError("This board has reached its note limit")
        comment = {"id": "note_" + uuid.uuid4().hex[:12], "board_id": board_id,
                   "item_id": item_id, "x": x, "y": y, "author": author,
                   "body": body, "created_at": now(), "resolved_at": None}
        db.execute("INSERT INTO critique_comments VALUES(?,?,?,?,?,?,?,?,?)", tuple(comment.values()))
        db.commit()
    return comment


class Handler(BaseHTTPRequestHandler):
    server_version = "VisualBrain/1.0"

    def parse_request(self):
        if not super().parse_request():
            return False
        # Railway probes the service by its internal hostname. Only this exact
        # non-sensitive path may bypass the public-host check.
        if self.path != "/healthz" and not hosting.allowed_host(self.headers.get("Host", ""), PORT):
            self.send_error(421, "Unrecognized host")
            return False
        return True

    def log_message(self, fmt, *args):
        if self.path.startswith(("/critique/", "/api/critique/shared/")):
            sys.stderr.write("%s private critique request\n" % self.log_date_time_string())
            return
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def send_bytes(self, body: bytes, mime="application/json", status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=3600" if mime.startswith("image/") and not hosting.hosted() else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, status=200):
        self.send_bytes(json.dumps(obj, separators=(",", ":")).encode(), status=status)

    def error(self, message, status=400):
        self.json({"error": message}, status)

    def peer_is_loopback(self):
        """True when the connection came from this machine rather than the network."""
        return hosting.is_loopback(self.client_address[0] if self.client_address else "")

    def session_ok(self):
        """Whether this browser has an active owner session."""
        return hosting.valid(self.headers.get("Cookie", ""), DATA)

    def same_origin_ok(self):
        origin = self.headers.get("Origin", "")
        return not origin or hosting.allowed_origin(origin, self.headers.get("Host", ""))

    def authorized(self):
        origin = self.headers.get("Origin", "")
        if hosting.hosted():
            return self.session_ok() and (self.command == "GET" and not origin or
                                          hosting.allowed_origin(origin, self.headers.get("Host", "")))
        if origin and not (hosting.allowed_origin(origin, self.headers.get("Host", ""))
                           or origin.startswith("chrome-extension://") or origin.startswith("moz-extension://")):
            return False
        return secrets.compare_digest(self.headers.get("X-Board-Key", ""), PAIRING_KEY)

    def private_read_required(self, path):
        if not hosting.hosted() or hosting.DEMO:
            return False
        public = {"/", "/landing", "/landing.js", "/favicon.svg", "/login", "/healthz",
                  "/vendor/model_viewer/model-viewer.min.js"}
        return path not in public and not path.startswith(("/brand/", "/demo-seed/images/"))

    def owner_read_ok(self):
        if hosting.hosted():
            return self.session_ok()
        return secrets.compare_digest(self.headers.get("X-Board-Key", ""), PAIRING_KEY)

    def request_is_secure(self):
        return hosting.TRUST_PROXY and self.headers.get("X-Forwarded-Proto", "") == "https"

    def handle_login(self):
        if not hosting.hosted() or hosting.DEMO:
            return self.redirect("/board")
        if not self.request_is_secure() and not hosting.ALLOW_INSECURE:
            return self.error("HTTPS required", 403)
        if not hosting.allowed_origin(self.headers.get("Origin", ""), self.headers.get("Host", "")):
            return self.error("Forbidden", 403)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 4096:
                raise ValueError
            raw = self.read_exact(size, 10).decode("utf-8", "replace")
        except (ValueError, OSError, TimeoutError):
            return self.error("Invalid login request", 400)
        from urllib.parse import parse_qs
        supplied = parse_qs(raw).get("passphrase", [""])[0]
        if not hosting.passphrase_ok(supplied):
            time.sleep(0.6)
            return self.send_bytes(hosting.login_page("That passphrase did not match."),
                                   "text/html; charset=utf-8", status=401)
        token = hosting.issue(DATA)
        self.send_response(302)
        self.send_header("Location", "/board")
        self.send_header("Set-Cookie", hosting.cookie_header(token, self.request_is_secure()))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def handle_logout(self):
        if not hosting.allowed_origin(self.headers.get("Origin", ""), self.headers.get("Host", "")):
            return self.error("Forbidden", 403)
        hosting.revoke(self.headers.get("Cookie", ""), DATA)
        self.send_response(302)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", hosting.clear_cookie_header())
        self.send_header("Content-Length", "0")
        self.end_headers()

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def body(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > MAX_BODY:
            raise ValueError("Request is empty or too large")
        return json.loads(self.read_exact(size, 30))

    def read_exact(self, size, seconds):
        deadline = time.monotonic() + seconds
        chunks = []
        remaining = size
        while remaining:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                raise TimeoutError("Request body timed out")
            self.connection.settimeout(timeout)
            chunk = self.rfile.read1(min(65536, remaining))
            if not chunk:
                raise ValueError("Request body ended early")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/3d/worker/socket":
            return OUTBOUND_BRIDGE.serve(self)
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "critique":
            if not critique_board_for_token(parts[1]):
                return self.error("This private link is unavailable", 404)
            return self.send_bytes((ROOT / "critique.html").read_bytes(), "text/html; charset=utf-8")
        if path in ("/critique.js", "/critique.css"):
            file = ROOT / path[1:]
            return self.send_bytes(file.read_bytes(), "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8")
        if len(parts) >= 4 and parts[:3] == ["api", "critique", "shared"]:
            board_id = critique_board_for_token(parts[3])
            if not board_id:
                return self.error("This private link is unavailable", 404)
            if len(parts) == 4:
                return self.json(critique_snapshot(board_id, shared=True))
            if len(parts) == 6 and parts[4] == "assets":
                asset_id = parts[5]
                with connection() as db:
                    row = db.execute("""SELECT assets.filename,assets.mime FROM assets JOIN items ON items.asset_id=assets.id
                        WHERE items.board_id=? AND assets.id=? AND items.kind='image' AND assets.trashed_at IS NULL LIMIT 1""",
                        (board_id, asset_id)).fetchone()
                if not row:
                    return self.error("Image not on this shared board", 404)
                return self.send_bytes((ASSETS / row["filename"]).read_bytes(), row["mime"])
            if len(parts) == 6 and parts[4] == "models":
                model_id = parts[5]
                with connection() as db:
                    used = any(json.loads(row[0]).get("model_id") == model_id for row in db.execute(
                        "SELECT state FROM items WHERE board_id=? AND kind='model'", (board_id,)))
                    row = db.execute("SELECT filename,status FROM models WHERE id=?", (model_id,)).fetchone() if used else None
                if not row or row["status"] != "ready":
                    return self.error("Model not on this shared board", 404)
                return self.send_bytes((MODELS / row["filename"]).read_bytes(), "model/gltf-binary")
            return self.error("Not found", 404)
        # Reachable before sign-in: the platform health check and the front door.
        if path == "/healthz":
            return self.json({"ok": True})
        if path == "/login":
            return self.send_bytes(hosting.login_page(), "text/html; charset=utf-8") if hosting.hosted() and not hosting.DEMO else self.redirect("/board")
        if path == "/logout":
            return self.redirect("/")
        if self.private_read_required(path) and not self.owner_read_ok():
            if not path.startswith("/api/") and "text/html" in self.headers.get("Accept", ""):
                return self.redirect("/login")
            return self.error("Owner access required", 401)
        if path == "/api/capabilities":
            return self.json(capabilities())
        if path == "/api/bootstrap":
            # Same-origin web app only. Extensions use their manually paired key.
            if not self.same_origin_ok():
                return self.error("Forbidden", 403)
            with LOCK, connection() as db:
                cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec="seconds")
                db.execute("UPDATE models SET status='failed',error='The 3D worker did not return a model. Retry to make it again.',updated_at=? WHERE status='running' AND updated_at<?",
                           (now(), cutoff))
                db.commit()
                return self.json({"key": PAIRING_KEY if not hosting.hosted() else "",
                                  "can_edit": not hosting.hosted() or self.owner_read_ok(),
                                  "boards": [row_dict(r) for r in db.execute("SELECT * FROM boards ORDER BY created_at")],
                                  "folders": [row_dict(r) for r in db.execute("SELECT * FROM folders ORDER BY name COLLATE NOCASE")],
                                  "assets": [row_dict(r) for r in db.execute("SELECT * FROM assets WHERE trashed_at IS NULL ORDER BY created_at DESC")],
                                  "models": [row_dict(r) for r in db.execute("SELECT * FROM models ORDER BY created_at DESC")],
                                  "fonts": [dict(r) for r in db.execute("SELECT * FROM fonts ORDER BY created_at")],
                                  "trash": ([{"id":r["id"], "title":r["title"], "trashed_at":r["trashed_at"]} for r in db.execute("SELECT id,title,trashed_at FROM assets WHERE trashed_at IS NOT NULL ORDER BY trashed_at DESC")] if not hosting.hosted() or self.owner_read_ok() else []),
                                  "items": [row_dict(r) for r in db.execute("SELECT * FROM items ORDER BY updated_at")],
                                  "local_visual_search_available": local_visual_search_available(),
                                  "capabilities": capabilities()})
        if path == "/api/boards-list":
            if not self.authorized():
                return self.error("Forbidden", 403)
            with connection() as db:
                return self.json({"boards": [{"id": r["id"], "name": r["name"]} for r in db.execute("SELECT id,name FROM boards ORDER BY created_at")]})
        if path.startswith("/api/critique/board/"):
            if not self.authorized():
                return self.error("Forbidden", 403)
            try:
                return self.json(critique_snapshot(path.rsplit("/", 1)[-1]))
            except ValueError as error:
                return self.error(str(error), 404)
        if path == "/api/taste-map":
            if not self.authorized():
                return self.error("Forbidden", 403)
            return self.json({"groups": visual_features.taste_groups(active_image_records())})
        if path.startswith("/api/fonts/"):
            font_id = path.rsplit("/", 1)[-1]
            with connection() as db:
                font = db.execute("SELECT filename,mime FROM fonts WHERE id=?", (font_id,)).fetchone()
            if not font:
                return self.error("Font not found", 404)
            return self.send_bytes((FONTS / font["filename"]).read_bytes(), font["mime"])
        if path.startswith("/api/enrichment/"):
            if not self.authorized():
                return self.error("Forbidden", 403)
            asset_id = path.rsplit("/", 1)[-1]
            with connection() as db:
                row = db.execute("SELECT status,suggestions,labels,provider,error,updated_at FROM asset_enrichment WHERE asset_id=?", (asset_id,)).fetchone()
            if not row:
                return self.error("Enrichment not found", 404)
            return self.json({"status": row["status"], "suggestions": json.loads(row["suggestions"]),
                              "labels": json.loads(row["labels"]), "provider": row["provider"],
                              "error": row["error"], "updated_at": row["updated_at"]})
        if path.startswith("/api/assets/"):
            asset_id = path.rsplit("/", 1)[-1]
            with connection() as db:
                row = db.execute("SELECT mime,filename FROM assets WHERE id=?" +
                                 ("" if self.owner_read_ok() else " AND trashed_at IS NULL"),
                                 (asset_id,)).fetchone()
            if not row:
                return self.error("Asset not found", 404)
            file = ASSETS / row["filename"]
            return self.send_bytes(file.read_bytes(), row["mime"])
        if path.startswith("/api/models/"):
            model_id = path.rsplit("/", 1)[-1]
            with connection() as db:
                row = db.execute("SELECT filename,status FROM models WHERE id=?", (model_id,)).fetchone()
            if not row or row["status"] != "ready":
                return self.error("Model not ready", 404)
            file = MODELS / row["filename"]
            if not file.is_file():
                return self.error("Model file not found", 404)
            return self.send_bytes(file.read_bytes(), "model/gltf-binary")
        if path == "/api/export":
            if not self.authorized():
                return self.error("Forbidden", 403)
            with connection() as db:
                snapshot = {name: [row_dict(r) for r in db.execute(f"SELECT * FROM {name}")]
                            for name in ("assets", "models", "boards", "items", "folders", "asset_enrichment", "fonts", "critique_comments", "critique_invites")}
            out = BytesIO()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("manifest.json", json.dumps(snapshot, indent=2))
                # The manifest is convenient to inspect. The SQLite snapshot is
                # the authoritative, complete backup of every record.
                with tempfile.TemporaryDirectory(prefix="visual-brain-backup-") as temporary:
                    database_copy = Path(temporary) / "visual-brain.sqlite3"
                    with sqlite3.connect(database_copy) as target:
                        with connection() as source:
                            source.backup(target)
                    z.write(database_copy, "data/visual-brain.sqlite3")
                for asset in snapshot["assets"]:
                    z.write(ASSETS / asset["filename"], "assets/" + asset["filename"])
                for font in snapshot["fonts"]:
                    z.write(FONTS / font["filename"], "fonts/" + font["filename"])
                for model in snapshot["models"]:
                    file = MODELS / model["filename"]
                    if file.is_file():
                        z.write(file, "models/" + model["filename"])
                z.writestr("README.txt", "Visual Brain backup. data/visual-brain.sqlite3 holds every record. "
                           "To restore, stop the server, copy data/visual-brain.sqlite3, assets/, models/ and fonts/ "
                           "into the data/ folder of a fresh install, then start it again. "
                           "This ZIP contains your private images. Do not upload it publicly.\n")
            return self.send_bytes(out.getvalue(), "application/zip", extra={"Content-Disposition": "attachment; filename=visual-brain-backup.zip"})
        if path.startswith("/brand/"):
            # Logo, demo film and poster. Only plain files directly inside brand/.
            name = path[len("/brand/"):]
            file = (ROOT / "brand" / name).resolve()
            if "/" in name or not name or file.parent != (ROOT / "brand").resolve() or not file.is_file():
                return self.error("Not found", 404)
            mime = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
            return self.send_bytes(file.read_bytes(), mime, extra={"Cache-Control": "public, max-age=86400"})
        if path.startswith("/demo-seed/images/"):
            # The sample pictures the landing page shows. Only plain files directly inside demo-seed/images/.
            name = path[len("/demo-seed/images/"):]
            file = (SEED / "images" / name).resolve()
            if "/" in name or not name or file.parent != (SEED / "images").resolve() or not file.is_file():
                return self.error("Not found", 404)
            mime = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
            return self.send_bytes(file.read_bytes(), mime, extra={"Cache-Control": "public, max-age=86400"})
        if path == "/landing":
            return self.send_bytes((ROOT / "landing.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/vendor/model_viewer/model-viewer.min.js":
            return self.send_bytes((ROOT / "vendor/model_viewer/model-viewer.min.js").read_bytes(), "text/javascript; charset=utf-8")
        if path in ("/", "/board", "/index.html", "/app.js", "/save-as.js", "/style.css", "/glow.css", "/glow.js", "/features.css", "/features.js", "/landing.js", "/favicon.svg"):
            if path == "/":
                file = ROOT / ("landing.html" if hosting.hosted() else "index.html")
            else:
                file = ROOT / ("index.html" if path == "/board" else path[1:])
            mime = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
            return self.send_bytes(file.read_bytes(), mime)
        return self.error("Not found", 404)

    def handle_3d_callback(self, model_id: str):
        """Accept a result from the one configured private Ryzen worker."""
        expected = os.environ.get("VISUAL_BRAIN_3D_REMOTE_TOKEN", "")
        provided = self.headers.get("X-Visual-Brain-Worker-Token", "")
        if (not free_3d.MODEL_ID.fullmatch(model_id) or len(expected) < 32
                or not hmac.compare_digest(provided, expected) or self.headers.get("Origin")):
            return self.error("Forbidden", 403)
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.error("Invalid result length")
        if not 1 <= size <= free_3d.MAX_GLB:
            return self.error("Result is too large")
        raw = self.rfile.read(size)
        kind = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if kind == "model/gltf-binary":
            try:
                free_3d.validate_glb(raw)
            except ValueError:
                return self.error("Worker returned an invalid 3D model")
            status, error = "ready", ""
        elif kind == "application/json" and size <= 1024:
            try:
                message = json.loads(raw).get("error", "3D generation failed")
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                return self.error("Invalid worker result")
            status, error = "failed", str(message)[:250]
        else:
            return self.error("Invalid worker result")
        with LOCK, connection() as db:
            record = db.execute("SELECT filename,status FROM models WHERE id=?", (model_id,)).fetchone()
            if not record or record["status"] not in ("running", "queued"):
                return self.error("Job is no longer active", 409)
            if status == "ready":
                destination = MODELS / record["filename"]
                temporary = MODELS / (model_id + ".incoming")
                temporary.write_bytes(raw)
                temporary.replace(destination)
            db.execute("UPDATE models SET status=?,error=?,updated_at=? WHERE id=?", (status, error, now(), model_id))
            db.commit()
        return self.json({"status": status})

    def do_POST(self):
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:3] == ["api", "3d", "callback"]:
            return self.handle_3d_callback(parts[3])
        if path == "/api/login":
            return self.handle_login()
        if path == "/api/logout":
            return self.handle_logout()
        if len(parts) == 5 and parts[:3] == ["api", "critique", "shared"] and parts[4] == "comments":
            board_id = critique_board_for_token(parts[3])
            if not board_id:
                return self.error("This private link is unavailable", 404)
            if not hosting.allowed_origin(self.headers.get("Origin", ""), self.headers.get("Host", "")):
                return self.error("Forbidden", 403)
            try:
                if int(self.headers.get("Content-Length", "0")) > 4096:
                    raise ValueError("Note is too long")
                return self.json(add_critique_comment(board_id, self.body()), 201)
            except (ValueError, json.JSONDecodeError) as error:
                return self.error(str(error))
        if not self.authorized():
            return self.error("Forbidden", 403)
        try:
            data = self.body()
            if path == "/api/capture":
                return self.json(add_image(data), 201)
            if path == "/api/capture-url":
                return self.json(add_image_from_url(data), 201)
            if path == "/api/load-sample":
                return self.json(seed_sample(), 201)
            if path == "/api/fonts":
                return self.json(add_font(data), 201)
            if path.startswith("/api/enrichment/") and path.endswith("/retry"):
                asset_id = path.split("/")[-2]
                with LOCK, connection() as db:
                    record = db.execute("SELECT filename FROM assets WHERE id=? AND trashed_at IS NULL", (asset_id,)).fetchone()
                    if not record:
                        return self.error("Image not found", 404)
                    enrichment.wait_for_vision(db, asset_id)
                    db.commit()
                threading.Thread(target=analyze_asset, args=(asset_id, record["filename"]), daemon=True).start()
                return self.json({"status": "waiting"})
            if path == "/api/local-visual-search":
                if not capabilities()["local_visual_search"]:
                    return self.error("Image content search is unavailable here. Title and tag search still work.", 501)
                return self.json(local_visual_search(str(data.get("query") or "")))
            if path == "/api/similar-images":
                return self.json(local_visual_similarity(str(data.get("asset_id") or "")))
            if path == "/api/camera-match":
                encoded = data.get("image_data_url")
                if not isinstance(encoded, str) or not encoded.startswith("data:image/") or "," not in encoded:
                    raise ValueError("Choose a camera frame or image")
                try:
                    raw = base64.b64decode(encoded.split(",", 1)[1], validate=True)
                except (ValueError, base64.binascii.Error):
                    raise ValueError("Invalid camera image") from None
                if not raw or len(raw) > 8 * 1024 * 1024 or detect_mime(raw) not in IMAGE_MIMES:
                    raise ValueError("Camera image must be JPEG, PNG or WebP under 8 MB")
                try:
                    query = visual_features.byte_features(raw)
                except OSError:
                    raise ValueError("Camera image could not be read") from None
                matches = visual_features.rank(query, active_image_records())
                with connection() as db:
                    for match in matches:
                        match["boards"] = [row["board_id"] for row in db.execute(
                            "SELECT DISTINCT board_id FROM items WHERE asset_id=? ORDER BY board_id", (match["id"],))]
                return self.json({"matches": matches, "method": "appearance", "frame_saved": False})
            if path == "/api/critique/invite":
                if hosting.DEMO:
                    return self.error("Private links are unavailable in the public playground", 403)
                board_id = str(data.get("board_id") or "")
                token = secrets.token_urlsafe(32)
                with LOCK, connection() as db:
                    if not db.execute("SELECT 1 FROM boards WHERE id=?", (board_id,)).fetchone():
                        raise ValueError("Board not found")
                    db.execute("INSERT OR REPLACE INTO critique_invites VALUES(?,?,?)",
                               (board_id, hashlib.sha256(token.encode()).hexdigest(), now()))
                    db.commit()
                return self.json({"path": "/critique/" + token, "board_id": board_id}, 201)
            if path == "/api/critique/comments":
                board_id = str(data.get("board_id") or "")
                return self.json(add_critique_comment(board_id, data), 201)
            if path.startswith("/api/critique/resolve/"):
                comment_id = path.rsplit("/", 1)[-1]
                with LOCK, connection() as db:
                    db.execute("UPDATE critique_comments SET resolved_at=? WHERE id=?", (now(), comment_id))
                    if not db.total_changes:
                        return self.error("Note not found", 404)
                    db.commit()
                return self.json({"ok": True})
            if path == "/api/models":
                encoded = data.get("model_data_url")
                if not isinstance(encoded, str) or not encoded.startswith("data:") or ";base64," not in encoded:
                    raise ValueError("Choose a GLB model")
                try:
                    raw = base64.b64decode(encoded.split(",", 1)[1], validate=True)
                except (ValueError, base64.binascii.Error):
                    raise ValueError("Invalid GLB data") from None
                if len(raw) > 20 * 1024 * 1024:
                    raise ValueError("Choose a GLB model under 20 MB")
                if hosting.DEMO and len(raw) > 5 * 1024 * 1024:
                    raise ValueError("The public demo accepts GLB models under 5 MB")
                free_3d.validate_glb(raw)
                board_id = str(data.get("board_id") or "main")
                model_id = "model_" + uuid.uuid4().hex[:12]
                item_id = "item_" + uuid.uuid4().hex[:12]
                filename = model_id + ".glb"
                with LOCK, connection() as db:
                    if not db.execute("SELECT 1 FROM boards WHERE id=?", (board_id,)).fetchone():
                        raise ValueError("Board not found")
                    if hosting.DEMO and db.execute("SELECT COUNT(*) FROM models").fetchone()[0] >= 40:
                        raise ValueError("The public demo has reached its model limit until it resets")
                    count = db.execute("SELECT COUNT(*) FROM items WHERE board_id=?", (board_id,)).fetchone()[0]
                    model = {"id": model_id, "filename": filename, "source_asset_id": None,
                             "status": "ready", "error": "", "created_at": now(), "updated_at": now()}
                    item = {"id": item_id, "board_id": board_id, "kind": "model", "asset_id": None,
                            "state": {"x": 80 + count % 4 * 330, "y": 90 + count // 4 * 350,
                                      "w": 300, "h": 300, "rotation": 0, "z": count + 1,
                                      "model_id": model_id, "locked": False}}
                    (MODELS / filename).write_bytes(raw)
                    db.execute("""INSERT INTO models(id,filename,source_asset_id,status,error,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?)""", tuple(model.values()))
                    db.execute("INSERT INTO items VALUES(?,?,?,?,?,?)",
                               (item_id, board_id, "model", None, json.dumps(item["state"]), now()))
                    db.commit()
                return self.json({"model": model, "item": item}, 201)
            if path == "/api/3d/boards":
                grouped = data.get("mode") == "one-object"
                if data.get("mode") not in (None, "separate", "one-object"):
                    raise ValueError("Choose a supported 3D mode")
                if not generation_available(grouped):
                    return self.error("3D creation is temporarily unavailable. You can still add a 3D model file.", 503)
                source_board_id = str(data.get("board_id") or "")
                source_ids = data.get("item_ids")
                if (not isinstance(source_ids, list) or any(not isinstance(value, str) for value in source_ids)
                        or len(set(source_ids)) != len(source_ids)):
                    raise ValueError("Select distinct images from one board")
                if grouped and not free_3d.MIN_MULTI_PHOTOS <= len(source_ids) <= free_3d.MAX_MULTI_PHOTOS:
                    raise ValueError(f"Select {free_3d.MIN_MULTI_PHOTOS} to {free_3d.MAX_MULTI_PHOTOS} overlapping photos")
                if not grouped and not 1 <= len(source_ids) <= 8:
                    raise ValueError("Select one to eight images")
                if hosting.DEMO and not grouped and len(source_ids) > 2:
                    raise ValueError("The public demo makes two 3D objects per board at a time")
                jobs = []
                model_count = 1 if grouped else len(source_ids)
                with LOCK, connection() as db:
                    if hosting.DEMO:
                        total = db.execute("SELECT COUNT(*) FROM models").fetchone()[0]
                        active = db.execute("SELECT COUNT(*) FROM models WHERE status IN ('queued','running')").fetchone()[0]
                        if total + model_count > 40 or active + model_count > 8:
                            raise ValueError("The public demo's 3D worker is busy. Try later.")
                    source_board = db.execute("SELECT name FROM boards WHERE id=?", (source_board_id,)).fetchone()
                    if not source_board:
                        raise ValueError("Source board not found")
                    selected = []
                    for item_id in source_ids:
                        image = db.execute("""SELECT items.asset_id,items.state,assets.filename FROM items JOIN assets ON assets.id=items.asset_id
                            WHERE items.id=? AND items.board_id=? AND items.kind='image' AND assets.trashed_at IS NULL""",
                            (str(item_id), source_board_id)).fetchone()
                        if not image:
                            raise ValueError("Select images from one board")
                        selected.append((image, free_3d.normalized_crop(json.loads(image["state"]).get("crop"))))
                    if grouped and len({image["asset_id"] for image, _ in selected}) != len(selected):
                        raise ValueError("Choose different photos of the same object")
                    board_id = "board_" + uuid.uuid4().hex[:10]
                    name = ("3D from " + source_board["name"])[:80]
                    db.execute("INSERT INTO boards(id,name,view,created_at,updated_at,folder_id) VALUES(?,?,?,?,?,NULL)",
                               (board_id, name, json.dumps({"x": 0, "y": 0, "zoom": 1}), now(), now()))
                    if grouped:
                        model_id = "model_" + uuid.uuid4().hex[:12]
                        db.execute("""INSERT INTO models(id,filename,source_asset_id,status,error,created_at,updated_at,source_crop,source_asset_ids)
                            VALUES(?,?,?,?,?,?,?,?,?)""",
                                   (model_id, model_id + ".glb", selected[0][0]["asset_id"], "queued", "", now(), now(),
                                    "{}", json.dumps([image["asset_id"] for image, _ in selected])))
                        model_state = {"x": 50, "y": 70, "w": 620, "h": 520, "rotation": 0,
                                       "z": 1, "locked": False, "model_id": model_id}
                        db.execute("INSERT INTO items VALUES(?,?,?,?,?,?)",
                                   ("item_" + uuid.uuid4().hex[:12], board_id, "model", None, json.dumps(model_state), now()))
                        jobs.append((model_id, [image["filename"] for image, _ in selected], {}))
                    for index, (image, crop) in enumerate(selected):
                        y = 70 + (index // 6) * 150 if grouped else 70 + index * 365
                        source_state = {"x": 720 + (index % 6) * 145 if grouped else 50, "y": y,
                                        "w": 135 if grouped else 260, "h": 130 if grouped else 300, "rotation": 0,
                                        "z": index + 2 if grouped else index * 2 + 1, "locked": False,
                                        "crop": crop}
                        db.execute("INSERT INTO items VALUES(?,?,?,?,?,?)",
                                   ("item_" + uuid.uuid4().hex[:12], board_id, "image", image["asset_id"], json.dumps(source_state), now()))
                        if grouped:
                            continue
                        model_id = "model_" + uuid.uuid4().hex[:12]
                        db.execute("""INSERT INTO models(id,filename,source_asset_id,status,error,created_at,updated_at,source_crop)
                            VALUES(?,?,?,?,?,?,?,?)""",
                                   (model_id, model_id + ".glb", image["asset_id"], "queued", "", now(), now(), json.dumps(crop)))
                        model_state = {"x": 355, "y": y, "w": 320, "h": 300, "rotation": 0,
                                       "z": index * 2 + 2, "locked": False, "model_id": model_id}
                        db.execute("INSERT INTO items VALUES(?,?,?,?,?,?)",
                                   ("item_" + uuid.uuid4().hex[:12], board_id, "model", None, json.dumps(model_state), now()))
                        jobs.append((model_id, image["filename"], crop))
                    db.commit()
                threading.Thread(target=generate_board_models, args=(jobs,), daemon=True).start()
                return self.json({"board_id": board_id, "queued": len(jobs)}, 201)
            if path.startswith("/api/models/") and path.endswith("/retry"):
                model_id = path.split("/")[-2]
                with LOCK, connection() as db:
                    model = db.execute("""SELECT models.source_asset_id,models.source_asset_ids,models.source_crop,assets.filename FROM models
                        JOIN assets ON assets.id=models.source_asset_id WHERE models.id=? AND models.status='failed'""",
                        (model_id,)).fetchone()
                    if not model:
                        return self.error("Model cannot be retried", 404)
                    source_ids = json.loads(model["source_asset_ids"] or "[]")
                    grouped = len(source_ids) > 1
                    if not generation_available(grouped):
                        return self.error("Free 3D generation is not installed here", 503)
                    if grouped:
                        assets = [db.execute("SELECT filename FROM assets WHERE id=? AND trashed_at IS NULL", (asset_id,)).fetchone()
                                  for asset_id in source_ids]
                        if any(asset is None for asset in assets):
                            return self.error("Restore the source photos before retrying", 409)
                        sources = [asset["filename"] for asset in assets]
                    else:
                        sources = model["filename"]
                    db.execute("UPDATE models SET status='queued',error='',updated_at=? WHERE id=?", (now(), model_id))
                    db.commit()
                crop = json.loads(model["source_crop"] or "{}")
                threading.Thread(target=generate_board_models, args=([(model_id, sources, crop)],), daemon=True).start()
                return self.json({"status": "queued"})
            if path.startswith("/api/assets/") and path.endswith("/restore"):
                asset_id = path.split("/")[-2]
                with LOCK, connection() as db:
                    record = db.execute("SELECT trash_items FROM assets WHERE id=? AND trashed_at IS NOT NULL", (asset_id,)).fetchone()
                    if not record:
                        return self.error("Image not in Trash", 404)
                    for saved in json.loads(record["trash_items"]):
                        if db.execute("SELECT 1 FROM boards WHERE id=?", (saved["board_id"],)).fetchone():
                            db.execute("INSERT OR IGNORE INTO items VALUES(?,?,?,?,?,?)", (saved["id"],saved["board_id"],saved["kind"],saved["asset_id"],json.dumps(saved["state"]),saved["updated_at"]))
                    db.execute("UPDATE assets SET trashed_at=NULL,trash_items='[]' WHERE id=?", (asset_id,))
                    db.commit()
                return self.json({"ok":True})
            if path == "/api/trash/restore-all":
                with LOCK, connection() as db:
                    records = db.execute("SELECT id,trash_items FROM assets WHERE trashed_at IS NOT NULL").fetchall()
                    for record in records:
                        for saved in json.loads(record["trash_items"]):
                            if db.execute("SELECT 1 FROM boards WHERE id=?", (saved["board_id"],)).fetchone():
                                db.execute("INSERT OR IGNORE INTO items VALUES(?,?,?,?,?,?)", (saved["id"],saved["board_id"],saved["kind"],saved["asset_id"],json.dumps(saved["state"]),saved["updated_at"]))
                    db.execute("UPDATE assets SET trashed_at=NULL,trash_items='[]' WHERE trashed_at IS NOT NULL")
                    db.commit()
                return self.json({"restored":len(records)})
            if path == "/api/folders":
                name = str(data.get("name") or "").strip()[:80]
                if not name:
                    raise ValueError("Folder name is required")
                folder_id = "folder_" + uuid.uuid4().hex[:10]
                with LOCK, connection() as db:
                    db.execute("INSERT INTO folders VALUES(?,?,?)", (folder_id,name,now()))
                    db.commit()
                return self.json({"id":folder_id,"name":name},201)
            if path == "/api/boards":
                name = str(data.get("name") or "Untitled board").strip()[:80]
                board_id = "board_" + uuid.uuid4().hex[:10]
                folder_id = data.get("folder_id") or None
                with LOCK, connection() as db:
                    if folder_id and not db.execute("SELECT 1 FROM folders WHERE id=?", (folder_id,)).fetchone():
                        raise ValueError("Folder not found")
                    db.execute("INSERT INTO boards(id,name,view,created_at,updated_at,folder_id) VALUES(?,?,?,?,?,?)", (board_id, name, '{"x":0,"y":0,"zoom":1}', now(), now(), folder_id))
                    db.commit()
                return self.json({"id": board_id, "name": name, "view": {"x": 0, "y": 0, "zoom": 1},"folder_id":folder_id}, 201)
            if path == "/api/items":
                item_id = "item_" + uuid.uuid4().hex[:12]
                board_id = str(data.get("board_id") or "main")
                kind = str(data.get("kind") or "text")
                if kind not in ("text", "frame", "drawing", "image", "shape", "model"):
                    raise ValueError("Unsupported item type")
                state = data.get("state") or {}
                if not isinstance(state, dict):
                    raise ValueError("Invalid item state")
                asset_id = data.get("asset_id") if kind == "image" else None
                with connection() as db:
                    if not db.execute("SELECT 1 FROM boards WHERE id=?", (board_id,)).fetchone():
                        raise ValueError("Board not found")
                    if asset_id and not db.execute("SELECT 1 FROM assets WHERE id=?", (asset_id,)).fetchone():
                        raise ValueError("Asset not found")
                    if kind == "model" and not db.execute("SELECT 1 FROM models WHERE id=?", (str(state.get("model_id") or ""),)).fetchone():
                        raise ValueError("Model not found")
                    db.execute("INSERT INTO items VALUES(?,?,?,?,?,?)", (item_id, board_id, kind, asset_id, json.dumps(state), now()))
                    db.commit()
                return self.json({"id": item_id, "board_id": board_id, "kind": kind, "asset_id": asset_id, "state": state}, 201)
            return self.error("Not found", 404)
        except (ValueError, json.JSONDecodeError) as e:
            return self.error(str(e))

    def do_PUT(self):
        if not self.authorized():
            return self.error("Forbidden", 403)
        path = urlparse(self.path).path
        try:
            data = self.body()
            with LOCK, connection() as db:
                if path.startswith("/api/items/"):
                    item_id = path.rsplit("/", 1)[-1]
                    state = data.get("state")
                    if not isinstance(state, dict):
                        raise ValueError("Invalid item state")
                    db.execute("UPDATE items SET state=?,updated_at=? WHERE id=?", (json.dumps(state), now(), item_id))
                elif path.startswith("/api/assets/"):
                    asset_id = path.rsplit("/", 1)[-1]
                    tags = data.get("tags", [])
                    if not isinstance(tags, list):
                        raise ValueError("Tags must be a list")
                    db.execute("UPDATE assets SET tags=?,collection=?,favorite=?,title=? WHERE id=?",
                               (json.dumps([str(t)[:40] for t in tags[:30]]), str(data.get("collection") or "")[:100],
                                int(bool(data.get("favorite"))), str(data.get("title") or "")[:500], asset_id))
                    if "source" in data:
                        source = data["source"]
                        if not isinstance(source, dict):
                            raise ValueError("Invalid source details")
                        db.execute("UPDATE assets SET source_url=?,source_title=?,source_author=?,source_license=?,source_license_url=? WHERE id=?",
                                   (safe_http_url(source.get("url")), str(source.get("title") or "")[:500],
                                    str(source.get("author") or "")[:200], str(source.get("license") or "")[:200],
                                    safe_http_url(source.get("license_url")), asset_id))
                elif path.startswith("/api/boards/"):
                    board_id = path.rsplit("/", 1)[-1]
                    if "name" in data or "folder_id" in data:
                        current = db.execute("SELECT * FROM boards WHERE id=?", (board_id,)).fetchone()
                        if not current:
                            return self.error("Board not found", 404)
                        name = str(data.get("name",current["name"])).strip()[:80]
                        if not name:
                            raise ValueError("Board name is required")
                        if board_id == "main" and name != "Everything":
                            raise ValueError("Everything cannot be renamed")
                        folder_id = data.get("folder_id",current["folder_id"]) or None
                        if board_id == "main" and folder_id:
                            raise ValueError("Everything cannot be moved to a folder")
                        if folder_id and not db.execute("SELECT 1 FROM folders WHERE id=?", (folder_id,)).fetchone():
                            raise ValueError("Folder not found")
                        db.execute("UPDATE boards SET name=?,folder_id=?,updated_at=? WHERE id=?", (name,folder_id,now(),board_id))
                    else:
                        view = data.get("view")
                        if not isinstance(view, dict):
                            raise ValueError("Invalid view")
                        db.execute("UPDATE boards SET view=?,updated_at=? WHERE id=?", (json.dumps(view), now(), board_id))
                elif path.startswith("/api/folders/"):
                    folder_id = path.rsplit("/", 1)[-1]
                    name = str(data.get("name") or "").strip()[:80]
                    if not name:
                        raise ValueError("Folder name is required")
                    db.execute("UPDATE folders SET name=? WHERE id=?", (name,folder_id))
                else:
                    return self.error("Not found", 404)
                if not db.total_changes:
                    return self.error("Not found", 404)
                db.commit()
            return self.json({"ok": True})
        except (ValueError, json.JSONDecodeError) as e:
            return self.error(str(e))

    def do_DELETE(self):
        if not self.authorized():
            return self.error("Forbidden", 403)
        path = urlparse(self.path).path
        if path.startswith("/api/critique/invite/"):
            board_id = path.rsplit("/", 1)[-1]
            with LOCK, connection() as db:
                db.execute("DELETE FROM critique_invites WHERE board_id=?", (board_id,))
                db.commit()
            return self.json({"ok": True})
        if path == "/api/trash":
            with LOCK, connection() as db:
                records = db.execute("SELECT id,filename FROM assets WHERE trashed_at IS NOT NULL").fetchall()
                forget_local_visual_vectors([record["id"] for record in records])
                db.execute("DELETE FROM assets WHERE trashed_at IS NOT NULL")
                db.commit()
            for record in records:
                (ASSETS / record["filename"]).unlink(missing_ok=True)
            return self.json({"deleted":len(records)})
        removed_models = []
        with LOCK, connection() as db:
            if path.startswith("/api/items/"):
                db.execute("DELETE FROM items WHERE id=?", (path.rsplit("/", 1)[-1],))
            elif path.startswith("/api/assets/"):
                asset_id = path.rsplit("/", 1)[-1]
                if not db.execute("SELECT 1 FROM assets WHERE id=? AND trashed_at IS NULL", (asset_id,)).fetchone():
                    return self.error("Image not found", 404)
                saved = [row_dict(r) for r in db.execute("SELECT * FROM items WHERE asset_id=?", (asset_id,))]
                db.execute("UPDATE assets SET trashed_at=?,trash_items=? WHERE id=?", (now(),json.dumps(saved),asset_id))
                db.execute("DELETE FROM items WHERE asset_id=?", (asset_id,))
            elif path.startswith("/api/boards/") and not path.endswith("/main"):
                board_id = path.rsplit("/", 1)[-1]
                if not db.execute("SELECT 1 FROM boards WHERE id=?",(board_id,)).fetchone():
                    return self.error("Board not found",404)
                db.execute("DELETE FROM items WHERE board_id=?", (board_id,))
                db.execute("DELETE FROM critique_comments WHERE board_id=?", (board_id,))
                db.execute("DELETE FROM critique_invites WHERE board_id=?", (board_id,))
                db.execute("DELETE FROM boards WHERE id=?", (board_id,))
            elif path.startswith("/api/folders/"):
                folder_id = path.rsplit("/",1)[-1]
                db.execute("UPDATE boards SET folder_id=NULL WHERE folder_id=?",(folder_id,))
                db.execute("DELETE FROM folders WHERE id=?",(folder_id,))
            else:
                return self.error("Not found", 404)
            removed_models = remove_unused_models(db)
            db.commit()
        for filename in removed_models:
            (MODELS / filename).unlink(missing_ok=True)
        return self.json({"ok": True})


if __name__ == "__main__":
    refusal = hosting.check_bind(HOST)
    if refusal:
        print(refusal, file=sys.stderr, flush=True)
        raise SystemExit(2)
    hosting.init(DATA)
    enrichment.start()
    threading.Thread(target=analyze_existing_assets, daemon=True).start()
    if hosting.DEMO:
        reset_demo_data()  # every deploy opens on the sample boards, not on whatever the last visitors left
        threading.Thread(target=demo_reset_loop, daemon=True).start()
        print(f"Visual Brain (public demo, resets every {DEMO_RESET_HOURS:g} h): listening on {HOST}:{PORT}", flush=True)
    elif hosting.hosted():
        print(f"Visual Brain (owner sign-in): listening on {HOST}:{PORT}", flush=True)
    else:
        print(f"Visual Brain: http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
