#!/usr/bin/env python3
"""Carry private 3D jobs from Railway to the Ryzen over one outbound socket.

No public route to the Ryzen is needed. The existing loopback worker validates
each image/archive and posts the finished model to the existing HTTPS callback.
"""

from __future__ import annotations

import json
import os
import struct
from urllib.parse import urlparse

import requests
import websocket

import free_3d
import free_3d_worker
import outbound_3d


def websocket_url() -> str:
    origin = free_3d_worker.CALLBACK_ORIGIN
    parsed = urlparse(origin)
    if parsed.scheme == "https":
        return "wss://" + parsed.netloc + "/api/3d/worker/socket"
    if (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            and os.environ.get("VISUAL_BRAIN_WORKER_TEST_LOOPBACK") == "1"):
        return "ws://" + parsed.netloc + "/api/3d/worker/socket"
    raise RuntimeError("The outbound 3D connection needs an HTTPS board address")


def decode_job(packet: bytes) -> tuple[str, str, bytes]:
    if not isinstance(packet, bytes) or not 8 <= len(packet) <= outbound_3d.MAX_JOB:
        raise ValueError("Invalid private 3D packet")
    header_size = struct.unpack("!I", packet[:4])[0]
    if not 1 <= header_size <= 512 or len(packet) < 4 + header_size:
        raise ValueError("Invalid private 3D header")
    try:
        header = json.loads(packet[4:4 + header_size])
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Invalid private 3D header") from None
    if not isinstance(header, dict):
        raise ValueError("Invalid private 3D header")
    model_id, kind, size = header.get("model_id"), header.get("kind"), header.get("size")
    raw = packet[4 + header_size:]
    if (not isinstance(model_id, str) or not free_3d.MODEL_ID.fullmatch(model_id)
            or kind not in {"single", "multi"} or type(size) is not int or size != len(raw)
            or not 1 <= size <= (free_3d.MAX_MULTI_ARCHIVE if kind == "multi" else 8 * 1024 * 1024)):
        raise ValueError("Invalid private 3D job")
    return model_id, kind, raw


def forward_job(model_id: str, kind: str, raw: bytes) -> None:
    headers = {"Content-Type": "application/zip" if kind == "multi" else "image/jpeg",
               "X-Visual-Brain-Worker-Token": free_3d_worker.TOKEN,
               "X-Visual-Brain-Model-Id": model_id}
    if kind == "multi":
        headers["X-Visual-Brain-Job-Kind"] = "multi"
    try:
        response = requests.post(f"http://127.0.0.1:{free_3d_worker.PORT}/jobs", data=raw,
                                 headers=headers, timeout=(3, 35), allow_redirects=False)
        if response.status_code in (202, 409):
            return  # The worker accepted this job or is already processing it.
    except requests.RequestException:
        pass
    free_3d_worker.callback(model_id, json.dumps({"error": "The free 3D worker is offline. Retry this model."}).encode(),
                            "application/json")


def run() -> None:
    if not free_3d_worker.configured():
        raise SystemExit("The private 3D worker is not ready")
    connection = websocket.create_connection(
        websocket_url(), timeout=20, suppress_origin=True,
        header={"X-Visual-Brain-Worker-Token": free_3d_worker.TOKEN,
                "X-Visual-Brain-Multi-Ready": "1" if free_3d.multi_local_available() else "0"})
    try:
        connection.settimeout(None)  # Block on socket events; no scheduled polling or heartbeat.
        while True:
            packet = connection.recv()
            if packet is None:
                raise RuntimeError("The board connection closed")
            model_id, kind, raw = decode_job(packet)
            forward_job(model_id, kind, raw)
    finally:
        connection.close()


if __name__ == "__main__":
    run()
