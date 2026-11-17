"""One photo -> one textured GLB, on the CPU, inside the app.

This is the always-on 3D engine. It needs no worker, no account and no network:
an open depth model (Depth Anything V2 Small, Apache-2.0) estimates how far each
part of the picture is, and the picture is pressed into that shape.

What it honestly makes:
  * object mode: when one thing stands clear of its background, the thing is cut
    out and closed into a solid. The front follows the photo. The back is a
    mirrored guess, because one photo never shows it.
  * scene mode: otherwise the whole picture becomes a relief tile.

The server runs this file as a separate process, so the model's memory is
returned when the job ends and a bad image cannot take the board down.

    python3 free_3d_relief.py --install            # fetch and verify the model
    python3 free_3d_relief.py photo.jpg out.glb    # make one model
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
from collections import deque
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL = Path(os.environ.get("VISUAL_BRAIN_RELIEF_MODEL")
             or ROOT / "relief-model" / "depth_anything_v2_small.onnx")
MODEL_REVISION = "4472b7362082ad9968fee890ca0f1e5aca36b93d"
MODEL_URL = ("https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/"
             + MODEL_REVISION + "/onnx/model.onnx")
MODEL_SHA256 = "afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c"
MODEL_BYTES = 99_060_839

GRID = 224            # mesh vertices along the longer side
DEPTH_SIDE = 518      # model input, longer side, a multiple of 14
TEXTURE_SIDE = 1024
MAX_PIXELS = 40_000_000
TIMEOUT = 180
ERROR_MARKER = "VISUAL_BRAIN_RELIEF_ERROR:"


class ReliefError(RuntimeError):
    """A picture problem that is safe to show to the person who submitted it."""


def available() -> bool:
    """True when this instance can make a model by itself, right now."""
    if os.environ.get("VISUAL_BRAIN_RELIEF") == "0":
        return False
    try:
        return (MODEL.is_file() and MODEL.stat().st_size == MODEL_BYTES
                and importlib.util.find_spec("numpy") is not None
                and importlib.util.find_spec("onnxruntime") is not None)
    except (OSError, ValueError):
        return False


def generate(source: Path, output: Path) -> str:
    """Make one GLB in a child process. Returns "object" or "scene"."""
    if not available():
        raise RuntimeError("Built-in 3D is not installed on this instance")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="visual-brain-relief-") as temporary:
        staged = Path(temporary) / "model.glb"
        env = os.environ.copy()
        env["OMP_NUM_THREADS"] = "4"
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(source), str(staged)],
                                    env=env, capture_output=True, text=True, timeout=TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Built-in 3D took too long on this picture. Try a smaller one.") from None
        if result.returncode or not staged.is_file():
            lines = (result.stderr or "").splitlines()
            message = next((line[len(ERROR_MARKER):].strip() for line in reversed(lines)
                            if line.startswith(ERROR_MARKER)), "")
            raise RuntimeError(message[:220] or "Built-in 3D could not read this picture")
        raw = staged.read_bytes()
        mode = "object" if result.stdout.strip().endswith("object") else "scene"
    output.write_bytes(raw)
    return mode


def install(destination: Path = MODEL) -> None:
    """Download the pinned model once and refuse anything that is not that exact file."""
    import requests

    if destination.is_file() and _sha256(destination) == MODEL_SHA256:
        print(f"Model already installed at {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".part")
    digest = hashlib.sha256()
    size = 0
    with requests.get(MODEL_URL, stream=True, timeout=(10, 120)) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_content(1 << 20):
                size += len(chunk)
                if size > MODEL_BYTES:
                    break
                digest.update(chunk)
                handle.write(chunk)
    if size != MODEL_BYTES or digest.hexdigest() != MODEL_SHA256:
        partial.unlink(missing_ok=True)
        raise SystemExit("Downloaded model does not match the pinned checksum. Nothing was installed.")
    partial.replace(destination)
    print(f"Installed {destination} ({size} bytes, sha256 verified)")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Everything below runs in the child process. numpy and onnxruntime are only
# imported there, so the board server itself never loads them.
# ---------------------------------------------------------------------------

def load_picture(source: Path):
    from PIL import Image, ImageOps

    try:
        with Image.open(source) as original:
            if original.width * original.height > MAX_PIXELS:
                raise ReliefError("This picture is too large for 3D")
            image = ImageOps.exif_transpose(original)
            if image.mode in ("RGBA", "LA", "P"):
                rgba = image.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (255, 255, 255))
                flat.paste(rgba, mask=rgba.getchannel("A"))
                image = flat
            else:
                image = image.convert("RGB")
    except ReliefError:
        raise
    except Exception:
        raise ReliefError("This picture could not be opened for 3D") from None
    if min(image.size) < 32:
        raise ReliefError("This picture is too small for 3D")
    if max(image.size) / min(image.size) > 6:
        raise ReliefError("This picture is too long and thin for 3D")
    return image


def estimate_depth(image):
    """Relative nearness per pixel: larger means closer to the camera."""
    import numpy as np
    import onnxruntime as ort
    from PIL import Image

    scale = DEPTH_SIDE / max(image.size)
    width = max(14, round(image.width * scale / 14) * 14)
    height = max(14, round(image.height * scale / 14) * 14)
    pixels = np.asarray(image.resize((width, height), Image.Resampling.BICUBIC), dtype=np.float32) / 255.0
    pixels = (pixels - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array([0.229, 0.224, 0.225], dtype=np.float32)
    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    session = ort.InferenceSession(str(MODEL), sess_options=options, providers=["CPUExecutionProvider"])
    depth = session.run(None, {session.get_inputs()[0].name: pixels.transpose(2, 0, 1)[None]})[0]
    depth = np.asarray(depth, dtype=np.float32).reshape(depth.shape[-2], depth.shape[-1])
    if not np.isfinite(depth).all():
        raise ReliefError("Depth could not be estimated for this picture")
    return depth


def _resize(values, width: int, height: int):
    import numpy as np
    from PIL import Image

    return np.asarray(Image.fromarray(np.ascontiguousarray(values, dtype=np.float32))
                      .resize((width, height), Image.Resampling.BILINEAR), dtype=np.float32)


def _blur(values, passes: int = 1):
    import numpy as np

    for _ in range(passes):
        padded = np.pad(values, 1, mode="edge")
        values = (padded[:-2, :-2] + padded[:-2, 1:-1] + padded[:-2, 2:] + padded[1:-1, :-2] + padded[1:-1, 1:-1]
                  + padded[1:-1, 2:] + padded[2:, :-2] + padded[2:, 1:-1] + padded[2:, 2:]) / 9.0
    return values


def _components(mask):
    """Label 4-connected regions of a small boolean grid. Returns labels and sizes."""
    import numpy as np

    height, width = mask.shape
    flat = mask.ravel()
    labels = np.zeros(flat.size, dtype=np.int32)
    sizes = [0]
    for start in np.flatnonzero(flat):
        if labels[start]:
            continue
        label = len(sizes)
        labels[start] = label
        queue = deque([int(start)])
        count = 0
        while queue:
            cell = queue.popleft()
            count += 1
            column = cell % width
            for neighbor, valid in ((cell - width, cell >= width), (cell + width, cell + width < flat.size),
                                    (cell - 1, column > 0), (cell + 1, column < width - 1)):
                if valid and flat[neighbor] and not labels[neighbor]:
                    labels[neighbor] = label
                    queue.append(neighbor)
        sizes.append(count)
    return labels.reshape(height, width), sizes


def _border(values):
    import numpy as np

    return np.concatenate([values[0], values[-1], values[1:-1, 0], values[1:-1, -1]])


def find_object(colors, nearness, known=None):
    """The thing photographed on a plain backdrop, as (mask, backdrop, tolerance), or None.

    Depth alone cannot find the object: a plain backdrop still gets a sloping
    depth. Colour can. The backdrop is whatever matches the picture's edge and
    is connected to it, so a white mug on white keeps its white middle.
    Pass a known (backdrop, tolerance) to look again inside a tighter crop.
    """
    import numpy as np

    rows, columns = nearness.shape
    if known is None:
        ring = np.zeros((rows, columns), dtype=bool)
        width = max(2, round(0.03 * min(rows, columns)))
        ring[:width] = ring[-width:] = True
        ring[:, :width] = ring[:, -width:] = True
        backdrop = np.median(colors[ring], axis=0)
        difference = np.sqrt(((colors - backdrop) ** 2).sum(axis=2))
        spread = float(np.percentile(difference[ring], 92))
        if spread > 30.0:
            return None  # the edge of the picture is not one plain colour
        tolerance = max(26.0, 2.4 * spread)
    else:
        backdrop, tolerance = known
        difference = np.sqrt(((colors - backdrop) ** 2).sum(axis=2))
    similar = difference < tolerance
    labels, sizes = _components(similar)
    outside = np.isin(labels, np.unique(_border(labels)[_border(similar)]))
    mask = ~outside
    if not mask.any() or not outside.any():
        return None

    # An enclosed backdrop-coloured patch is an opening (a handle, a gap) when it
    # sits as far away as the backdrop does, and part of the object otherwise.
    far, close = float(np.median(nearness[outside])), float(np.median(nearness[mask & ~similar])) \
        if (mask & ~similar).any() else 1.0
    for label in np.unique(labels[mask & similar]):
        patch = labels == label
        if sizes[label] >= 0.003 * mask.size and abs(float(np.median(nearness[patch])) - far) < abs(
                float(np.median(nearness[patch])) - close):
            mask &= ~patch

    # One thing, not a collage or a poster: most of what is left must be one piece.
    # Then shave one cell so no backdrop colour fringes the edge.
    labels, sizes = _components(mask)
    if len(sizes) < 2 or max(sizes) < 0.78 * mask.sum():
        return None
    mask = labels == int(np.argmax(sizes))
    padded = np.pad(mask, 1, mode="constant", constant_values=False)
    mask = mask & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
    if not mask.any():
        return None
    if known is None and (not 0.03 <= mask.mean() <= 0.85 or _border(mask).mean() > 0.2):
        return None
    return mask, backdrop, tolerance


def _edge_distance(mask, reach: int):
    """How many cells each cell sits inside the mask edge, capped at reach."""
    import numpy as np

    distance = np.zeros(mask.shape, dtype=np.float32)
    current = mask.copy()
    for _ in range(reach):
        distance += current
        padded = np.pad(current, 1, mode="constant", constant_values=False)
        current = current & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
        if not current.any():
            break
    return distance


def _prepare(image, depth):
    """Lay the mesh grid over a picture: nearness (0 far .. 1 near) and colour per vertex."""
    import numpy as np
    from PIL import Image

    scale = GRID / max(image.size)
    columns = max(16, round(image.width * scale))
    rows = max(16, round(image.height * scale))
    near = _blur(_resize(depth, columns, rows), 2)
    low, high = np.percentile(near, [1.0, 99.0])
    near = np.full_like(near, 0.5) if high - low < 1e-6 else np.clip((near - low) / (high - low), 0.0, 1.0)
    colors = np.asarray(image.resize((columns, rows), Image.Resampling.BOX), dtype=np.float32)
    return columns, rows, near, colors


def build_mesh(image, depth):
    """Turn a picture and its depth into (mode, mesh parts, the picture actually used)."""
    import numpy as np

    columns, rows, near, colors = _prepare(image, depth)
    found = find_object(colors, near)
    mode = "object" if found is not None else "scene"
    if found is None:
        mask = np.ones((rows, columns), dtype=bool)
    else:
        mask, backdrop, tolerance = found
        # A small object would get few vertices. Frame it tightly and look again.
        occupied_rows, occupied_columns = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
        left, right = occupied_columns[0] / columns, (occupied_columns[-1] + 1) / columns
        top, bottom = occupied_rows[0] / rows, (occupied_rows[-1] + 1) / rows
        if max(right - left, bottom - top) < 0.8:
            margin_x, margin_y = 0.09 * (right - left), 0.09 * (bottom - top)
            left, right = max(0.0, left - margin_x), min(1.0, right + margin_x)
            top, bottom = max(0.0, top - margin_y), min(1.0, bottom + margin_y)
            box = (round(left * image.width), round(top * image.height),
                   round(right * image.width), round(bottom * image.height))
            depth_rows, depth_columns = depth.shape
            closer = image.crop(box)
            closer_depth = depth[int(top * depth_rows):max(int(top * depth_rows) + 2, round(bottom * depth_rows)),
                                 int(left * depth_columns):max(int(left * depth_columns) + 2, round(right * depth_columns))]
            if min(closer.size) >= 32:
                closer_columns, closer_rows, closer_near, closer_colors = _prepare(closer, closer_depth)
                refined = find_object(closer_colors, closer_near, known=(backdrop, tolerance))
                if refined is not None:
                    image, columns, rows, near, mask = closer, closer_columns, closer_rows, closer_near, refined[0]

    column_index, row_index = np.meshgrid(np.arange(columns, dtype=np.float32), np.arange(rows, dtype=np.float32))
    u = column_index / (columns - 1)
    v = row_index / (rows - 1)
    x = (u - 0.5) * image.width
    y = (0.5 - v) * image.height

    if mode == "object":
        inside = near[mask]
        floor, ceiling = float(inside.min()), float(np.percentile(inside, 99.5))
        relief = np.clip((near - floor) / max(ceiling - floor, 1e-6), 0.0, 1.0)
        occupied_rows, occupied_columns = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
        extent_x = (occupied_columns[-1] - occupied_columns[0] + 1) / columns * image.width
        extent_y = (occupied_rows[-1] - occupied_rows[0] + 1) / rows * image.height
        body = min(extent_x, extent_y)
        reach = max(3, round(0.09 * min(occupied_columns.size, occupied_rows.size)))
        rim = np.clip(_edge_distance(mask, reach) / reach, 0.0, 1.0)
        rounded = np.sqrt(1.0 - (1.0 - rim) ** 2)          # a quarter-circle edge profile
        front = body * rounded * (0.09 + 0.19 * relief)
        back = -body * rounded * (0.09 + 0.05 * _blur(relief, 3))
    else:
        body = min(image.width, image.height)
        front = body * 0.30 * near
        back = np.full_like(front, -body * 0.04)

    # A grid cell is kept when all four of its corners are part of the shape.
    cell = mask[:-1, :-1] & mask[:-1, 1:] & mask[1:, :-1] & mask[1:, 1:]
    if cell.sum() < 24:
        raise ReliefError("No clear shape was found in this picture")
    used = np.zeros_like(mask)
    used[:-1, :-1] |= cell
    used[:-1, 1:] |= cell
    used[1:, :-1] |= cell
    used[1:, 1:] |= cell
    count = int(used.sum())
    index = np.full(mask.shape, -1, dtype=np.int64)
    index[used] = np.arange(count)

    top_left, top_right = index[:-1, :-1][cell], index[:-1, 1:][cell]
    bottom_left, bottom_right = index[1:, :-1][cell], index[1:, 1:][cell]
    front_faces = np.concatenate([np.stack([top_left, bottom_left, bottom_right], axis=1),
                                  np.stack([top_left, bottom_right, top_right], axis=1)])
    back_faces = front_faces[:, ::-1] + count

    # Walls join the front to the back along every open edge, wound to face outward.
    padded = np.pad(cell, 1, mode="constant", constant_values=False)
    walls = []
    for open_edge, start, end in (
            (cell & ~padded[:-2, 1:-1], index[:-1, :-1], index[:-1, 1:]),    # nothing above
            (cell & ~padded[2:, 1:-1], index[1:, 1:], index[1:, :-1]),       # nothing below
            (cell & ~padded[1:-1, :-2], index[1:, :-1], index[:-1, :-1]),    # nothing to the left
            (cell & ~padded[1:-1, 2:], index[:-1, 1:], index[1:, 1:])):      # nothing to the right
        a, b = start[open_edge], end[open_edge]
        walls.append(np.stack([a, b, b + count], axis=1))
        walls.append(np.stack([a, b + count, a + count], axis=1))
    wall_faces = np.concatenate(walls)

    flat_x, flat_y = x[used], y[used]
    positions = np.concatenate([np.stack([flat_x, flat_y, front[used]], axis=1),
                                np.stack([flat_x, flat_y, back[used]], axis=1)]).astype(np.float32)
    uv = np.stack([u[used], v[used]], axis=1).astype(np.float32)
    uv = np.concatenate([uv, uv])

    if mode == "scene":
        # A tile's back is flat, so a fan from its middle replaces a full second grid.
        outline = np.concatenate([index[0, :-1], index[:-1, -1], index[-1, :0:-1], index[:0:-1, 0]]) + count
        middle = np.full(outline.size, 2 * count)
        back_faces = np.stack([middle, outline, np.roll(outline, -1)], axis=1)
        positions = np.concatenate([positions, np.array([[0.0, 0.0, float(back[0, 0])]], dtype=np.float32)])

    # Centre the shape and make its longest side one unit, so every model opens framed.
    low_corner, high_corner = positions.min(axis=0), positions.max(axis=0)
    positions = (positions - (low_corner + high_corner) / 2) / float((high_corner - low_corner).max())

    if mode == "object":
        parts = [(positions, uv, np.concatenate([front_faces, back_faces, wall_faces]))]
    else:
        shell_positions, shell_faces = _compact(positions, np.concatenate([back_faces, wall_faces]))
        parts = [(positions[:count], uv[:count], front_faces), (shell_positions, None, shell_faces)]
    return mode, [(part_positions, _normals(part_positions, faces), part_uv, faces.astype(np.uint32))
                  for part_positions, part_uv, faces in parts], image


def _compact(positions, faces):
    import numpy as np

    kept, remapped = np.unique(faces, return_inverse=True)
    return positions[kept], remapped.reshape(faces.shape)


def _normals(positions, faces):
    import numpy as np

    corners = positions[faces]
    face_normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals = np.zeros_like(positions)
    for corner in range(3):
        np.add.at(normals, faces[:, corner], face_normals)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.where(length > 1e-12, normals / np.maximum(length, 1e-12), np.array([0.0, 0.0, 1.0]))
    return normals.astype(np.float32)


def signed_volume(parts) -> float:
    """Positive when the closed surface faces outward."""
    import numpy as np

    total = 0.0
    for positions, _, _, faces in parts:
        corners = positions[faces].astype(np.float64)
        total += float(np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])).sum()) / 6.0
    return total


def write_glb(image, mode: str, parts) -> bytes:
    import numpy as np
    from PIL import Image

    texture = image.copy()
    texture.thumbnail((TEXTURE_SIDE, TEXTURE_SIDE), Image.Resampling.LANCZOS)
    jpeg = BytesIO()
    texture.save(jpeg, "JPEG", quality=88, optimize=True)
    average = np.asarray(texture.resize((1, 1), Image.Resampling.BOX), dtype=np.float32).reshape(3) / 255.0
    shade = [round(float(channel) ** 2.2 * 0.55 + 0.05, 4) for channel in average]

    binary = bytearray()
    views, accessors, primitives = [], [], []

    def add_view(raw: bytes, target: int | None = None) -> int:
        while len(binary) % 4:
            binary.append(0)
        view = {"buffer": 0, "byteOffset": len(binary), "byteLength": len(raw)}
        if target:
            view["target"] = target
        binary.extend(raw)
        views.append(view)
        return len(views) - 1

    def add_accessor(values, kind: str, component: int, target: int, bounds: bool = False) -> int:
        accessor = {"bufferView": add_view(values.tobytes(), target), "componentType": component,
                    "count": int(values.shape[0] if kind != "SCALAR" else values.size), "type": kind}
        if bounds:
            accessor["min"] = [float(value) for value in values.min(axis=0)]
            accessor["max"] = [float(value) for value in values.max(axis=0)]
        accessors.append(accessor)
        return len(accessors) - 1

    for positions, normals, uv, faces in parts:
        attributes = {"POSITION": add_accessor(positions, "VEC3", 5126, 34962, bounds=True),
                      "NORMAL": add_accessor(normals, "VEC3", 5126, 34962)}
        if uv is not None:
            attributes["TEXCOORD_0"] = add_accessor(uv, "VEC2", 5126, 34962)
        primitives.append({"attributes": attributes, "material": 0 if uv is not None else 1, "mode": 4,
                           "indices": add_accessor(faces.reshape(-1), "SCALAR", 5125, 34963)})

    image_view = add_view(jpeg.getvalue())
    while len(binary) % 4:
        binary.append(0)
    scene = {
        "asset": {"version": "2.0", "generator": "Visual Brain built-in 3D",
                  "extras": {"engine": "relief", "mode": mode, "depth_model": "Depth Anything V2 Small"}},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "Object"}],
        "meshes": [{"primitives": primitives}],
        "materials": [
            {"name": "Photo", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicFactor": 0.0,
                                                       "roughnessFactor": 0.85}},
            {"name": "Back", "pbrMetallicRoughness": {"baseColorFactor": shade + [1.0], "metallicFactor": 0.0,
                                                      "roughnessFactor": 0.95}}],
        "textures": [{"source": 0, "sampler": 0}],
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}],
        "images": [{"bufferView": image_view, "mimeType": "image/jpeg"}],
        "bufferViews": views, "accessors": accessors, "buffers": [{"byteLength": len(binary)}],
    }
    if len(primitives) == 1:
        scene["materials"].pop()
    encoded = json.dumps(scene, separators=(",", ":")).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    length = 12 + 8 + len(encoded) + 8 + len(binary)
    return b"".join([struct.pack("<4sII", b"glTF", 2, length), struct.pack("<II", len(encoded), 0x4E4F534A), encoded,
                     struct.pack("<II", len(binary), 0x004E4942), bytes(binary)])


def make(source: Path) -> tuple[str, bytes]:
    image = load_picture(source)
    mode, parts, used = build_mesh(image, estimate_depth(image))
    return mode, write_glb(used, mode, parts)


def main(arguments: list[str]) -> int:
    if arguments == ["--install"]:
        install()
        return 0
    if len(arguments) != 2:
        print("usage: free_3d_relief.py --install | free_3d_relief.py SOURCE OUTPUT.glb", file=sys.stderr)
        return 2
    try:
        mode, raw = make(Path(arguments[0]))
    except ReliefError as error:
        print(ERROR_MARKER + str(error), file=sys.stderr)
        return 1
    Path(arguments[1]).write_bytes(raw)
    print(mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
