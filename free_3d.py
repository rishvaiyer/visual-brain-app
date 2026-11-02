"""Validate and generate GLB models using a free local TripoSR installation."""

from __future__ import annotations

import json
import math
import os
import re
import struct
import subprocess
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
WORKER = Path(os.environ.get("VISUAL_BRAIN_3D_WORKER_DIR", str(ROOT / ".local-3d-worker")))
MAX_GLB = 40 * 1024 * 1024
MIN_MULTI_PHOTOS = 32
MAX_MULTI_PHOTOS = 80
MAX_MULTI_ARCHIVE = 64 * 1024 * 1024
MODEL_ID = re.compile(r"model_[0-9a-f]{12}\Z")
MULTI_ERROR_MARKER = "VISUAL_BRAIN_MULTI_ERROR:"


class MultiPhotoError(RuntimeError):
    """A photo-set problem safe to show to the person who submitted it."""


FULL_CROP = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}


def normalized_crop(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return FULL_CROP.copy()
    try:
        crop = {key: float(value[key]) for key in FULL_CROP}
    except (KeyError, TypeError, ValueError):
        return FULL_CROP.copy()
    x, y, width, height = (crop[key] for key in FULL_CROP)
    if (not all(math.isfinite(number) for number in crop.values()) or x < 0 or y < 0
            or width <= 0 or height <= 0 or x + width > 1.000001 or y + height > 1.000001):
        return FULL_CROP.copy()
    return crop


def cropped_source(source: Path, destination: Path, crop: object) -> Path:
    """Use the same image region selected on the board for 3D generation."""
    selected = normalized_crop(crop)
    if selected == FULL_CROP:
        return source
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original)
        width, height = image.size
        left = round(selected["x"] * width)
        top = round(selected["y"] * height)
        right = round((selected["x"] + selected["w"]) * width)
        bottom = round((selected["y"] + selected["h"]) * height)
        if right - left < 16 or bottom - top < 16:
            raise ValueError("The selected picture crop is too small for 3D")
        image.crop((left, top, right, bottom)).convert("RGB").save(destination, "JPEG", quality=92)
    return destination


def local_available() -> bool:
    return (WORKER / ".ready").is_file() and (WORKER / ".venv/bin/python").is_file() and (WORKER / "TripoSR/run.py").is_file()


def remote_available() -> bool:
    origin = os.environ.get("VISUAL_BRAIN_3D_REMOTE_ORIGIN", "").rstrip("/")
    token = os.environ.get("VISUAL_BRAIN_3D_REMOTE_TOKEN", "")
    client_id = os.environ.get("VISUAL_BRAIN_3D_CF_CLIENT_ID", "")
    client_secret = os.environ.get("VISUAL_BRAIN_3D_CF_CLIENT_SECRET", "")
    if len(token) < 32 or not origin:
        return False
    try:
        parsed = urlparse(origin)
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or not parsed.hostname:
            return False
        if parsed.scheme == "https":
            return bool(client_id and client_secret)
        return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"} and os.environ.get("VISUAL_BRAIN_3D_REMOTE_TEST_LOOPBACK") == "1"
    except ValueError:
        return False


def worker_available() -> bool:
    return local_available() or remote_available()


def multi_local_available() -> bool:
    bin_dir = Path(os.environ.get("VISUAL_BRAIN_OPENMVS_BIN", str(WORKER / "openmvs")))
    return (local_available() and (WORKER / ".multi-ready").is_file()
            and (WORKER / "multi-venv/bin/python").is_file()
            and (ROOT / "free_3d_multi.py").is_file()
            and all((bin_dir / name).is_file() for name in
                    ("InterfaceCOLMAP", "DensifyPointCloud", "ReconstructMesh", "TextureMesh")))


def multi_worker_available() -> bool:
    return multi_local_available() or (remote_available() and os.environ.get("VISUAL_BRAIN_3D_REMOTE_MULTI") == "1")


