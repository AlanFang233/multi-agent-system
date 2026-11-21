#!/usr/bin/env python3
"""Operator harness: serve the team's dashboard in LIVE mode on :8200.

/api/*  -> the team's analytics_api WSGI app (reads the real bookings.db)
other   -> static files from artifacts/mid_test_local/dashboard_live/
           (a COPY of the team's dashboard with live-mode injected; the team's
           deliverables under mcp_workspace are never modified)
"""
import os
import sys
from pathlib import Path
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parents[1]
WS = ROOT / "mcp_workspace" / "sandboxes" / "258c8916685642f3a261281a8ffde149"
LIVE = ROOT / "artifacts" / "mid_test_local" / "dashboard_live"

sys.path.insert(0, str(WS))
os.chdir(str(WS))
import analytics_api  # noqa: E402  (team module, needs cwd=WS for bookings.db)

api_app = analytics_api.create_app()

CTYPES = {".html": "text/html", ".js": "text/javascript",
          ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml"}


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    if path.startswith("/api/"):
        return api_app(environ, start_response)
    rel = path.lstrip("/") or "index.html"
    f = (LIVE / rel).resolve()
    if not str(f).startswith(str(LIVE.resolve())) or not f.is_file():
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found"]
    data = f.read_bytes()
    ctype = CTYPES.get(f.suffix, "application/octet-stream")
    start_response("200 OK", [("Content-Type", ctype + "; charset=utf-8"),
                              ("Content-Length", str(len(data)))])
    return [data]


if __name__ == "__main__":
    print("serving dashboard (live mode) on http://0.0.0.0:8200", flush=True)
    make_server("0.0.0.0", 8200, app).serve_forever()
