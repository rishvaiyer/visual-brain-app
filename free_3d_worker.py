#!/usr/bin/env python3
"""Private Ryzen worker for free photo-to-3D generation.

Bind only to loopback. A protected Cloudflare Tunnel can forward one HTTPS route
to it after the owner approves that connection. No credentials live in source.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import subprocess
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

import free_3d

HOST = "127.0.0.1"
PORT = int(os.environ.get("VISUAL_BRAIN_WORKER_PORT", "8788"))


def load_worker_token() -> str:
    credential = os.environ.get("VISUAL_BRAIN_WORKER_TOKEN_FILE")
    if not credential and os.environ.get("CREDENTIALS_DIRECTORY"):
        credential = str(Path(os.environ["CREDENTIALS_DIRECTORY"]) / "worker-token")
    if credential:
        try:
            return Path(credential).read_text(encoding="utf-8").strip()
        except OSError:
            return ""  # A missing configured credential must not fall back to another token.
    return os.environ.get("VISUAL_BRAIN_WORKER_TOKEN", "")


TOKEN = load_worker_token()
CALLBACK_ORIGIN = os.environ.get("VISUAL_BRAIN_WORKER_CALLBACK_ORIGIN", "").rstrip("/")
INCOMING = free_3d.WORKER / "incoming"
RESULTS = free_3d.WORKER / "results"
MAX_IMAGE = 8 * 1024 * 1024
PHOTO_NAME = re.compile(r"photo_\d{3}\.jpg\Z")
MAX_QUEUED = 16
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="visual-brain-3d")
PENDING: set[str] = set()
PENDING_LOCK = threading.Lock()


def configured() -> bool:
    try:
        parsed = urlparse(CALLBACK_ORIGIN)
        secure = parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                                              and os.environ.get("VISUAL_BRAIN_WORKER_TEST_LOOPBACK") == "1")
        return (len(TOKEN) >= 32 and secure and bool(parsed.hostname)
                and not (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment)
                and free_3d.local_available())
    except ValueError:
        return False


def callback(model_id: str, body: bytes, mime: str) -> bool:
    try:
        response = requests.post(CALLBACK_ORIGIN + "/api/3d/callback/" + model_id,
                                 data=body, headers={"Content-Type": mime, "X-Visual-Brain-Worker-Token": TOKEN},
                                 timeout=(5, 45), allow_redirects=False)
        if response.status_code == 200:
            return True
        print(f"3D callback {model_id}: HTTP {response.status_code}", flush=True)
    except requests.RequestException:
        print(f"3D callback {model_id}: destination unavailable", flush=True)
    return False


def process_job(model_id: str) -> None:
    single = INCOMING / (model_id + ".jpg")
    multiple = INCOMING / (model_id + ".zip")
    source = multiple if multiple.is_file() else single
    result = RESULTS / (model_id + ".glb")
    try:
        if not result.is_file():
            if source == multiple:
                with tempfile.TemporaryDirectory(prefix="visual-brain-worker-multi-") as temporary:
                    images = Path(temporary)
                    with zipfile.ZipFile(source) as archive:
                        for name in archive.namelist():
                            (images / name).write_bytes(archive.read(name))
                    free_3d.generate_multi(sorted(images.glob("*.jpg")), result)
            else:
                free_3d.generate(source, result)
        sent = callback(model_id, result.read_bytes(), "model/gltf-binary")
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        print(f"3D job {model_id}: {type(error).__name__}", flush=True)
        message = (str(error) if source == multiple and isinstance(error, free_3d.MultiPhotoError)
                   else "The free 3D worker could not combine these photos." if source == multiple
                   else "The free 3D worker could not make this image.")
        sent = callback(model_id, json.dumps({"error": message}).encode(), "application/json")
    if sent:
        source.unlink(missing_ok=True)
        result.unlink(missing_ok=True)
    with PENDING_LOCK:
        PENDING.discard(model_id)


class Handler(BaseHTTPRequestHandler):
    server_version = "VisualBrain3D/1.0"

    def log_message(self, format_string, *args):
        # Job IDs are not credentials; requests still never log header values.
        print(format_string % args, flush=True)

    def json(self, value: dict, status: int) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            return self.json({"ok": configured()}, 200 if configured() else 503)
        return self.json({"error": "Not found"}, 404)

    def do_POST(self):
        if self.path != "/jobs":
            return self.json({"error": "Not found"}, 404)
        token = self.headers.get("X-Visual-Brain-Worker-Token", "")
        model_id = self.headers.get("X-Visual-Brain-Model-Id", "")
        if not configured() or not hmac.compare_digest(token, TOKEN) or not free_3d.MODEL_ID.fullmatch(model_id):
            return self.json({"error": "Forbidden"}, 403)
        multiple = self.headers.get("X-Visual-Brain-Job-Kind", "") == "multi"
        mime = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if multiple and (mime != "application/zip" or not free_3d.multi_local_available()):
            return self.json({"error": "Multi-photo 3D is unavailable"}, 503)
        if not multiple and mime != "image/jpeg":
            return self.json({"error": "Choose a JPEG image"}, 400)
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.json({"error": "Invalid image length"}, 400)
        if not 1 <= size <= (free_3d.MAX_MULTI_ARCHIVE if multiple else MAX_IMAGE):
            return self.json({"error": "Image is too large"}, 413)
        raw = self.rfile.read(size)
        if multiple:
            try:
                with zipfile.ZipFile(BytesIO(raw)) as archive:
                    names = archive.namelist()
                    if (not free_3d.MIN_MULTI_PHOTOS <= len(names) <= free_3d.MAX_MULTI_PHOTOS
                            or len(set(names)) != len(names) or any(not PHOTO_NAME.fullmatch(name) for name in names)
                            or sorted(names) != [f"photo_{index:03}.jpg" for index in range(len(names))]
                            or sum(info.file_size for info in archive.infolist()) > free_3d.MAX_MULTI_ARCHIVE):
                        raise ValueError("Invalid photo set")
                    for name in names:
                        info = archive.getinfo(name)
                        if info.file_size > 3 * 1024 * 1024:
                            raise ValueError("Photo is too large")
                        with Image.open(BytesIO(archive.read(name))) as image:
                            if image.width * image.height > 40_000_000:
                                raise ValueError("Photo is too large")
                            image.verify()
            except (OSError, ValueError, zipfile.BadZipFile):
                return self.json({"error": "These photos could not be read"}, 400)
        else:
            try:
                with Image.open(BytesIO(raw)) as image:
                    if image.width * image.height > 40_000_000:
                        return self.json({"error": "Image is too large"}, 413)
                    image.verify()
            except (OSError, ValueError):
                return self.json({"error": "Image could not be read"}, 400)
        with PENDING_LOCK:
            if model_id in PENDING:
                return self.json({"error": "Job already running"}, 409)
            if len(PENDING) >= MAX_QUEUED:
                return self.json({"error": "Worker is full"}, 429)
            PENDING.add(model_id)
        try:
            INCOMING.mkdir(parents=True, exist_ok=True)
            RESULTS.mkdir(parents=True, exist_ok=True)
            (INCOMING / (model_id + (".zip" if multiple else ".jpg"))).write_bytes(raw)
            EXECUTOR.submit(process_job, model_id)
        except OSError:
            with PENDING_LOCK:
                PENDING.discard(model_id)
            return self.json({"error": "Worker could not store this job"}, 500)
        return self.json({"status": "queued"}, 202)


if __name__ == "__main__":
    if not configured():
        raise SystemExit("Set worker token and HTTPS callback origin, and install the local CPU worker first")
    INCOMING.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    for source in list(INCOMING.glob("model_*.jpg")) + list(INCOMING.glob("model_*.zip")):
        model_id = source.stem
        if free_3d.MODEL_ID.fullmatch(model_id):
            with PENDING_LOCK:
                if len(PENDING) >= MAX_QUEUED:
                    break
                PENDING.add(model_id)
            EXECUTOR.submit(process_job, model_id)
    print(f"Private 3D worker listening on {HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
