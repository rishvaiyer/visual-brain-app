"""Isolated end-to-end smoke test for local capture and board persistence."""
import base64
import io
import json
import os
import sqlite3
import socket
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    PORT = probe.getsockname()[1]
URL = f"http://127.0.0.1:{PORT}"


def wait_ready(process):
    for _ in range(80):
        if process.poll() is not None:
            raise RuntimeError(f"Server exited with {process.returncode}")
        try:
            if requests.get(URL + "/api/bootstrap", timeout=.2).ok:
                return
        except requests.RequestException:
            time.sleep(.05)
    raise RuntimeError("Server did not start")


with tempfile.TemporaryDirectory() as temp:
    env = dict(os.environ, VISUAL_BRAIN_PORT=str(PORT), VISUAL_BRAIN_DATA_DIR=temp,
               VISUAL_BRAIN_DECISION_URL="")
    process = subprocess.Popen(["python3", str(ROOT / "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_ready(process)
        bootstrap = requests.get(URL + "/api/bootstrap").json()
        key = bootstrap["key"]
        headers = {"X-Board-Key": key}
        assert bootstrap["boards"][0]["id"] == "main"
        assert bootstrap["boards"][0]["name"] == "Everything"
        assert requests.post(URL + "/api/items", json={"kind": "text"}).status_code == 403
        assert requests.get(URL + "/api/boards-list").status_code == 403
        assert requests.get(URL + "/api/boards-list", headers=headers).json()["boards"] == [{"id":"main","name":"Everything"}]
        board = requests.post(URL + "/api/boards", json={"name":"Chosen"}, headers=headers).json()
        folder = requests.post(URL + "/api/folders", json={"name":"Ideas"}, headers=headers).json()
        assert folder["name"] == "Ideas"
        assert requests.put(URL + "/api/boards/" + board["id"], json={"name":"Chosen work","folder_id":folder["id"]}, headers=headers).ok
        grouped = requests.get(URL + "/api/bootstrap").json()
        assert grouped["folders"][0]["id"] == folder["id"]
        assert next(b for b in grouped["boards"] if b["id"] == board["id"])["folder_id"] == folder["id"]
        assert requests.put(URL + "/api/boards/main", json={"name":"Renamed"}, headers=headers).status_code == 400
        shape = requests.post(URL + "/api/items", json={"board_id":board["id"],"kind":"shape","state":{"shape":"arrow","x":10,"y":20,"w":100,"h":80}}, headers=headers)
        assert shape.status_code == 201 and shape.json()["kind"] == "shape"
        for gone in ("/api/agent-tasks", "/api/thoughts", "/api/call-briefs", "/api/personal-tasks", "/api/photo-brain/status", "/api/newsletter/issues", "/api/agent-reference", "/api/drive-status"):
            assert requests.get(URL + gone, headers=headers).status_code == 404, gone
        assert requests.post(URL + "/api/theme-search", json={"query": "x"}, headers=headers).status_code == 404
        assert requests.get(URL + "/").status_code == 200 and "Visual Brain" in requests.get(URL + "/").text
        img = Image.new("RGB", (24, 16), "#db8d5b")
        buffer = io.BytesIO()
        img.save(buffer, "PNG")
        image_bytes = buffer.getvalue()
        payload = {"source_url": "https://example.com/inspiration", "title": "Sample", "favorite": True,
                   "image_data_url": "data:image/png;base64," + base64.b64encode(image_bytes).decode()}
        first = requests.post(URL + "/api/capture", json=payload, headers=headers, timeout=5)
        assert first.status_code == 201, first.text
        first = first.json()
        assert first["duplicate"] is False and first["asset"]["favorite"] is True
        assert first["asset"]["auto_tags"] == []
        for _ in range(150):
            suggestion = requests.get(URL + "/api/enrichment/" + first["asset"]["id"], headers=headers).json()
            if suggestion["status"] in ("ready", "error"):
                break
            time.sleep(.1)
        assert suggestion["status"] == "ready", suggestion
        assert any(tag["tag"] == "sample" for tag in suggestion["suggestions"]["tags"])
        assert requests.get(URL + "/api/enrichment/" + first["asset"]["id"]).status_code == 403
        assert (first["asset"]["pixel_width"], first["asset"]["pixel_height"]) == (24, 16)
        assert first["item"]["state"]["h"] == 173
        second = requests.post(URL + "/api/capture", json=payload, headers=headers, timeout=5).json()
        assert second["duplicate"] is True and second["asset"]["id"] == first["asset"]["id"]
        chosen = requests.post(URL + "/api/capture", json={**payload,"board_id":board["id"]}, headers=headers, timeout=5).json()
        assert chosen["item"]["board_id"] == board["id"] and chosen["asset"]["id"] == first["asset"]["id"]
        assert requests.post(URL + "/api/capture", json={**payload,"board_id":"missing"}, headers=headers).status_code == 400
        assert requests.get(URL + "/api/assets/" + first["asset"]["id"]).content == image_bytes

        item = first["item"]
        item["state"].update({"x": -175, "y": 320, "rotation": 19,
                              "crop": {"x": .1, "y": .1, "w": .8, "h": .8}})
        assert requests.put(URL + "/api/items/" + item["id"], json={"state": item["state"]}, headers=headers).ok
        assert requests.put(URL + "/api/assets/" + first["asset"]["id"],
                            json={"title": "Edited", "tags": ["retro", "orange"], "collection": "Dream Office", "favorite": True},
                            headers=headers).ok
        fresh = requests.get(URL + "/api/bootstrap").json()
        assert next(x for x in fresh["items"] if x["id"] == item["id"])["state"]["x"] == -175
        assert fresh["assets"][0]["title"] == "Edited"
        assert fresh["assets"][0]["tags"] == ["retro", "orange"]
        assert requests.post(URL + "/api/local-visual-search", json={"query": "retro"}).status_code == 403
        assert requests.post(URL + "/api/local-visual-search", json={"query": "retro"}, headers=headers).status_code == 501
        archive = requests.get(URL + "/api/export", headers=headers)
        assert archive.ok
        with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
            manifest = json.loads(z.read("manifest.json"))
            assert len(manifest["assets"]) == 1 and len(manifest["items"]) == 4
            assert "auto_tags" in manifest["assets"][0]
            assert len(manifest["asset_enrichment"]) == 1
            assert manifest["folders"][0]["name"] == "Ideas"
            assert "data/visual-brain.sqlite3" in z.namelist()
            assert z.read("assets/" + first["asset"]["filename"]) == image_bytes
        font_path = Path("/System/Library/Fonts/Geneva.ttf")
        font_bytes = font_path.read_bytes()
        font_payload = {"name": "Geneva.ttf", "font_data_url": "data:font/ttf;base64," + base64.b64encode(font_bytes).decode()}
        font_response = requests.post(URL + "/api/fonts", json=font_payload, headers=headers)
        assert font_response.status_code == 201, font_response.text
        saved_font = font_response.json()
        assert saved_font["duplicate"] is False
        assert requests.get(URL + "/api/fonts/" + saved_font["font"]["id"]).content == font_bytes
        font_board = next(b for b in requests.get(URL + "/api/bootstrap").json()["boards"] if b["id"] == saved_font["board_id"])
        font_board["view"]["background"] = "graph"
        assert requests.put(URL + "/api/boards/" + font_board["id"], json={"view": font_board["view"]}, headers=headers).ok
        font_state = requests.get(URL + "/api/bootstrap").json()
        assert next(b for b in font_state["boards"] if b["id"] == font_board["id"])["view"]["background"] == "graph"
        assert next(i for i in font_state["items"] if i["id"] == saved_font["item_id"])["state"]["fontFamily"] == "VBFont_" + saved_font["font"]["id"]
        duplicate_font = requests.post(URL + "/api/fonts", json=font_payload, headers=headers).json()
        assert duplicate_font["duplicate"] is True and duplicate_font["font"]["id"] == saved_font["font"]["id"]
        with zipfile.ZipFile(io.BytesIO(requests.get(URL + "/api/export", headers=headers).content)) as z:
            assert z.read("fonts/" + saved_font["font"]["filename"]) == font_bytes
        assert requests.delete(URL + "/api/items/" + chosen["item"]["id"], headers=headers).ok
        assert requests.get(URL + "/api/assets/" + first["asset"]["id"]).content == image_bytes
        assert requests.delete(URL + "/api/assets/" + first["asset"]["id"], headers=headers).ok
        trashed = requests.get(URL + "/api/bootstrap").json()
        assert not trashed["assets"] and len(trashed["trash"]) == 1
        assert all(x["asset_id"] != first["asset"]["id"] for x in trashed["items"])
        recaptured = requests.post(URL + "/api/capture", json=payload, headers=headers).json()
        assert recaptured["duplicate"] is True
        assert sum(x["asset_id"] == first["asset"]["id"] for x in requests.get(URL + "/api/bootstrap").json()["items"]) == 3
        assert requests.delete(URL + "/api/assets/" + first["asset"]["id"], headers=headers).ok
        assert requests.post(URL + "/api/assets/" + first["asset"]["id"] + "/restore", json={}, headers=headers).ok
        restored = requests.get(URL + "/api/bootstrap").json()
        assert len(restored["assets"]) == 1 and not restored["trash"]
        assert sum(x["asset_id"] == first["asset"]["id"] for x in restored["items"]) == 3
        assert requests.delete(URL + "/api/assets/" + first["asset"]["id"], headers=headers).ok
        assert requests.post(URL + "/api/trash/restore-all", json={}, headers=headers).json()["restored"] == 1
        assert requests.delete(URL + "/api/assets/" + first["asset"]["id"], headers=headers).ok
        with sqlite3.connect(Path(temp) / "clip-index.sqlite3") as db:
            db.execute("CREATE TABLE vectors (id TEXT PRIMARY KEY, filename TEXT, size INTEGER, mtime_ns INTEGER, value BLOB)")
            db.execute("INSERT INTO vectors VALUES (?,?,?,?,?)", (first["asset"]["id"], "private-image", 1, 1, b"vector"))
        assert requests.delete(URL + "/api/trash", headers=headers).json()["deleted"] == 1
        with sqlite3.connect(Path(temp) / "clip-index.sqlite3") as db:
            assert db.execute("SELECT * FROM vectors").fetchall() == []
        assert not (Path(temp) / "assets" / first["asset"]["filename"]).exists()
        assert requests.get(URL + "/api/assets/" + first["asset"]["id"]).status_code == 404
        assert not requests.get(URL + "/api/bootstrap").json()["trash"]
        assert requests.put(URL + "/api/folders/" + folder["id"], json={"name":"References"}, headers=headers).ok
        assert requests.delete(URL + "/api/folders/" + folder["id"], headers=headers).ok
        assert next(b for b in requests.get(URL + "/api/bootstrap").json()["boards"] if b["id"] == board["id"])["folder_id"] is None
        screenshot = Image.new("RGB", (100, 60), "#3466c8")
        screenshot.paste("#d23b36", (50, 0, 100, 60))
        screenshot_bytes = io.BytesIO()
        screenshot.save(screenshot_bytes, "PNG")
        clipped = requests.post(URL + "/api/capture", json={
            "image_data_url": "data:image/png;base64," + base64.b64encode(screenshot_bytes.getvalue()).decode(),
            "screen_crop": {"x": 50, "y": 0, "w": 50, "h": 60, "viewport_w": 100, "viewport_h": 60}}, headers=headers)
        assert clipped.status_code == 201, clipped.text
        clip_bytes = requests.get(URL + "/api/assets/" + clipped.json()["asset"]["id"]).content
        with Image.open(io.BytesIO(clip_bytes)) as clip:
            assert clip.size == (50, 60) and clip.getpixel((25, 30)) == (210, 59, 54)
        print("PASS capture, folders, boards, shapes, fonts, Trash/Restore/Purge, screenshot crop, auth, backup")
    finally:
        process.terminate()
        process.wait(timeout=5)
