"""Fly.io process wrapper: minimal health endpoint + Alfred child lifecycle."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_started_at = time.time()
_child: subprocess.Popen[str] | None = None


class HealthHandler(BaseHTTPRequestHandler):
    server_version = "AlfredHealth/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/healthz", "/readyz"}:
            self.send_response(404)
            self.end_headers()
            return
        alive = _child is not None and _child.poll() is None
        status = 200 if alive else 503
        body = json.dumps({"status": "ok" if alive else "starting"}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _serve_health() -> None:
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever(poll_interval=0.5)


def _forward(signum: int, _frame: object) -> None:
    if _child is not None and _child.poll() is None:
        _child.send_signal(signum)


def main() -> int:
    global _child
    threading.Thread(target=_serve_health, name="health", daemon=True).start()
    signal.signal(signal.SIGTERM, _forward)
    signal.signal(signal.SIGINT, _forward)

    # Fly already restarts failed Machines; avoid a nested supervisor loop.
    _child = subprocess.Popen(
        [sys.executable, "-u", "/app/bridge.py"],
        cwd="/app",
        env=os.environ.copy(),
        text=True,
    )
    return _child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