def prepared_multi_source(source: Path, destination: Path) -> Path:
    """Keep the complete, oriented photo and camera metadata for alignment."""
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original)
        if image.width * image.height > 40_000_000:
            raise ValueError("One of the photos is too large for 3D")
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        exif = image.getexif()
        if exif:
            exif[274] = 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.convert("RGB").save(destination, "JPEG", quality=90, optimize=True,
                                   exif=exif.tobytes() if exif else b"")
    return destination


def multi_archive(sources: list[Path]) -> bytes:
    if not MIN_MULTI_PHOTOS <= len(sources) <= MAX_MULTI_PHOTOS:
        raise ValueError(f"Choose {MIN_MULTI_PHOTOS} to {MAX_MULTI_PHOTOS} photos of one object")
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for index, source in enumerate(sources):
            raw = source.read_bytes()
            if len(raw) > 3 * 1024 * 1024:
                raise ValueError("One of the photos is too large for the free 3D worker")
            archive.writestr(f"photo_{index:03}.jpg", raw)
            if output.tell() > MAX_MULTI_ARCHIVE:
                raise ValueError("These photos are too large for one 3D job")
    return output.getvalue()


def generate_multi(sources: list[Path], output: Path) -> None:
    if not multi_local_available():
        raise RuntimeError("The free multi-photo worker is not installed")
    bin_dir = Path(os.environ.get("VISUAL_BRAIN_OPENMVS_BIN", str(WORKER / "openmvs")))
    with tempfile.TemporaryDirectory(prefix="visual-brain-multi-input-") as temporary:
        images = Path(temporary)
        for index, source in enumerate(sources):
            (images / f"photo_{index:03}.jpg").write_bytes(source.read_bytes())
        env = os.environ.copy()
        env["U2NET_HOME"] = str(WORKER / "model-cache")
        env["OMP_NUM_THREADS"] = "4"
        env["OPENBLAS_NUM_THREADS"] = "2"
        command = [str(WORKER / "multi-venv/bin/python"), str(ROOT / "free_3d_multi.py"),
                   str(images), str(output), "--openmvs-bin", str(bin_dir)]
        result = subprocess.run(command, env=env, capture_output=True, text=True,
                                timeout=2400, check=False)
        if result.returncode:
            lines = (result.stderr or result.stdout).splitlines()
            message = next((line[len(MULTI_ERROR_MARKER):].strip() for line in reversed(lines)
                            if line.startswith(MULTI_ERROR_MARKER)),
                           "The photos could not form one 3D object. Try clearer views around the full object.")
            raise MultiPhotoError(message[:220])
    validate_glb(output.read_bytes())


def dispatch_remote_multi(model_id: str, sources: list[Path]) -> None:
    if not remote_available() or not MODEL_ID.fullmatch(model_id) or os.environ.get("VISUAL_BRAIN_3D_REMOTE_MULTI") != "1":
        raise RuntimeError("The private multi-photo worker is not configured")
    raw = multi_archive(sources)
    headers = {"Content-Type": "application/zip", "X-Visual-Brain-Worker-Token": os.environ["VISUAL_BRAIN_3D_REMOTE_TOKEN"],
               "X-Visual-Brain-Model-Id": model_id, "X-Visual-Brain-Job-Kind": "multi"}
    if os.environ.get("VISUAL_BRAIN_3D_CF_CLIENT_ID"):
        headers["CF-Access-Client-Id"] = os.environ["VISUAL_BRAIN_3D_CF_CLIENT_ID"]
        headers["CF-Access-Client-Secret"] = os.environ["VISUAL_BRAIN_3D_CF_CLIENT_SECRET"]
    try:
        response = requests.post(os.environ["VISUAL_BRAIN_3D_REMOTE_ORIGIN"].rstrip("/") + "/jobs",
                                 data=raw, headers=headers, timeout=(5, 60), allow_redirects=False)
    except requests.RequestException as error:
        raise RuntimeError("The private 3D worker could not be reached") from error
    if response.status_code != 202:
        raise RuntimeError(f"The private 3D worker refused these photos ({response.status_code})")


