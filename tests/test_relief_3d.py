"""The built-in 3D engine: mesh rules without the model, then the real path over HTTP."""

import base64
import importlib.util
import json
import os
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import requests
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import free_3d
import free_3d_relief

HAS_NUMPY = importlib.util.find_spec("numpy") is not None


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def glb_scene(raw: bytes) -> dict:
    return json.loads(raw[20:20 + struct.unpack_from("<I", raw, 12)[0]])


@unittest.skipUnless(HAS_NUMPY, "numpy is not installed")
class ReliefMesh(unittest.TestCase):
    """Shape rules, checked with a made-up depth so no model is needed."""

    def disc(self, size=(320, 240), center=(150, 120), radius=70):
        import numpy as np

        image = Image.new("RGB", size, (250, 250, 250))
        ImageDraw.Draw(image).ellipse([center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius],
                                      fill=(200, 60, 40))
        yy, xx = np.mgrid[0:size[1], 0:size[0]].astype("float32")
        distance = np.sqrt((xx - center[0]) ** 2 + (yy - center[1]) ** 2)
        # A dome over the disc, sitting on a sloping floor like real depth of a plain backdrop.
        depth = np.clip(1 - (distance / radius) ** 2, 0, None) * 2 + yy / size[1]
        return image, depth

    def edges_closed(self, parts) -> bool:
        import numpy as np

        positions = np.concatenate([part[0] for part in parts])
        offset, faces = 0, []
        for part in parts:
            faces.append(part[3].astype("int64") + offset)
            offset += len(part[0])
        faces = np.concatenate(faces)
        # Parts may duplicate vertices on a seam, so compare edges by where they are.
        _, where = np.unique(np.round(positions, 5), axis=0, return_inverse=True)
        faces = where.reshape(-1)[faces]
        edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
        _, counts = np.unique(edges, axis=0, return_counts=True)
        return bool((counts == 2).all())

    def test_plain_backdrop_becomes_one_closed_object(self):
        image, depth = self.disc()
        mode, parts, used = free_3d_relief.build_mesh(image, depth)
        self.assertEqual(mode, "object")
        self.assertEqual(len(parts), 1)
        self.assertLess(used.width, image.width, "a small object should be framed tightly")
        self.assertGreater(free_3d_relief.signed_volume(parts), 0, "faces must point outward")
        self.assertTrue(self.edges_closed(parts), "the object must have no open edges")
        positions = parts[0][0]
        self.assertAlmostEqual(float((positions.max(axis=0) - positions.min(axis=0)).max()), 1.0, places=4)
        raw = free_3d_relief.write_glb(used, mode, parts)
        free_3d.validate_glb(raw)
        scene = glb_scene(raw)
        self.assertEqual(scene["asset"]["extras"], {"engine": "relief", "mode": "object",
                                                    "depth_model": "Depth Anything V2 Small"})
        self.assertEqual(len(scene["materials"]), 1)

    def test_busy_picture_becomes_a_relief_tile(self):
        import numpy as np

        random = np.random.default_rng(7)
        image = Image.fromarray(random.integers(0, 255, (180, 260, 3), dtype="uint8"))
        depth = np.linspace(0, 1, 260, dtype="float32")[None, :].repeat(180, axis=0)
        mode, parts, used = free_3d_relief.build_mesh(image, depth)
        self.assertEqual(mode, "scene")
        self.assertEqual(used.size, image.size)
        self.assertEqual(len(parts), 2)
        self.assertIsNone(parts[1][2], "the back of a tile is plain, not a mirrored photo")
        self.assertGreater(free_3d_relief.signed_volume(parts), 0)
        self.assertTrue(self.edges_closed(parts))
        raw = free_3d_relief.write_glb(used, mode, parts)
        free_3d.validate_glb(raw)
        self.assertEqual(len(glb_scene(raw)["materials"]), 2)

    def test_collage_is_not_mistaken_for_one_object(self):
        import numpy as np

        image = Image.new("RGB", (300, 300), (20, 20, 40))
        draw = ImageDraw.Draw(image)
        for left, top in ((30, 30), (170, 30), (30, 170), (170, 170)):
            draw.rectangle([left, top, left + 100, top + 100], fill=(210, 190, 90))
        mode, _, _ = free_3d_relief.build_mesh(image, np.ones((300, 300), dtype="float32"))
        self.assertEqual(mode, "scene")

    def test_object_touching_the_frame_stays_a_tile(self):
        import numpy as np

        image = Image.new("RGB", (300, 200), (245, 245, 245))
        ImageDraw.Draw(image).rectangle([0, 60, 299, 199], fill=(60, 120, 60))
        mode, _, _ = free_3d_relief.build_mesh(image, np.ones((200, 300), dtype="float32"))
        self.assertEqual(mode, "scene")

    def test_unusable_pictures_are_refused_in_plain_words(self):
        with tempfile.TemporaryDirectory() as temporary:
            tiny = Path(temporary) / "tiny.png"
            Image.new("RGB", (20, 20)).save(tiny)
            with self.assertRaisesRegex(free_3d_relief.ReliefError, "too small"):
                free_3d_relief.load_picture(tiny)
            ribbon = Path(temporary) / "ribbon.png"
            Image.new("RGB", (900, 40)).save(ribbon)
            with self.assertRaisesRegex(free_3d_relief.ReliefError, "long and thin"):
                free_3d_relief.load_picture(ribbon)
            broken = Path(temporary) / "broken.jpg"
            broken.write_bytes(b"not a picture")
            with self.assertRaisesRegex(free_3d_relief.ReliefError, "could not be opened"):
                free_3d_relief.load_picture(broken)

    def test_switch_and_missing_model_turn_the_engine_off(self):
        original = free_3d_relief.MODEL
        try:
            free_3d_relief.MODEL = Path("/nonexistent/model.onnx")
            self.assertFalse(free_3d_relief.available())
        finally:
            free_3d_relief.MODEL = original
        os.environ["VISUAL_BRAIN_RELIEF"] = "0"
        try:
            self.assertFalse(free_3d_relief.available())
        finally:
            del os.environ["VISUAL_BRAIN_RELIEF"]


