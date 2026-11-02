"""Check that a multi-file upload exposes the one-object action on the board."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="visual-brain-ui-multi-") as temporary:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        photos = []
        for index in range(32):
            path = Path(temporary) / f"angle_{index:03}.jpg"
            Image.new("RGB", (80, 80), (index * 7 % 256, index * 11 % 256, index * 13 % 256)).save(path)
            photos.append(str(path))
        env = dict(os.environ, VISUAL_BRAIN_DATA_DIR=str(Path(temporary) / "app"),
                   VISUAL_BRAIN_HOST="127.0.0.1", VISUAL_BRAIN_PORT=str(port))
        app = subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=ROOT, env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            if "Visual Brain" not in app.stdout.readline():
                raise RuntimeError("Local Visual Brain did not start")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 900})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{port}/board")
                    page.locator("#file-input").set_input_files(photos)
                    action = page.locator('[data-action="one-3d-object"]')
                    action.wait_for(timeout=30000)
                    selected = page.evaluate("state.selected.size")
                    if selected != 32:
                        raise AssertionError(f"Expected all uploaded photos selected, got {selected}")
                    page.evaluate("() => { window.create3DBoard = (ids, mode) => { window.__oneObjectAction = {count: ids.length, mode}; }; }")
                    action.click()
                    if page.evaluate("window.__oneObjectAction") != {"count": 32, "mode": "one-object"}:
                        raise AssertionError("The action did not pass the selected photos as one object")
                    page.set_viewport_size({"width": 390, "height": 844})
                    if not action.is_visible():
                        raise AssertionError("The one-object action disappeared on a phone-sized screen")
                    if errors:
                        raise AssertionError(errors[0])
                    print("MULTI_UPLOAD_UI_READY")
                finally:
                    browser.close()
        finally:
            app.terminate()
            app.wait(timeout=5)
            app.stdout.close()


if __name__ == "__main__":
    main()