def dispatch_remote(model_id: str, source: Path) -> None:
    """Send one reduced source image; the Ryzen worker calls back with the GLB."""
    if not remote_available() or not MODEL_ID.fullmatch(model_id):
        raise RuntimeError("The private 3D worker is not configured")
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        image = image.convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=90, optimize=True)
    headers = {"Content-Type": "image/jpeg", "X-Visual-Brain-Worker-Token": os.environ["VISUAL_BRAIN_3D_REMOTE_TOKEN"],
               "X-Visual-Brain-Model-Id": model_id}
    if os.environ.get("VISUAL_BRAIN_3D_CF_CLIENT_ID"):
        headers["CF-Access-Client-Id"] = os.environ["VISUAL_BRAIN_3D_CF_CLIENT_ID"]
        headers["CF-Access-Client-Secret"] = os.environ["VISUAL_BRAIN_3D_CF_CLIENT_SECRET"]
    try:
        response = requests.post(os.environ["VISUAL_BRAIN_3D_REMOTE_ORIGIN"].rstrip("/") + "/jobs",
                                 data=output.getvalue(), headers=headers, timeout=(5, 25), allow_redirects=False)
    except requests.RequestException as error:
        raise RuntimeError("The private 3D worker could not be reached") from error
    if response.status_code != 202:
        raise RuntimeError(f"The private 3D worker refused this image ({response.status_code})")


def validate_glb(raw: bytes) -> None:
    if not 20 <= len(raw) <= MAX_GLB or raw[:4] != b"glTF":
        raise ValueError("Choose a GLB model under 40 MB")
    version, length = struct.unpack_from("<II", raw, 4)
    if version != 2 or length != len(raw):
        raise ValueError("GLB file is incomplete or uses an unsupported version")
    json_length, chunk_type = struct.unpack_from("<II", raw, 12)
    if chunk_type != 0x4E4F534A or json_length > len(raw) - 20:
        raise ValueError("GLB file has no valid scene")
    try:
        scene = json.loads(raw[20:20 + json_length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("GLB scene could not be read") from None
    if not scene.get("meshes") or not scene.get("scenes"):
        raise ValueError("GLB has no 3D object")
    for entry in scene.get("buffers", []) + scene.get("images", []):
        uri = entry.get("uri", "")
        if uri and not uri.startswith("data:"):
            raise ValueError("GLB must include its own textures and data")


def generate(source: Path, output: Path) -> None:
    """One source photo -> one mesh. Run only on a machine with the local worker."""
    if not local_available():
        raise RuntimeError("Free 3D worker is not installed on this instance")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="visual-brain-3d-") as temporary:
        destination = Path(temporary)
        command = [str(WORKER / ".venv/bin/python"), str(WORKER / "TripoSR/run.py"),
                   str(source), "--device", "cpu", "--model-save-format", "glb",
                   "--mc-resolution", "256", "--chunk-size", "2048", "--output-dir", str(destination)]
        env = os.environ.copy()
        env["OMP_NUM_THREADS"] = "4"
        env["OPENBLAS_NUM_THREADS"] = "4"
        env["HF_HOME"] = str(WORKER / "model-cache")
        result = subprocess.run(command, cwd=WORKER / "TripoSR", env=env,
                                capture_output=True, text=True, timeout=1800, check=False)
        if result.returncode:
            error = (result.stderr or result.stdout).splitlines()[-1:] or ["Unknown model error"]
            raise RuntimeError("Free 3D worker failed: " + error[0][:180])
        model = destination / "0/mesh.glb"
        if not model.is_file():
            raise RuntimeError("Free 3D worker returned no model")
        raw = model.read_bytes()
        validate_glb(raw)
        output.write_bytes(raw)
