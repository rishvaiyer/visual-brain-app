#!/usr/bin/env python3
"""Run a real multi-photo board job on a local Ryzen test server.

Example: python3 tools/smoke_3d_multi_cpu.py /path/to/jpeg-orbit /path/to/result.glb
Add --remote-loopback to run through the authenticated worker and callback on this Ryzen.
The source photos stay local and the temporary board is removed afterwards.
"""

from __future__ import annotations

import argparse
import base64
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("photos", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--remote-loopback", action="store_true",
                        help="send the full job through the private worker and callback protocol")
    args = parser.parse_args()
    photos = sorted(args.photos.glob("*.jpg"))
    output = args.output
    if not 32 <= len(photos) <= 80:
        raise SystemExit("The folder needs 32–80 JPEG photos")
    with tempfile.TemporaryDirectory(prefix="visual-brain-real-board-") as temporary:
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        worker_env = None
        if args.remote_loopback:
            real_worker = Path(os.environ["VISUAL_BRAIN_3D_WORKER_DIR"])
            test_worker = Path(temporary) / "worker"
            test_worker.mkdir()
            for name in (".ready", ".multi-ready", ".venv", "multi-venv", "TripoSR", "openmvs", "model-cache"):
                (test_worker / name).symlink_to(real_worker / name, target_is_directory=not name.startswith("."))
            token = secrets.token_urlsafe(36)
            worker_env = dict(os.environ, VISUAL_BRAIN_3D_WORKER_DIR=str(test_worker),
                              VISUAL_BRAIN_WORKER_TOKEN=token,
                              VISUAL_BRAIN_WORKER_CALLBACK_ORIGIN=base,
                              VISUAL_BRAIN_WORKER_TEST_LOOPBACK="1",
                              VISUAL_BRAIN_WORKER_PORT=str(free_port()))
            os.environ["VISUAL_BRAIN_3D_WORKER_DIR"] = str(Path(temporary) / "no-local-worker")
            os.environ["VISUAL_BRAIN_3D_REMOTE_ORIGIN"] = f"http://127.0.0.1:{worker_env['VISUAL_BRAIN_WORKER_PORT']}"
            os.environ["VISUAL_BRAIN_3D_REMOTE_TOKEN"] = token
            os.environ["VISUAL_BRAIN_3D_REMOTE_TEST_LOOPBACK"] = "1"
            os.environ["VISUAL_BRAIN_3D_REMOTE_MULTI"] = "1"
        os.environ["VISUAL_BRAIN_DATA_DIR"] = temporary
        os.environ["VISUAL_BRAIN_PORT"] = str(port)
        os.environ["VISUAL_BRAIN_HOST"] = "127.0.0.1"
        import server

        done = threading.Event()
        if args.remote_loopback:
            callback = server.Handler.handle_3d_callback

            def report_callback(self, model_id):
                try:
                    return callback(self, model_id)
                finally:
                    done.set()

            server.Handler.handle_3d_callback = report_callback
        else:
            generate = server.generate_board_models

            def report_completion(jobs):
                try:
                    generate(jobs)
                finally:
                    done.set()

            server.generate_board_models = report_completion
        httpd = ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        worker = None
        try:
            if worker_env:
                worker = subprocess.Popen([sys.executable, str(ROOT / "free_3d_worker.py")], cwd=ROOT,
                                          env=worker_env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True)
                if "Private 3D worker listening" not in worker.stdout.readline():
                    raise RuntimeError("Private Ryzen worker did not start")
            bootstrap = requests.get(base + "/api/bootstrap", timeout=10).json()
            if not bootstrap["capabilities"]["free_3d_multi"]:
                raise RuntimeError("The Ryzen multi-photo worker is unavailable")
            headers = {"X-Board-Key": bootstrap["key"]}
            item_ids = []
            for photo in photos:
                data = "data:image/jpeg;base64," + base64.b64encode(photo.read_bytes()).decode("ascii")
                response = requests.post(base + "/api/capture", headers=headers, timeout=30,
                                         json={"image_data_url": data, "board_id": "main", "title": photo.name})
                response.raise_for_status()
                item_ids.append(response.json()["item"]["id"])
            response = requests.post(base + "/api/3d/boards", headers=headers, timeout=30,
                                     json={"board_id": "main", "item_ids": item_ids, "mode": "one-object"})
            response.raise_for_status()
            board_id = response.json()["board_id"]
            if not done.wait(1800):
                raise RuntimeError("The model did not finish within the test window")
            state = requests.get(base + "/api/bootstrap", timeout=10).json()
            models = [model for model in state["models"] if len(model["source_asset_ids"]) == len(photos)]
            if len(models) != 1 or models[0]["status"] != "ready":
                raise RuntimeError(models[0]["error"] if models else "The board has no combined model")
            if len([item for item in state["items"] if item["board_id"] == board_id and item["kind"] == "model"]) != 1:
                raise RuntimeError("The board did not keep one model")
            raw = requests.get(base + "/api/models/" + models[0]["id"], timeout=30).content
            server.free_3d.validate_glb(raw)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(raw)
            print("REAL_MULTI_BOARD_READY", "private-bridge" if args.remote_loopback else "local", len(photos), len(raw), output)
        finally:
            if worker:
                worker.terminate()
                worker.wait(timeout=5)
                worker.stdout.close()
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    main()
