"""Local web server: serves the map UI and re-solves plans on request.

Run with:  uv run python -m planner.server   then open http://localhost:8765
"""

import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from planner.solve import DEFAULTS, ROOT, load_addresses, solve

PORT = 8765
PLAN = ROOT / "out" / "plan.json"
_lock = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def _json(self, obj, status=200):
        body = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.path = "/web/index.html"
        elif self.path == "/api/plan":
            if not PLAN.exists():
                with _lock:
                    PLAN.write_text(json.dumps(solve(), separators=(",", ":")))
            self.path = "/out/plan.json"
        elif self.path == "/api/towns":
            counts: dict[str, int] = {}
            for a in load_addresses():
                counts[a["town"]] = counts.get(a["town"], 0) + 1
            return self._json({"towns": counts, "defaults": DEFAULTS})
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/solve":
            return self._json({"error": "not found"}, 404)
        length = int(self.headers.get("Content-Length", 0))
        try:
            overrides = json.loads(self.rfile.read(length) or b"{}")
            with _lock:
                plan = solve(overrides)
                PLAN.write_text(json.dumps(plan, separators=(",", ":")))
        except Exception as e:  # surface solver errors in the UI
            return self._json({"error": str(e)}, 400)
        return self._json(plan)

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


def main():
    print(f"Quiet Route planner: http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
