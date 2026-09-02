#!/usr/bin/env python3
"""Local console server - static files plus the one endpoint SS12 writes to.

`python -m http.server` is read-only, so the review board could show what the
three parties owe each other but Andrew could not answer from the page. This
adds exactly one write path and nothing else.

  python dashboard/serve.py            # http://127.0.0.1:8787/dashboard/
  python dashboard/serve.py --port 9000

Endpoints
  GET  /...            static, rooted at the repo, so ../data/results/*.js resolve
  POST /api/review     one inbox event -> data/review/inbox.jsonl, then re-emit
  POST /api/emit       re-run the review emitter (used by the page's refresh)
  GET  /api/state      the current review_state.json

Security posture: binds 127.0.0.1 only, refuses anything but the three routes
above, and every write goes through `review.py post`, which validates the item
id and the event type. It is a local tool for one person on one machine and is
not hardened for anything else - do not bind it to 0.0.0.0.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REVIEW_PY = REPO / "dashboard" / "review.py"
STATE = REPO / "data" / "results" / "review_state.json"
MAX_BODY = 64 * 1024


class Handler(SimpleHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # The page is loaded from this same origin, so no CORS is needed; the
        # no-store is what matters - a cached build_status.js is a stale monitor.
        self.send_header("Cache-Control", "no-store")
        super(SimpleHTTPRequestHandler, self).end_headers()

    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0] == "/api/state":
            if not STATE.exists():
                return self._json(404, {"ok": False, "error": "review_state.json not written yet"})
            return self._json(200, json.loads(STATE.read_text(encoding="utf-8")))
        return super().do_GET()

    def do_POST(self):  # noqa: N802
        route = self.path.split("?")[0]
        if route not in ("/api/review", "/api/emit"):
            return self._json(404, {"ok": False, "error": f"no route {route}"})

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._json(413, {"ok": False, "error": "body too large"})
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError as e:
            return self._json(400, {"ok": False, "error": f"bad JSON: {e}"})

        if route == "/api/emit":
            rc, out = self._review("emit")
            return self._json(200 if rc == 0 else 500, {"ok": rc == 0, "output": out})

        # /api/review - one event, validated by review.py rather than here, so
        # the CLI and the page can never drift on what a legal event is.
        argv = ["post", "--item", str(body.get("item", "")),
                "--type", str(body.get("type", "")),
                "--by", str(body.get("by", "andrew"))]
        for flag in ("text", "ref", "needs"):
            if body.get(flag):
                argv += [f"--{flag}", str(body[flag])]
        if body.get("type") == "raise" or body.get("raise"):
            argv = ["raise", "--kind", str(body.get("kind", "finding")),
                    "--title", str(body.get("title", "")),
                    "--by", str(body.get("by", "andrew")),
                    "--for", str(body.get("for", "cowork"))]
            if body.get("text"):
                argv += ["--text", str(body["text"])]

        rc, out = self._review(*argv)
        if rc != 0:
            return self._json(400, {"ok": False, "error": out.strip()})
        state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None
        return self._json(200, {"ok": True, "output": out.strip(), "state": state})

    def _review(self, *argv: str) -> tuple[int, str]:
        p = subprocess.run([sys.executable, str(REVIEW_PY), *argv],
                           cwd=REPO, capture_output=True, text=True, timeout=60)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    def log_message(self, fmt, *a):
        if self.path.startswith("/api/"):
            sys.stderr.write("  %s %s\n" % (self.command, self.path))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()

    subprocess.run([sys.executable, str(REVIEW_PY), "emit"], cwd=REPO)
    handler = partial(Handler, directory=str(REPO))
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"\n  Zoltar Research Console   http://127.0.0.1:{args.port}/dashboard/")
    print(f"  Review posts enabled      POST /api/review")
    print(f"  Serving                   {REPO}")
    print("  Ctrl+C to stop.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