@unittest.skipUnless(free_3d_relief.available(), "built-in 3D model is not installed (python3 free_3d_relief.py --install)")
class ReliefOverHTTP(unittest.TestCase):
    """A real photo through the real board server, with no worker anywhere."""

    def board(self, temporary, **extra):
        port = free_port()
        env = dict(os.environ, VISUAL_BRAIN_HOST="127.0.0.1", VISUAL_BRAIN_PORT=str(port),
                   VISUAL_BRAIN_DATA_DIR=temporary,
                   VISUAL_BRAIN_3D_WORKER_DIR=str(Path(temporary) / "absent-worker"),
                   VISUAL_BRAIN_3D_REMOTE_ORIGIN="", **extra)
        env.pop("VISUAL_BRAIN_RELIEF", None)
        env.update(extra)
        app = subprocess.Popen([sys.executable, str(ROOT / "server.py")], env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(app.stdout.close)
        self.addCleanup(app.wait, 10)
        self.addCleanup(app.terminate)
        self.assertTrue(select.select([app.stdout], [], [], 15)[0], "board did not start")
        self.assertIn("Visual Brain", app.stdout.readline())
        return f"http://127.0.0.1:{port}"

    def make_model(self, base, picture: Path):
        bootstrap = requests.get(base + "/api/bootstrap", timeout=5).json()
        headers = {"X-Board-Key": bootstrap["key"]}
        data_url = "data:image/jpeg;base64," + base64.b64encode(picture.read_bytes()).decode()
        captured = requests.post(base + "/api/capture", headers=headers, timeout=10, json={"image_data_url": data_url})
        self.assertEqual(captured.status_code, 201, captured.text)
        created = requests.post(base + "/api/3d/boards", headers=headers, timeout=10,
                                json={"board_id": "main", "item_ids": [captured.json()["item"]["id"]]})
        self.assertEqual(created.status_code, 201, created.text)
        deadline = time.time() + 90
        while time.time() < deadline:
            models = requests.get(base + "/api/bootstrap", timeout=5).json()["models"]
            if models and models[0]["status"] in ("ready", "failed"):
                break
            time.sleep(0.5)
        self.assertEqual(models[0]["status"], "ready", models[0].get("error"))
        raw = requests.get(f"{base}/api/models/{models[0]['id']}", timeout=10).content
        free_3d.validate_glb(raw)
        return models[0], glb_scene(raw)

    def test_one_photo_becomes_an_object_with_no_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = self.board(temporary)
            capabilities = requests.get(base + "/api/capabilities", timeout=5).json()
            self.assertTrue(capabilities["free_3d"])
            self.assertEqual(capabilities["free_3d_engine"], "builtin")
            self.assertFalse(capabilities["free_3d_multi"], "many angles still need a worker")
            model, scene = self.make_model(base, ROOT / "demo-seed/images/e0106.jpg")
            self.assertEqual(model["engine"], "builtin-object")
            self.assertEqual(scene["asset"]["extras"]["mode"], "object")

    def test_hosted_bridge_with_no_worker_connected_falls_back(self):
        """The live site's exact state: bridge switched on, the Ryzen not answering."""
        with tempfile.TemporaryDirectory() as temporary:
            base = self.board(temporary, VISUAL_BRAIN_3D_OUTBOUND="1", VISUAL_BRAIN_3D_REMOTE_TOKEN="t" * 48)
            self.assertEqual(requests.get(base + "/api/capabilities", timeout=5).json()["free_3d_engine"], "builtin")
            model, scene = self.make_model(base, ROOT / "demo-seed/images/e0096.jpg")
            self.assertEqual(model["engine"], "builtin-scene")
            self.assertEqual(scene["asset"]["extras"]["mode"], "scene")

    def test_switched_off_means_unavailable_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = self.board(temporary, VISUAL_BRAIN_RELIEF="0")
            capabilities = requests.get(base + "/api/capabilities", timeout=5).json()
            self.assertFalse(capabilities["free_3d"])
            self.assertEqual(capabilities["free_3d_engine"], "")


if __name__ == "__main__":
    unittest.main()
