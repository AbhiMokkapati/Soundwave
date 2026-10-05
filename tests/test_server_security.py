"""
Guards on the local web app: it can read/rename/delete files by path with no
login, so requests from other sites or non-loopback Host headers must be
refused, and file-serving endpoints must only serve audio. Drives the ASGI
app directly (no httpx / network needed).
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
server = pytest.importorskip("webapp.server")


def _call(path, query=b"", headers=None, method="GET"):
    hdrs = {"host": "127.0.0.1:8765"}
    hdrs.update(headers or {})
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": query, "root_path": "", "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8765),
        "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()],
    }
    out = {"status": None, "body": b""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    asyncio.run(server.app(scope, receive, send))
    return out["status"], out["body"]


def test_rejects_non_loopback_host():
    status, _ = _call("/api/config", headers={"host": "evil.example:8765"})
    assert status == 403


def test_rejects_cross_site_request():
    status, _ = _call("/api/config", headers={"sec-fetch-site": "cross-site"})
    assert status == 403


def test_rejects_foreign_origin():
    status, _ = _call("/api/config", headers={"origin": "https://evil.example"})
    assert status == 403


def test_same_origin_allowed():
    status, _ = _call("/api/config", headers={"sec-fetch-site": "same-origin"})
    assert status == 200


def test_audio_endpoint_refuses_non_audio(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    status, body = _call("/api/track/audio", query=f"path={secret}".encode())
    assert status == 400
    assert b"x" != body
