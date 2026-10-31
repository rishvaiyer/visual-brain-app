"""The sample boards: loaded into an empty library, refused otherwise, and a clean start without the pictures."""
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start(env_extra, root=ROOT):
    port = free_port()
    temp = tempfile.mkdtemp()
    env = dict(os.environ, VISUAL_BRAIN_PORT=str(port), VISUAL_BRAIN_DATA_DIR=temp, **env_extra)
    process = subprocess.Popen(["python3", str(root / "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError(f"server exited with {process.returncode}")
        try:
            if requests.get(url + "/api/bootstrap", timeout=.3).ok:
                return process, url, temp
        except requests.RequestException:
            time.sleep(.05)
    process.kill()
    raise RuntimeError("server did not start")


def stop(process, temp):
    process.terminate()
    process.wait(timeout=5)
    shutil.rmtree(temp, ignore_errors=True)


manifest = json.loads((ROOT / "demo-seed" / "manifest.json").read_text(encoding="utf-8"))

pictures = all((ROOT / "demo-seed" / a["file"]).is_file() for a in manifest["assets"])
if pictures:
    # 1. The public playground opens on the sample boards, with every picture served.
    process, url, temp = start({"VISUAL_BRAIN_DEMO": "1"})
    try:
        data = requests.get(url + "/api/bootstrap", timeout=5).json()
        assert data["capabilities"]["sample_board"] is True
        assert len(data["assets"]) == len(manifest["assets"]), (len(data["assets"]), len(manifest["assets"]))
        assert {b["id"] for b in data["boards"]} == {b["id"] for b in manifest["boards"]}
        assert all(b["view"].get("autofit") for b in data["boards"]), "boards open fitted to the screen"
        folder_boards = [b for b in data["boards"] if b["folder_id"]]
        assert folder_boards and data["folders"], "the themed boards sit in a folder"
        kinds = {i["kind"] for i in data["items"]}
        assert {"image", "text", "shape"} <= kinds, kinds
        image_items = [i for i in data["items"] if i["kind"] == "image"]
        assert all(i["asset_id"] for i in image_items)
        for asset in data["assets"][:5]:
            response = requests.get(f"{url}/api/assets/{asset['id']}", timeout=5)
            assert response.status_code == 200 and response.headers["Content-Type"] == "image/jpeg"
            assert asset["credit"] and asset["tags"] and asset["collection"]
        # a visitor cannot load the samples a second time
        again = requests.post(url + "/api/load-sample", json={}, headers={"X-Board-Key": data["key"]}, timeout=5)
        assert again.status_code == 400 and "empty" in again.json()["error"], again.text
        # the landing page and its pictures are public, and path tricks go nowhere
        assert requests.get(url + "/landing", timeout=5).status_code == 200
        assert requests.get(url + "/demo-seed/images/e0014.jpg", timeout=5).status_code == 200
        assert requests.get(url + "/demo-seed/images/..%2Fmanifest.json", timeout=5).status_code == 404
        assert requests.get(url + "/demo-seed/images/nothing.jpg", timeout=5).status_code == 404
    finally:
        stop(process, temp)

    # 2. A private local copy starts empty, and can opt in to the samples once.
    process, url, temp = start({})
    try:
        data = requests.get(url + "/api/bootstrap", timeout=5).json()
        assert data["assets"] == [] and data["capabilities"]["sample_board"] is True
        headers = {"X-Board-Key": data["key"]}
        loaded = requests.post(url + "/api/load-sample", json={}, headers=headers, timeout=20)
        assert loaded.status_code == 201 and loaded.json()["assets"] == len(manifest["assets"]), loaded.text
        after = requests.get(url + "/api/bootstrap", timeout=5).json()
        assert len(after["assets"]) == len(manifest["assets"])
        assert requests.post(url + "/api/load-sample", json={}, headers=headers, timeout=5).status_code == 400
        assert requests.post(url + "/api/load-sample", json={}, timeout=5).status_code == 403, "needs the pairing key"
    finally:
        stop(process, temp)

else:
    print("skipped parts 1 and 2: this copy has no sample pictures (they are kept out of the public repo)")

# 3. A copy that ships without the pictures (the public repo does) still starts, empty.
with tempfile.TemporaryDirectory() as bare:
    bare = Path(bare)
    for name in ("server.py", "hosting.py", "enrichment.py", "vision_labels.swift"):
        if (ROOT / name).exists():
            shutil.copy(ROOT / name, bare / name)
    (bare / "demo-seed").mkdir()
    shutil.copy(ROOT / "demo-seed" / "manifest.json", bare / "demo-seed" / "manifest.json")
    process, url, temp = start({"VISUAL_BRAIN_DEMO": "1"}, root=bare)
    try:
        data = requests.get(url + "/api/bootstrap", timeout=5).json()
        assert data["assets"] == [] and data["capabilities"]["sample_board"] is False
    finally:
        stop(process, temp)

print("demo seed ok")
