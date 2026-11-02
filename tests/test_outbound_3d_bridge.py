"""Exercise authenticated outbound transport through the actual board server."""

import base64
import io
import os
import select
import secrets
import socket
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import requests
import websocket
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import free_3d_outbound
from test_remote_3d_bridge import triangle_glb


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class OutboundBridgeHTTP(unittest.TestCase):
    def test_authenticated_single_and_multi_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            port = free_port()
            token = secrets.token_urlsafe(36)
            base = f"http://127.0.0.1:{port}"
            env = dict(os.environ, VISUAL_BRAIN_HOST="127.0.0.1", VISUAL_BRAIN_PORT=str(port),
                       VISUAL_BRAIN_DATA_DIR=temporary,
                       VISUAL_BRAIN_3D_WORKER_DIR=str(Path(temporary) / "absent-worker"),
                       VISUAL_BRAIN_3D_OUTBOUND="1", VISUAL_BRAIN_3D_REMOTE_TOKEN=token,
                       VISUAL_BRAIN_3D_REMOTE_ORIGIN="")
            app = subprocess.Popen([sys.executable, str(ROOT / "server.py")], env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True)
            connection = None
            try:
                self.assertTrue(select.select([app.stdout], [], [], 15)[0], "board did not start")
                self.assertIn("Visual Brain", app.stdout.readline())
                socket_url = f"ws://127.0.0.1:{port}/api/3d/worker/socket"
                with self.assertRaises(websocket.WebSocketBadStatusException):
                    websocket.create_connection(socket_url, timeout=5, suppress_origin=True,
                                                header={"X-Visual-Brain-Worker-Token": "wrong"})
                self.assertFalse(requests.get(base + "/api/capabilities", timeout=5).json()["free_3d"])
                connection = websocket.create_connection(
                    socket_url, timeout=15, suppress_origin=True,
                    header={"X-Visual-Brain-Worker-Token": token, "X-Visual-Brain-Multi-Ready": "1"})
                self.assertTrue(requests.get(base + "/api/capabilities", timeout=5).json()["free_3d_multi"])
                bootstrap = requests.get(base + "/api/bootstrap", timeout=5).json()
                headers = {"X-Board-Key": bootstrap["key"]}

                def capture(color):
                    image = Image.new("RGB", (48, 48), color)
                    output = io.BytesIO()
                    image.save(output, "JPEG")
                    response = requests.post(base + "/api/capture", headers=headers, timeout=10,
                                             json={"image_data_url": "data:image/jpeg;base64,"
                                                   + base64.b64encode(output.getvalue()).decode()})
                    self.assertEqual(response.status_code, 201, response.text)
                    return response.json()["item"]["id"]

                item_id = capture((233, 95, 48))
                created = requests.post(base + "/api/3d/boards", headers=headers, timeout=10,
                                        json={"board_id": "main", "item_ids": [item_id]})
                self.assertEqual(created.status_code, 201, created.text)
                model_id, kind, raw = free_3d_outbound.decode_job(connection.recv())
                self.assertEqual(kind, "single")
                with Image.open(io.BytesIO(raw)) as image:
                    self.assertEqual(image.size, (48, 48))
                self.assertEqual(requests.post(base + "/api/3d/callback/" + model_id,
                                               headers={"X-Visual-Brain-Worker-Token": token,
                                                        "Content-Type": "model/gltf-binary"},
                                               data=triangle_glb(), timeout=5).status_code, 200)
                self.assertEqual(requests.get(base + "/api/models/" + model_id, timeout=5).content,
                                 triangle_glb())

                ids = [capture((index * 7 % 256, index * 11 % 256, index * 13 % 256))
                       for index in range(32)]
                grouped = requests.post(base + "/api/3d/boards", headers=headers, timeout=10,
                                        json={"board_id": "main", "item_ids": ids, "mode": "one-object"})
                self.assertEqual(grouped.status_code, 201, grouped.text)
                multi_id, kind, raw = free_3d_outbound.decode_job(connection.recv())
                self.assertEqual(kind, "multi")
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    self.assertEqual(len(archive.namelist()), 32)
                self.assertEqual(requests.post(base + "/api/3d/callback/" + multi_id,
                                               headers={"X-Visual-Brain-Worker-Token": token,
                                                        "Content-Type": "model/gltf-binary"},
                                               data=triangle_glb(), timeout=5).status_code, 200)
                self.assertEqual(requests.get(base + "/api/models/" + multi_id, timeout=5).content,
                                 triangle_glb())
            finally:
                if connection:
                    connection.close()
                app.terminate()
                app.wait(timeout=5)
                app.stdout.close()

    def test_reject_malformed_worker_job(self):
        invalid = b'[]'
        packet = len(invalid).to_bytes(4, "big") + invalid + b"1234"
        with self.assertRaisesRegex(ValueError, "header"):
            free_3d_outbound.decode_job(packet)


if __name__ == "__main__":
    unittest.main()
