"""One authenticated, worker-initiated WebSocket for private 3D jobs.

The Ryzen opens the connection to the existing hosted app. Browser clients
cannot supply the worker header, and no inbound route to the Ryzen is needed.
Only job inputs cross this socket; completed models use the existing callback.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import socket
import struct
import threading

MAX_JOB = 64 * 1024 * 1024 + 4096
MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _read_exact(stream, size: int) -> bytes:
    raw = stream.read(size)
    if len(raw) != size:
        raise EOFError("Worker connection ended")
    return raw


def _frame_header(opcode: int, size: int) -> bytes:
    first = 0x80 | opcode
    if size < 126:
        return bytes((first, size))
    if size <= 65535:
        return bytes((first, 126)) + struct.pack("!H", size)
    return bytes((first, 127)) + struct.pack("!Q", size)


class WorkerBridge:
    def __init__(self, token: str, enabled: bool):
        self.token = token
        self.enabled = enabled and len(token) >= 32
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._multi = False

    def connected(self, multi: bool = False) -> bool:
        with self._lock:
            return self.enabled and self._socket is not None and (not multi or self._multi)

    def serve(self, handler) -> None:
        headers = handler.headers
        key = headers.get("Sec-WebSocket-Key", "")
        try:
            decoded = base64.b64decode(key, validate=True)
        except (ValueError, binascii.Error):
            decoded = b""
        if (not self.enabled or headers.get("Origin")
                or not hmac.compare_digest(headers.get("X-Visual-Brain-Worker-Token", ""), self.token)):
            return handler.error("Forbidden", 403)
        if (handler.request_version != "HTTP/1.1" or headers.get("Upgrade", "").lower() != "websocket"
                or "upgrade" not in {part.strip().lower() for part in headers.get("Connection", "").split(",")}
                or headers.get("Sec-WebSocket-Version") != "13" or len(decoded) != 16):
            return handler.error("WebSocket upgrade required", 400)
        accept = base64.b64encode(hashlib.sha1(key.encode("ascii") + MAGIC).digest())
        handler.close_connection = True
        handler.wfile.write(b"HTTP/1.1 101 Switching Protocols\r\n"
                            b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                            b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        handler.wfile.flush()
        current = handler.connection
        with self._lock:
            previous = self._socket
            self._socket = current
            self._multi = headers.get("X-Visual-Brain-Multi-Ready") == "1"
        if previous is not None and previous is not current:
            try:
                previous.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            previous.close()
        try:
            while True:
                first, second = _read_exact(handler.rfile, 2)
                opcode = first & 0x0F
                if not first & 0x80 or not second & 0x80:
                    break  # Client frames must be final and masked.
                size = second & 0x7F
                if size == 126:
                    size = struct.unpack("!H", _read_exact(handler.rfile, 2))[0]
                elif size == 127:
                    size = struct.unpack("!Q", _read_exact(handler.rfile, 8))[0]
                if size > 125:
                    break  # The client only sends control frames.
                mask = _read_exact(handler.rfile, 4)
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(_read_exact(handler.rfile, size)))
                if opcode == 8:
                    break
                if opcode == 9:
                    with self._lock:
                        if self._socket is current:
                            current.sendall(_frame_header(10, len(payload)) + payload)
                elif opcode != 10:
                    break
        except (EOFError, OSError, ValueError):
            pass
        finally:
            with self._lock:
                if self._socket is current:
                    self._socket = None
                    self._multi = False

    def send_job(self, model_id: str, kind: str, raw: bytes) -> None:
        if kind not in {"single", "multi"} or not raw or len(raw) > MAX_JOB - 4096:
            raise ValueError("Invalid private 3D job")
        metadata = json.dumps({"model_id": model_id, "kind": kind, "size": len(raw)},
                              separators=(",", ":")).encode()
        size = 4 + len(metadata) + len(raw)
        if size > MAX_JOB:
            raise ValueError("Private 3D job is too large")
        with self._lock:
            current = self._socket
            if not self.enabled or current is None or (kind == "multi" and not self._multi):
                raise RuntimeError("The free 3D worker is offline. Retry this model after it reconnects.")
            try:
                current.sendall(_frame_header(2, size))
                current.sendall(struct.pack("!I", len(metadata)))
                current.sendall(metadata)
                current.sendall(raw)
            except OSError as error:
                self._socket = None
                self._multi = False
                current.close()
                raise RuntimeError("The free 3D worker disconnected. Retry this model.") from error
