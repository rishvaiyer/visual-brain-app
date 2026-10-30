"""Focused hosted security checks without opening a listening socket."""
import os
import sqlite3
import tempfile
import unittest
import zipfile
from io import BytesIO
from email.message import Message
from pathlib import Path


DATA_DIR = tempfile.TemporaryDirectory()
os.environ.update({
    "VISUAL_BRAIN_HOST": "0.0.0.0",
    "VISUAL_BRAIN_PUBLIC_ORIGIN": "https://visual.example",
    "VISUAL_BRAIN_PASSPHRASE": "test-only-passphrase",
    "VISUAL_BRAIN_DATA_DIR": DATA_DIR.name,
})

import hosting  # noqa: E402
import server  # noqa: E402


def handler(path, headers=None):
    h = server.Handler.__new__(server.Handler)
    h.path = path
    h.headers = Message()
    h.headers["Host"] = "visual.example"
    for name, value in (headers or {}).items():
        h.headers[name] = value
    h.client_address = ("10.0.0.5", 12345)
    h.command = "GET"
    h.responses = []
    h.json = lambda body, status=200: h.responses.append((status, body))
    h.error = lambda body, status=400: h.responses.append((status, body))
    return h


class HostedSecurityTests(unittest.TestCase):
    def owner_cookie(self):
        return f"{hosting.COOKIE_NAME}={hosting.issue(Path(DATA_DIR.name))}"

    def test_hosted_capabilities_are_private_and_match_runtime(self):
        anonymous = handler("/api/capabilities")
        anonymous.do_GET()
        self.assertEqual(anonymous.responses[0][0], 401)
        owner = handler("/api/capabilities", {"Cookie": self.owner_cookie()})
        owner.do_GET()
        self.assertEqual(owner.responses[0][0], 200)
        capabilities = owner.responses[0][1]
        self.assertEqual(capabilities["runtime"], "hosted")
        for name in ("vision_labels", "local_visual_search", "extension_capture", "demo"):
            self.assertFalse(capabilities[name], name)
        self.assertTrue(capabilities["similar_images"])
        self.assertTrue(capabilities["backup_export"])

    def test_hosted_mac_only_actions_fail_before_starting_background_work(self):
        cookie = self.owner_cookie()
        for path in ("/api/local-visual-search",):
            with self.subTest(path=path):
                owner = handler(path, {"Cookie": cookie, "Origin": "https://visual.example"})
                owner.command = "POST"
                owner.body = lambda: {}
                owner.do_POST()
                self.assertEqual(owner.responses[0][0], 501)

    def test_backup_contains_complete_database(self):
        owner = handler("/api/export", {"Cookie": self.owner_cookie()})
        owner.send_bytes = lambda body, mime="application/json", status=200, extra=None: owner.responses.append((status, body))
        owner.do_GET()
        self.assertEqual(owner.responses[0][0], 200)
        with zipfile.ZipFile(BytesIO(owner.responses[0][1])) as archive:
            self.assertIn("data/visual-brain.sqlite3", archive.namelist())
            self.assertIn("README.txt", archive.namelist())
            with tempfile.TemporaryDirectory() as temporary:
                backup_db = Path(temporary) / "backup.sqlite3"
                backup_db.write_bytes(archive.read("data/visual-brain.sqlite3"))
                with sqlite3.connect(backup_db) as db:
                    self.assertEqual(db.execute("SELECT name FROM boards WHERE id='main'").fetchone()[0], "Everything")

    def test_unknown_host_and_origin_spoofs_are_rejected(self):
        self.assertTrue(hosting.allowed_host("visual.example", server.PORT))
        self.assertFalse(hosting.allowed_host("attacker.example", server.PORT))
        self.assertFalse(hosting.allowed_host("visual.example@attacker.example", server.PORT))
        self.assertTrue(hosting.allowed_origin("https://visual.example", "visual.example"))
        self.assertFalse(hosting.allowed_origin("https://visual.example.attacker.test", "visual.example"))
        self.assertFalse(hosting.allowed_origin("null", "visual.example"))

    def test_anonymous_board_bootstrap_and_images_are_private(self):
        for path in ("/board", "/api/bootstrap", "/api/assets/example", "/api/export", "/api/capabilities"):
            with self.subTest(path=path):
                h = handler(path)
                h.do_GET()
                self.assertEqual(h.responses[0][0], 401)
        page = handler("/board", {"Accept": "text/html"})
        page.redirect = lambda location: page.responses.append((302, location))
        page.do_GET()
        self.assertEqual(page.responses[0], (302, "/login"))
        self.assertFalse(handler("/").private_read_required("/"))

    def test_private_reads_and_writes_need_owner_access(self):
        private = handler("/api/boards-list")
        private.do_GET()
        self.assertEqual(private.responses[0][0], 401)
        write = handler("/api/items")
        write.command = "POST"
        write.do_POST()
        self.assertEqual(write.responses[0][0], 403)
        leaked_key = handler("/api/items", {"X-Board-Key": server.PAIRING_KEY})
        leaked_key.command = "POST"
        leaked_key.do_POST()
        self.assertEqual(leaked_key.responses[0][0], 403)

    def test_session_logout_revokes_copied_cookie(self):
        data = Path(DATA_DIR.name)
        hosting.init(data)
        token = hosting.issue(data)
        cookie = f"{hosting.COOKIE_NAME}={token}"
        self.assertTrue(hosting.valid(cookie, data))
        hosting.revoke(cookie, data)
        self.assertFalse(hosting.valid(cookie, data))

    def test_cookie_writes_require_exact_origin(self):
        data = Path(DATA_DIR.name)
        token = hosting.issue(data)
        cookie = f"{hosting.COOKIE_NAME}={token}"
        bad = handler("/api/items", {"Cookie": cookie, "Origin": "https://attacker.example"})
        bad.command = "POST"
        self.assertFalse(bad.authorized())
        missing = handler("/api/items", {"Cookie": cookie})
        missing.command = "POST"
        self.assertFalse(missing.authorized())
        good = handler("/api/items", {"Cookie": cookie, "Origin": "https://visual.example"})
        good.command = "POST"
        self.assertTrue(good.authorized())
        safe_get = handler("/api/boards-list", {"Cookie": cookie})
        self.assertTrue(safe_get.authorized())
        hostile_get = handler("/api/boards-list", {"Cookie": cookie, "Origin": "https://attacker.example"})
        self.assertFalse(hostile_get.authorized())
        bootstrap = handler("/api/bootstrap", {"Cookie": cookie})
        bootstrap.do_GET()
        self.assertEqual(bootstrap.responses[0][1]["key"], "")
        self.assertTrue(bootstrap.responses[0][1]["can_edit"])

    def test_far_future_expiry_is_rejected_even_with_valid_signature(self):
        data = Path(DATA_DIR.name)
        expiry = str(int(hosting.time.time()) + hosting.SESSION_SECONDS + 100)
        nonce = "a" * 32
        signed = expiry + "." + nonce
        token = signed + "." + hosting._sign(hosting._load_secret(data), signed)
        self.assertFalse(hosting.valid(f"{hosting.COOKIE_NAME}={token}", data))


if __name__ == "__main__":
    unittest.main()
