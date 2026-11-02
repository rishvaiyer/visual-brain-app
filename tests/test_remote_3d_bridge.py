"""Exercise the private Ryzen dispatch and callback without a public tunnel."""

import base64
import io
import json
import os
import select
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def triangle_glb():
    points = struct.pack("<9f", -1, -1, 0, 1, -1, 0, 0, 1, 0)
    scene = {"asset": {"version": "2.0"}, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
             "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
             "buffers": [{"byteLength": len(points)}],
             "bufferViews": [{"buffer": 0, "byteLength": len(points)}],
             "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3,
                            "type": "VEC3", "min": [-1, -1, 0], "max": [1, 1, 0]}]}
    encoded = json.dumps(scene, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    return (b"glTF" + struct.pack("<II", 2, 12 + 8 + len(encoded) + 8 + len(points))
            + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
            + struct.pack("<II", len(points), 0x004E4942) + points)


class RemoteBridgeHTTP(unittest.TestCase):
    def test_worker_token_can_load_from_protected_credential_file(self):
        import free_3d_worker as worker
        with tempfile.TemporaryDirectory() as temporary:
            credential = Path(temporary) / "worker-token"
            credential.write_text("from-file-token\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"VISUAL_BRAIN_WORKER_TOKEN_FILE": str(credential),
                                              "VISUAL_BRAIN_WORKER_TOKEN": "environment-token"}):
                self.assertEqual(worker.load_worker_token(), "from-file-token")
                credential.unlink()
                self.assertEqual(worker.load_worker_token(), "")

    def test_multi_photo_failure_keeps_guidance_and_hides_trace(self):
        import free_3d
        with tempfile.TemporaryDirectory() as temporary:
            sources = []
            for index in range(32):
                source = Path(temporary) / f"source_{index:03}.jpg"
                source.write_bytes(b"test")
                sources.append(source)
            output = Path(temporary) / "model.glb"
            with mock.patch.object(free_3d, "multi_local_available", return_value=True):
                with mock.patch.object(free_3d.subprocess, "run", return_value=SimpleNamespace(
                    returncode=2, stderr="VISUAL_BRAIN_MULTI_ERROR:One side of the object is missing. Add photos around the full circle.\n", stdout="")):
                    with self.assertRaisesRegex(free_3d.MultiPhotoError, "One side of the object is missing"):
                        free_3d.generate_multi(sources, output)
                with mock.patch.object(free_3d.subprocess, "run", return_value=SimpleNamespace(
                    returncode=1, stderr="Traceback: /private/worker/path", stdout="")):
                    with self.assertRaises(free_3d.MultiPhotoError) as caught:
                        free_3d.generate_multi(sources, output)
                    self.assertNotIn("/private/worker/path", str(caught.exception))

    def test_dispatch_worker_callback_and_token_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_env = os.environ.copy()
            worker_port, app_port = free_port(), free_port()
            token = secrets.token_urlsafe(36)
            app_url = f"http://127.0.0.1:{app_port}"
            worker_url = f"http://127.0.0.1:{worker_port}"
            os.environ.update(VISUAL_BRAIN_WORKER_TOKEN=token,
                              VISUAL_BRAIN_WORKER_CALLBACK_ORIGIN=app_url,
                              VISUAL_BRAIN_WORKER_TEST_LOOPBACK="1")
            import free_3d_worker as worker
            original_worker_state = (worker.TOKEN, worker.CALLBACK_ORIGIN, worker.INCOMING, worker.RESULTS,
                                     worker.free_3d.local_available, worker.free_3d.multi_local_available,
                                     worker.free_3d.generate, worker.free_3d.generate_multi, worker.callback)
            worker.TOKEN = token
            worker.CALLBACK_ORIGIN = app_url
            worker.INCOMING = Path(temporary) / "incoming"
            worker.RESULTS = Path(temporary) / "results"
            worker.free_3d.local_available = lambda: True
            worker.free_3d.multi_local_available = lambda: True
            received_images = []
            received_multi = []

            def fake_generate(source, output):
                with Image.open(source) as image:
                    received_images.append((image.size, image.getpixel((10, 32))))
                output.write_bytes(triangle_glb())

            worker.free_3d.generate = fake_generate
            def fake_generate_multi(sources, output):
                received_multi.append(len(sources))
                with Image.open(sources[0]) as image:
                    self.assertEqual(image.size, (64, 64))
                if len(received_multi) == 1:
                    raise worker.free_3d.MultiPhotoError("One side of the object is missing. Add photos around the full circle.")
                output.write_bytes(triangle_glb())
            worker.free_3d.generate_multi = fake_generate_multi
            completed = threading.Event()
            original_callback = worker.callback

            def marked_callback(*args):
                result = original_callback(*args)
                completed.set()
                return result

            worker.callback = marked_callback
            worker_server = worker.ThreadingHTTPServer(("127.0.0.1", worker_port), worker.Handler)
            worker_thread = threading.Thread(target=worker_server.serve_forever, daemon=True)
            worker_thread.start()
            env = dict(os.environ, VISUAL_BRAIN_HOST="127.0.0.1", VISUAL_BRAIN_PORT=str(app_port),
                       VISUAL_BRAIN_DATA_DIR=str(Path(temporary) / "app"),
                       VISUAL_BRAIN_3D_WORKER_DIR=str(Path(temporary) / "no-local-worker"),
                       VISUAL_BRAIN_3D_REMOTE_ORIGIN=worker_url,
                       VISUAL_BRAIN_3D_REMOTE_TOKEN=token, VISUAL_BRAIN_3D_REMOTE_TEST_LOOPBACK="1",
                       VISUAL_BRAIN_3D_REMOTE_MULTI="1")
            app = subprocess.Popen([sys.executable, str(ROOT / "server.py")], env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            try:
                denied_job = requests.post(worker_url + "/jobs", data=b"bad", headers={
                    "Content-Type": "image/jpeg", "X-Visual-Brain-Worker-Token": "wrong",
                    "X-Visual-Brain-Model-Id": "model_123456789abc"}, timeout=5)
                self.assertEqual(denied_job.status_code, 403)
                bad_archive = io.BytesIO()
                with zipfile.ZipFile(bad_archive, "w") as archive:
                    for index in range(31):
                        archive.writestr(f"photo_{index:03}.jpg", b"unused")
                    archive.writestr("../outside.jpg", b"bad")
                rejected_archive = requests.post(worker_url + "/jobs", data=bad_archive.getvalue(), headers={
                    "Content-Type": "application/zip", "X-Visual-Brain-Job-Kind": "multi",
                    "X-Visual-Brain-Worker-Token": token,
                    "X-Visual-Brain-Model-Id": "model_123456789abc"}, timeout=5)
                self.assertEqual(rejected_archive.status_code, 400)
                self.assertTrue(select.select([app.stdout], [], [], 15)[0], "app did not start")
                self.assertIn("Visual Brain", app.stdout.readline())
                ready = requests.get(app_url + "/healthz", timeout=5)
                self.assertEqual(ready.status_code, 200)
                bootstrap = requests.get(app_url + "/api/bootstrap", timeout=5).json()
                self.assertTrue(bootstrap["capabilities"]["free_3d"])
                image = Image.new("RGB", (64, 64), "#2464d5")
                image.paste(Image.new("RGB", (32, 64), "#d56d34"), (0, 0))
                buffer = io.BytesIO()
                image.save(buffer, "PNG")
                data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
                headers = {"X-Board-Key": bootstrap["key"]}
                saved = requests.post(app_url + "/api/capture", json={"image_data_url": data_url},
                                      headers=headers, timeout=10).json()
                source_state = saved["item"]["state"]
                source_state["crop"] = {"x": 0, "y": 0, "w": .5, "h": 1}
                edited = requests.put(app_url + "/api/items/" + saved["item"]["id"],
                                      json={"state": source_state}, headers=headers, timeout=10)
                self.assertTrue(edited.ok, edited.text)
                created = requests.post(app_url + "/api/3d/boards", json={"board_id": "main",
                                        "item_ids": [saved["item"]["id"]]}, headers=headers, timeout=10)
                self.assertEqual(created.status_code, 201, created.text)
                self.assertTrue(completed.wait(timeout=15), "worker did not call back")
                self.assertEqual(received_images[0][0], (32, 64))
                self.assertGreater(received_images[0][1][0], received_images[0][1][2])
                data = requests.get(app_url + "/api/bootstrap", timeout=5).json()
                generated = next(model for model in data["models"] if model["source_asset_id"] == saved["asset"]["id"])
                self.assertEqual(generated["status"], "ready", generated["error"])
                self.assertEqual(json.loads(generated["source_crop"]), source_state["crop"])
                copied_image = next(item for item in data["items"] if item["board_id"] == created.json()["board_id"]
                                    and item["kind"] == "image")
                self.assertEqual(copied_image["state"]["crop"], source_state["crop"])
                model_url = app_url + "/api/models/" + generated["id"]
                self.assertEqual(requests.get(model_url, timeout=5).content, triangle_glb())
                denied = requests.post(app_url + "/api/3d/callback/" + generated["id"],
                                       data=triangle_glb(), headers={"Content-Type": "model/gltf-binary",
                                       "X-Visual-Brain-Worker-Token": "wrong"}, timeout=5)
                self.assertEqual(denied.status_code, 403)
                photo_ids = []
                for index in range(32):
                    photo = Image.new("RGB", (64, 64), (index * 7 % 256, index * 11 % 256, index * 13 % 256))
                    photo_bytes = io.BytesIO()
                    photo.save(photo_bytes, "JPEG")
                    saved_photo = requests.post(app_url + "/api/capture", headers=headers, timeout=10,
                                                json={"image_data_url": "data:image/jpeg;base64," + base64.b64encode(photo_bytes.getvalue()).decode()})
                    self.assertEqual(saved_photo.status_code, 201, saved_photo.text)
                    photo_ids.append(saved_photo.json()["item"]["id"])
                completed.clear()
                grouped = requests.post(app_url + "/api/3d/boards", headers=headers, timeout=10,
                                        json={"board_id": "main", "item_ids": photo_ids, "mode": "one-object"})
                self.assertEqual(grouped.status_code, 201, grouped.text)
                self.assertEqual(grouped.json()["queued"], 1)
                self.assertTrue(completed.wait(timeout=20), "multi-photo worker did not call back")
                self.assertEqual(received_multi, [32])
                updated = requests.get(app_url + "/api/bootstrap", timeout=5).json()
                grouped_models = [entry for entry in updated["models"] if len(entry["source_asset_ids"]) == 32]
                self.assertEqual(len(grouped_models), 1)
                self.assertEqual(grouped_models[0]["status"], "failed")
                self.assertIn("One side of the object is missing", grouped_models[0]["error"])
                self.assertEqual(len([entry for entry in updated["items"] if entry["board_id"] == grouped.json()["board_id"] and entry["kind"] == "model"]), 1)
                grouped_id = grouped_models[0]["id"]
                completed.clear()
                retry = requests.post(app_url + f"/api/models/{grouped_id}/retry", headers=headers, json={}, timeout=10)
                self.assertEqual(retry.status_code, 200, retry.text)
                self.assertTrue(completed.wait(timeout=20), "multi-photo retry did not call back")
                self.assertEqual(received_multi, [32, 32])
                retried = requests.get(app_url + "/api/bootstrap", timeout=5).json()
                self.assertEqual(next(entry for entry in retried["models"] if entry["id"] == grouped_id)["status"], "ready")
                self.assertEqual(requests.get(app_url + "/api/models/" + grouped_id, timeout=5).content, triangle_glb())
            finally:
                app.terminate()
                app.wait(timeout=5)
                app.stdout.close()
                worker_server.shutdown()
                worker_server.server_close()
                worker_thread.join(timeout=5)
                (worker.TOKEN, worker.CALLBACK_ORIGIN, worker.INCOMING, worker.RESULTS,
                 worker.free_3d.local_available, worker.free_3d.multi_local_available,
                 worker.free_3d.generate, worker.free_3d.generate_multi, worker.callback) = original_worker_state
                os.environ.clear()
                os.environ.update(original_env)


if __name__ == "__main__":
    unittest.main()
