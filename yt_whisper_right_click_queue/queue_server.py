#!/usr/bin/env python3
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOST = "127.0.0.1"
PORT = 8765

# Save beside this helper. Put this helper in the same folder as your queue file.
QUEUE_FILE = Path(__file__).resolve().parent / "to-be-transcribed.txt"


def valid_url(url: str) -> bool:
    try:
        p = urlparse(url.strip())
        return p.scheme in {"http", "https"} and bool(p.netloc)
    except Exception:
        return False


def append_if_new(url: str):
    url = url.strip()
    existing = set()
    if QUEUE_FILE.exists():
        existing = {
            line.strip()
            for line in QUEUE_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

    if url in existing:
        return False, "Link is already in the queue."

    needs_newline = QUEUE_FILE.exists() and QUEUE_FILE.stat().st_size > 0
    with QUEUE_FILE.open("a", encoding="utf-8") as f:
        if needs_newline:
            f.write("\n")
        f.write(url)

    return True, "Added"


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/add":
            self.send_json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            url = str(data.get("url", "")).strip()

            if not valid_url(url):
                self.send_json(400, {"added": False, "reason": "Not a valid web link."})
                return

            added, reason = append_if_new(url)
            self.send_json(200, {"added": added, "reason": reason})
            print(("ADDED: " if added else "SKIP : ") + url, flush=True)
        except Exception as exc:
            self.send_json(500, {"added": False, "reason": str(exc)})

    def log_message(self, *args):
        pass


print("YT Whisper right-click queue helper")
print(f"Queue file : {QUEUE_FILE}")
print(f"Listening  : http://{HOST}:{PORT}")
print("Leave this window open while browsing.")
print("Press Ctrl+C to stop.\n")

try:
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
except KeyboardInterrupt:
    print("\nStopped.")
