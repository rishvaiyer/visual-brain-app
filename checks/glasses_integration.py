"""Companion API checks. Run on the remote Linux test host, never a user camera."""
import base64
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

TEMPORARY = tempfile.TemporaryDirectory(prefix="visual-brain-companion-test-")
os.environ.update(VISUAL_BRAIN_DATA_DIR=TEMPORARY.name, VISUAL_BRAIN_HOST="0.0.0.0",
                  VISUAL_BRAIN_PASSPHRASE="test-passphrase-not-a-real-secret",
                  VISUAL_BRAIN_PUBLIC_ORIGIN="https://library.example",
                  VISUAL_BRAIN_TRUST_PROXY="1", VISUAL_BRAIN_RELIEF="0")
os.environ.pop("VISUAL_BRAIN_DEMO", None)
os.environ.pop("ELEVENLABS_API_KEY", None)
os.environ.pop("ELEVENLABS_VOICE_ID", None)
from PIL import Image
import server
import hosting
from http.server import ThreadingHTTPServer


class CompanionAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hosting.init(server.DATA)
        output = io.BytesIO()
        Image.new("RGB", (1024, 768), "navy").save(output, format="PNG")
        cls.frame = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
        (server.ASSETS / "reference.png").write_bytes(output.getvalue())
        with server.connection() as db:
            db.execute("INSERT INTO assets(id,sha256,mime,filename,title,tags,collection,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       ("reference", "test-image-hash", "image/png", "reference.png", "Navy reference", "[]", "", server.now()))
            db.execute("INSERT INTO boards(id,name,view,created_at,updated_at) VALUES(?,?,?,?,?)",
                       ("reference-board", "Saved inspiration", "{}", server.now(), server.now()))
            db.execute("INSERT INTO items VALUES(?,?,?,?,?,?)",
                       ("placed-reference", "reference-board", "image", "reference", "{}", server.now()))
            db.commit()
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        status, headers, _ = cls.request("POST", "/api/login", "passphrase=test-passphrase-not-a-real-secret",
                                        content_type="application/x-www-form-urlencoded")
        assert status == 302
        cls.cookie = headers["Set-Cookie"].split(";", 1)[0]

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        TEMPORARY.cleanup()

    @classmethod
    def request(cls, method, path, body=None, *, authenticated=False, origin="https://library.example", content_type="application/json"):
        headers = {"Host": "library.example", "Origin": origin, "X-Forwarded-Proto": "https"}
        if authenticated:
            headers["Cookie"] = cls.cookie
        if body is not None:
            headers["Content-Type"] = content_type
            if not isinstance(body, str):
                body = json.dumps(body)
        connection = http.client.HTTPConnection("127.0.0.1", cls.http.server_port, timeout=4)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_owner_status_and_anonymous_privacy(self):
        self.assertEqual(self.request("GET", "/api/glasses/status")[0], 401)
        status, _, data = self.request("GET", "/api/glasses/status", authenticated=True)
        self.assertEqual(status, 200)
        result = json.loads(data)
        self.assertTrue(result["private_library"])
        self.assertFalse(result["voice_available"])
        self.assertFalse(result["frame_saved"])

    def test_frames_match_saved_boards_without_being_stored(self):
        before = sorted(str(p.relative_to(server.DATA)) for p in server.DATA.rglob("*") if p.is_file())
        status, _, data = self.request("POST", "/api/camera-match", {"image_data_url": self.frame}, authenticated=True)
        self.assertEqual(status, 200, data)
        result = json.loads(data)
        self.assertFalse(result["frame_saved"])
        self.assertEqual(result["method"], "appearance")
        self.assertEqual(result["matches"][0]["title"], "Navy reference")
        self.assertEqual(result["matches"][0]["board_names"], [{"id": "reference-board", "name": "Saved inspiration"}])
        self.assertEqual(result["match_state"], "close")
        after = sorted(str(p.relative_to(server.DATA)) for p in server.DATA.rglob("*") if p.is_file())
        self.assertEqual(before, after)
        with server.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 1)

    def test_companion_thumbnails_remain_private_and_small(self):
        path = "/api/assets/reference/thumbnail"
        self.assertEqual(self.request("GET", path)[0], 401)
        status, headers, data = self.request("GET", path, authenticated=True)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        with Image.open(io.BytesIO(data)) as image:
            self.assertLessEqual(max(image.size), 384)
        self.assertEqual(self.request("GET", "/api/assets/missing/thumbnail", authenticated=True)[0], 404)

    def test_wrong_origin_and_invalid_images_are_refused(self):
        self.assertEqual(self.request("POST", "/api/camera-match", {"image_data_url": self.frame},
                                     authenticated=True, origin="https://other.example")[0], 403)
        self.assertEqual(self.request("POST", "/api/camera-match", {"image_data_url": "bad"}, authenticated=True)[0], 400)
        self.assertEqual(self.request("POST", "/api/camera-match", [], authenticated=True)[0], 400)

    def test_no_credentials_means_voice_unavailable_without_provider_call(self):
        status, _, data = self.request("POST", "/api/glasses/voice", {"text": "Saved inspiration"}, authenticated=True)
        self.assertEqual(status, 503)
        self.assertIn("Natural voice is unavailable", json.loads(data)["error"])


if __name__ == "__main__":
    unittest.main()
