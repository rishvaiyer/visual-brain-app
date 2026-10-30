#!/usr/bin/env python3
"""Hosted mode: an owner passphrase guards the board, or a public demo opens it to everyone."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

COOKIE_NAME = "vb_session"
#: How long a session lasts before the passphrase is asked for again.
SESSION_SECONDS = 30 * 24 * 60 * 60
#: A real token is well under this. Anything longer is refused before it is
#: parsed, so a cookie with thousands of digits in its expiry cannot be turned
#: into an unbounded int conversion.
MAX_TOKEN_LENGTH = 160

PASSPHRASE = os.environ.get("VISUAL_BRAIN_PASSPHRASE", "")

#: Public playground. Everyone may edit, nothing is private, and the server
#: wipes its data on a timer. Never combine this with a board you care about.
DEMO = os.environ.get("VISUAL_BRAIN_DEMO", "") == "1"

#: Hosted logins require HTTPS. The passphrase and the session cookie both
#: travel in the clear otherwise, and the cookie cannot be marked Secure. Set
#: VISUAL_BRAIN_ALLOW_INSECURE=1 only for a deployment you know terminates TLS
#: somewhere this process cannot see.
ALLOW_INSECURE = os.environ.get("VISUAL_BRAIN_ALLOW_INSECURE", "") == "1"
PUBLIC_ORIGIN = os.environ.get("VISUAL_BRAIN_PUBLIC_ORIGIN", "").rstrip("/")
if not PUBLIC_ORIGIN and os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
    PUBLIC_ORIGIN = "https://" + os.environ["RAILWAY_PUBLIC_DOMAIN"]
TRUST_PROXY = os.environ.get("VISUAL_BRAIN_TRUST_PROXY", "") == "1" or bool(os.environ.get("RAILWAY_ENVIRONMENT_ID"))

#: Reachable without a session: the front door itself and what it needs to render.
PUBLIC_PATHS = frozenset({"/login", "/api/login", "/healthz", "/favicon.svg"})

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})

_secret: bytes | None = None
_sessions_lock = threading.Lock()


def _sessions(data_dir: Path) -> sqlite3.Connection:
    db = sqlite3.connect(data_dir / "hosted-sessions.sqlite3", timeout=5)
    db.execute("CREATE TABLE IF NOT EXISTS sessions (id_hash TEXT PRIMARY KEY, expires INTEGER NOT NULL)")
    return db


def allowed_host(host_header: str, port: int) -> bool:
    """Only known local names or the configured public hostname may reach the app."""
    if not host_header or any(c in host_header for c in "/@\\?# "):
        return False
    try:
        parsed = urlsplit("http://" + host_header)
        host, supplied_port = parsed.hostname, parsed.port
    except ValueError:
        return False
    if host in LOOPBACK and supplied_port in (None, port):
        return True
    if not hosted() or not PUBLIC_ORIGIN:
        return False
    public = urlsplit(PUBLIC_ORIGIN)
    if public.scheme != "https" or not public.hostname or public.username or public.password:
        return False
    return host == public.hostname and supplied_port == public.port


def allowed_origin(origin: str, host_header: str) -> bool:
    """An unsafe browser request must come from the exact served origin."""
    if not origin or origin == "null":
        return False
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        return False
    if PUBLIC_ORIGIN and origin == PUBLIC_ORIGIN:
        return True
    return parsed.scheme == "http" and parsed.netloc == host_header and parsed.hostname in LOOPBACK


def hosted() -> bool:
    """True when the server binds beyond the local machine."""
    return not is_loopback(os.environ.get("VISUAL_BRAIN_HOST", "127.0.0.1"))


def is_loopback(address: str) -> bool:
    return address in LOOPBACK


def check_bind(host: str) -> str | None:
    """
    The interlock.

    Returns an error message when this configuration would publish the app with
    no way to keep anyone out, or None when the bind is safe. Called before the
    socket is opened, so the mistake is impossible rather than merely discouraged.
    """
    if is_loopback(host):
        return None
    if hosted() and PUBLIC_ORIGIN and (PASSPHRASE or DEMO):
        return None
    return (
        f"Refusing to listen on {host} without a passphrase and a public origin.\n"
        "\n"
        "Off 127.0.0.1 there is nothing between your saved images and the\n"
        "internet without a front door.\n"
        "\n"
        "Set VISUAL_BRAIN_PASSPHRASE and VISUAL_BRAIN_PUBLIC_ORIGIN (or RAILWAY_PUBLIC_DOMAIN),\n"
        "set VISUAL_BRAIN_DEMO=1 for a throwaway public playground, or leave\n"
        "VISUAL_BRAIN_HOST unset to keep the server on this machine only."
    )


def _load_secret(data_dir: Path) -> bytes:
    """Reads, or creates, the key that signs session cookies."""
    global _secret
    if _secret is not None:
        return _secret
    file = data_dir / "session-secret.txt"
    try:
        _secret = file.read_text(encoding="utf-8").strip().encode()
        if _secret:
            return _secret
    except OSError:
        pass
    _secret = secrets.token_urlsafe(48).encode()
    try:
        file.write_text(_secret.decode(), encoding="utf-8")
        file.chmod(0o600)
    except OSError:
        # An unwritable data directory means sessions reset on restart. That is
        # an inconvenience, not a hole, so it must not stop the server booting.
        pass
    return _secret


def init(data_dir: Path) -> None:
    """Prepares the signing secret. Safe to call when not hosted."""
    if hosted():
        _load_secret(data_dir)
        with _sessions(data_dir):
            pass


def passphrase_ok(supplied: str) -> bool:
    """Constant-time passphrase comparison."""
    if not PASSPHRASE:
        return False
    return hmac.compare_digest(supplied.encode(), PASSPHRASE.encode())


def _generation() -> bytes:
    """
    A fingerprint of the current passphrase, mixed into every signature.

    Changing the passphrase changes this, which invalidates every token already
    issued. Without it a copied cookie stayed usable for its full thirty days no
    matter what the owner did, because the token signs only an expiry and logout
    can do no more than clear one browser's copy.
    """
    return hashlib.sha256(b"vb-generation:" + PASSPHRASE.encode()).digest()


def _sign(secret: bytes, expiry: str) -> str:
    mac = hmac.new(secret, expiry.encode() + b"." + _generation(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def issue(data_dir: Path) -> str:
    """Builds a signed `expiry.signature` token for the session cookie."""
    expiry = str(int(time.time()) + SESSION_SECONDS)
    nonce = secrets.token_urlsafe(24)
    signed = expiry + "." + nonce
    with _sessions_lock, _sessions(data_dir) as db:
        db.execute("DELETE FROM sessions WHERE expires < ?", (int(time.time()),))
        db.execute("INSERT INTO sessions VALUES (?, ?)", (hashlib.sha256(nonce.encode()).hexdigest(), int(expiry)))
    return f"{signed}.{_sign(_load_secret(data_dir), signed)}"


def valid(cookie_header: str, data_dir: Path) -> bool:
    """True when the request carries a session cookie this server signed and that has not expired."""
    if not hosted() or DEMO:
        return True
    if not cookie_header or len(cookie_header) > 8192:
        return False
    try:
        jar = SimpleCookie()
        jar.load(cookie_header)
        token = jar[COOKIE_NAME].value
    except Exception:  # a malformed or absent cookie must not raise
        return False
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    expiry, nonce, signature = parts
    # `isdigit` is true for digits of any length, and int() on a few thousand of
    # them is work an unauthenticated caller should not get to ask for.
    if not expiry.isascii() or not expiry.isdigit() or len(expiry) > 12 or len(nonce) < 16 or len(signature) != 43:
        return False
    try:
        now = int(time.time())
        if int(expiry) < now or int(expiry) > now + SESSION_SECONDS:
            return False
    except ValueError:
        return False
    if not hmac.compare_digest(_sign(_load_secret(data_dir), expiry + "." + nonce), signature):
        return False
    with _sessions_lock, _sessions(data_dir) as db:
        return db.execute("SELECT 1 FROM sessions WHERE id_hash=? AND expires=?",
                          (hashlib.sha256(nonce.encode()).hexdigest(), int(expiry))).fetchone() is not None


def revoke(cookie_header: str, data_dir: Path) -> None:
    """Invalidate the presented session on logout, including copied cookies."""
    if not valid(cookie_header, data_dir):
        return
    jar = SimpleCookie()
    jar.load(cookie_header)
    nonce = jar[COOKIE_NAME].value.split(".")[1]
    with _sessions_lock, _sessions(data_dir) as db:
        db.execute("DELETE FROM sessions WHERE id_hash=?", (hashlib.sha256(nonce.encode()).hexdigest(),))


def cookie_header(token: str, secure: bool) -> str:
    """
    The Set-Cookie value for a new session.

    HttpOnly keeps it away from page scripts, SameSite=Lax keeps it off
    cross-site requests, and Secure is set whenever the request arrived over
    HTTPS so it cannot leak over a plain connection.
    """
    parts = [
        f"{COOKIE_NAME}={token}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={SESSION_SECONDS}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie_header() -> str:
    return f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Visual Brain</title>
<link rel="icon" href="/favicon.svg">
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100dvh; display: grid; place-items: center;
    padding: 24px;
    font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    background: #f4f1ec; color: #1d1a16;
  }
  @media (prefers-color-scheme: dark) { body { background: #16140f; color: #efe9de; } }
  form {
    width: min(360px, 100%); display: grid; gap: 14px;
    background: #fffdf9; border: 1px solid #ddd5c7; border-radius: 14px; padding: 28px;
    box-shadow: 0 24px 50px -30px rgb(60 40 20 / .45);
  }
  @media (prefers-color-scheme: dark) { form { background: #221e18; border-color: #3b342a; } }
  h1 { margin: 0; font-size: 1.25rem; letter-spacing: -.02em; }
  p { margin: 0; font-size: .875rem; color: #5f574a; }
  @media (prefers-color-scheme: dark) { p { color: #b3a894; } }
  label { font-size: .8125rem; font-weight: 600; }
  input {
    width: 100%; min-height: 44px; padding: 0 12px; font: inherit; color: inherit;
    background: transparent; border: 1px solid #c9bda9; border-radius: 9px;
  }
  button {
    min-height: 44px; font: inherit; font-weight: 600; cursor: pointer;
    background: #6b4a32; color: #fffaf3; border: 0; border-radius: 9px;
  }
  button:hover { filter: brightness(1.1); }
  .home-link { color: inherit; font-size: .875rem; text-align: center; }
  .err { color: #8d2616; font-size: .8125rem; }
  @media (prefers-color-scheme: dark) { .err { color: #f58d7a; } }
</style></head>
<body>
  <form method="POST" action="/api/login">
    <h1>Visual Brain</h1>
    <p>This board is private. Enter the passphrase to continue.</p>
    <label for="p">Passphrase</label>
    <input id="p" name="passphrase" type="password" autocomplete="current-password" autofocus required>
    __ERROR__
    <button type="submit">Open</button>
    <a class="home-link" href="/">Back</a>
  </form>
</body></html>
"""


def login_page(error: str = "") -> bytes:
    block = f'<p class="err">{error}</p>' if error else ""
    return LOGIN_PAGE.replace("__ERROR__", block).encode()
