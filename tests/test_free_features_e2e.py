"""One local HTTP run across camera lookup, sources, 3D storage, and private critique."""

import base64
import io
import json
import os
import select
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def triangle_glb():
    positions = struct.pack("<9f", -1, -1, 0, 1, -1, 0, 0, 1, 0)
    scene = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
             "nodes": [{"mesh": 0}], "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
             "buffers": [{"byteLength": len(positions)}],
             "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions)}],
             "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3,
                            "type": "VEC3", "min": [-1, -1, 0], "max": [1, 1, 0]}]}
    encoded = json.dumps(scene, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    return (b"glTF" + struct.pack("<II", 2, 12 + 8 + len(encoded) + 8 + len(positions))
            + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
            + struct.pack("<II", len(positions), 0x004E4942) + positions)


class FreeFeaturesHTTP(unittest.TestCase):
    def test_complete_local_flow(self):
        with tempfile.TemporaryDirectory() as temporary:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            os.environ.update(VISUAL_BRAIN_HOST="127.0.0.1", VISUAL_BRAIN_PORT=str(port),
                              VISUAL_BRAIN_DATA_DIR=temporary, VISUAL_BRAIN_DECISION_URL="")
            import server
            httpd = server.ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}"
            try:
                bootstrap = requests.get(base + "/api/bootstrap", timeout=5).json()
                headers = {"X-Board-Key": bootstrap["key"]}
                photo = Image.new("RGB", (40, 40), "#d6753f")
                image_file = io.BytesIO()
                photo.save(image_file, "PNG")
                image_data = "data:image/png;base64," + base64.b64encode(image_file.getvalue()).decode()
                saved = requests.post(base + "/api/capture", headers=headers, timeout=10, json={
                    "image_data_url": image_data, "title": "Orange object",
                    "source_url": "https://example.org/object", "source_title": "A page about it",
                    "source_author": "Maker", "source_license": "CC BY 4.0"}).json()
                image_id = saved["asset"]["id"]
                board_id = saved["item"]["board_id"]
                self.assertEqual(saved["asset"]["source_author"], "Maker")
                groups = requests.get(base + "/api/taste-map", headers=headers, timeout=10).json()["groups"]
                self.assertEqual(groups[0]["ids"], [image_id])
                matched = requests.post(base + "/api/camera-match", headers=headers, timeout=10,
                                        json={"image_data_url": image_data}).json()
                self.assertEqual(matched["matches"][0]["id"], image_id)
                self.assertFalse(matched["frame_saved"])
                self.assertEqual(len(requests.get(base + "/api/bootstrap").json()["assets"]), 1)
                source = {"url": "https://example.org/object", "title": "A page about it",
                          "author": "Maker", "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/"}
                self.assertTrue(requests.put(base + "/api/assets/" + image_id, headers=headers, timeout=10,
                                             json={"title": "Orange object", "tags": [], "collection": "",
                                                   "favorite": False, "source": source}).ok)
                self.assertEqual(requests.get(base + "/api/bootstrap").json()["assets"][0]["source_license_url"], source["license_url"])
                model_data = "data:model/gltf-binary;base64," + base64.b64encode(triangle_glb()).decode()
                added_model = requests.post(base + "/api/models", headers=headers, timeout=10,
                                            json={"model_data_url": model_data, "board_id": board_id})
                self.assertEqual(added_model.status_code, 201, added_model.text)
                model_id = added_model.json()["model"]["id"]
                self.assertEqual(requests.get(base + "/api/models/" + model_id, timeout=5).content, triangle_glb())
                restore_dir = Path(temporary) / "restored"
                with zipfile.ZipFile(io.BytesIO(requests.get(base + "/api/export", headers=headers, timeout=10).content)) as archive:
                    self.assertIn("models/" + model_id + ".glb", archive.namelist())
                    for name in archive.namelist():
                        if name in {"manifest.json", "README.txt"}:
                            continue
                        destination = restore_dir / (name.removeprefix("data/") if name.startswith("data/") else name)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(archive.read(name))
                invite = requests.post(base + "/api/critique/invite", headers=headers, timeout=10,
                                       json={"board_id": board_id}).json()
                shared = requests.get(base + invite["path"].replace("/critique/", "/api/critique/shared/"), timeout=10).json()
                self.assertEqual(shared["board"]["id"], board_id)
                self.assertEqual([image["id"] for image in shared["images"]], [image_id])
                comment_url = base + invite["path"].replace("/critique/", "/api/critique/shared/") + "/comments"
                comment = requests.post(comment_url, headers={"Origin": base}, timeout=10,
                                        json={"item_id": saved["item"]["id"], "x": .25, "y": .75,
                                              "author": "Guest", "body": "Change this corner"})
                self.assertEqual(comment.status_code, 201, comment.text)
                self.assertEqual(comment.json()["x"], .25)
                self.assertTrue(requests.delete(base + "/api/critique/invite/" + board_id, headers=headers, timeout=10).ok)
                self.assertEqual(requests.get(base + invite["path"], timeout=5).status_code, 404)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                restored_port = probe.getsockname()[1]
            env = dict(os.environ, VISUAL_BRAIN_PORT=str(restored_port), VISUAL_BRAIN_DATA_DIR=str(restore_dir))
            restored = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[1] / "server.py")],
                                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(select.select([restored.stdout], [], [], 10)[0], "Restored server did not start")
                self.assertIn("Visual Brain", restored.stdout.readline())
                restored_base = f"http://127.0.0.1:{restored_port}"
                restored_data = requests.get(restored_base + "/api/bootstrap", timeout=5).json()
                self.assertEqual(next(model for model in restored_data["models"] if model["id"] == model_id)["status"], "ready")
                self.assertEqual(requests.get(restored_base + "/api/models/" + model_id, timeout=5).content, triangle_glb())
                self.assertEqual(next(asset for asset in restored_data["assets"] if asset["id"] == image_id)["source_author"], "Maker")
            finally:
                restored.terminate()
                restored.wait(timeout=5)
                restored.stdout.close()
                restored.stderr.close()


if __name__ == "__main__":
    unittest.main()
