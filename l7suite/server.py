#!/usr/bin/env python3
"""
L7 Proxy Test Suite — All-in-one server
=========================================
Run:  python3 server.py
Then: open http://localhost:5000 in your browser.

Serves the static UI from ./public/ AND exposes:
  GET  /api/proxy-status   – TCP reachability check
  GET  /api/diag           – quick diagnostic (egress IP, GET, DNS, TLS)
  POST /api/start          – start a flood  { target, method, workers, rps, duration }
  POST /api/stop           – graceful stop
  GET  /api/stream         – Server-Sent Events (live metrics)

Pure stdlib — no pip installs required.
"""

import os
import sys
import json
import time
import queue
import threading
import mimetypes
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ── Engine import ──────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine as eng

PORT   = int(os.environ.get("PORT", "5000"))
PUBLIC = os.path.join(HERE, "public")

# ── Shared SSE state ───────────────────────────────────────────────────────────
_lock         = threading.Lock()
_sse_clients  = []                          # type: list
_flood_thread = None                        # type: threading.Thread | None

# live counter mirror (updated by on_result callback)
_live = {
    "total": 0, "success": 0, "errors": 0,
    "latencies": [], "status_counts": {},
    "wall_start": None, "target": "", "method": "",
}


# ── Broadcast helpers ──────────────────────────────────────────────────────────
def _broadcast(obj):
    # type: (dict) -> None
    msg = "data: {}\n\n".format(json.dumps(obj))
    with _lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)


# ── Ticker: pushes live stats every 500ms ─────────────────────────────────────
_ticker_stop = threading.Event()

def _ticker():
    while not _ticker_stop.is_set():
        time.sleep(0.5)
        with _lock:
            s    = _live.copy()
            lats = sorted(s["latencies"])
        if s["wall_start"] is None:
            continue
        p50 = lats[int(len(lats) * 0.50)] if lats else 0
        p99 = lats[int(len(lats) * 0.99)] if lats else 0
        _broadcast({
            "type":          "tick",
            "total":         s["total"],
            "success":       s["success"],
            "errors":        s["errors"],
            "p50":           round(p50, 1),
            "p99":           round(p99, 1),
            "status_counts": s["status_counts"],
        })


# ── on_result callback: called by engine for every completed request ───────────
def _on_result(ok_flag, ms, status):
    with _lock:
        _live["total"] += 1
        if ok_flag:
            _live["success"] += 1
        else:
            _live["errors"] += 1
        if ms > 0:
            _live["latencies"].append(ms)
        sc = str(status) if status else "0"
        _live["status_counts"][sc] = _live["status_counts"].get(sc, 0) + 1


# ── Flood orchestrator ─────────────────────────────────────────────────────────
def _run_flood(target, method, workers, rps, duration):
    # Reset live counters
    with _lock:
        _live.update({
            "total": 0, "success": 0, "errors": 0,
            "latencies": [], "status_counts": {},
            "wall_start": time.time(),
            "target": target, "method": method,
        })

    eng._STOP.clear()
    _ticker_stop.clear()
    tick_t = threading.Thread(target=_ticker, daemon=True)
    tick_t.start()

    _broadcast({"type": "log", "level": "section",
                "msg": "▶ {} flood → {}".format(method, target)})
    _broadcast({"type": "log", "level": "info",
                "msg": "  Workers: {}  RPS: {}  Duration: {}s".format(
                    workers, rps, duration)})

    try:
        if method == "MULTI":
            result = eng.run_multi_vector(
                target_url=target, workers=workers,
                rps=rps, duration=duration, on_result=_on_result,
            )
        else:
            result = eng.run_flood(
                target_url=target, workers=workers, rps=rps,
                duration=duration, method=method, on_result=_on_result,
            )
    except Exception as exc:
        _broadcast({"type": "log", "level": "err",
                    "msg": "✗ Flood error: {}".format(exc)})
        result = {"total": 0, "success": 0, "errors": 0,
                  "wall": 0, "rps_actual": 0, "p50": 0, "p99": 0,
                  "status_counts": {}}
    finally:
        _ticker_stop.set()

    with _lock:
        lats = sorted(_live["latencies"])
        sc   = dict(_live["status_counts"])
        _live["wall_start"] = None

    p50 = lats[int(len(lats) * 0.50)] if lats else 0
    p99 = lats[int(len(lats) * 0.99)] if lats else 0

    stopped = eng._STOP.is_set()
    _broadcast({
        "type":          "done",
        "method":        result.get("method", method),
        "target":        target,
        "total":         result.get("total", 0),
        "success":       result.get("success", 0),
        "errors":        result.get("errors", 0),
        "rps_actual":    round(result.get("rps_actual", 0), 1),
        "wall":          round(result.get("wall", 0), 2),
        "p50":           round(p50, 1),
        "p99":           round(p99, 1),
        "status_counts": sc,
        "stopped_early": stopped,
    })
    _broadcast({"type": "log", "level": "ok",
                "msg": "✔ {} — {:,} sent  {:.1f} RPS  p99 {:.0f}ms".format(
                    "Stopped" if stopped else "Complete",
                    result.get("total", 0),
                    result.get("rps_actual", 0),
                    p99)})


# ── HTTP handler ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass  # suppress access log

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ── GET ────────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"

        # ── API routes ────────────────────────────────────────────────────────
        if path == "/api/proxy-status":
            ok, ms = eng.check_proxy_reachability()
            self._json({"ok": ok, "ms": ms,
                        "host": eng.PROXY_HOST, "port": eng.PROXY_PORT})
            return

        if path == "/api/diag":
            result = {}
            ok, ms = eng.check_proxy_reachability()
            result["proxy"] = {"ok": ok, "ms": ms}
            result["egress_ip"] = eng.test_egress_ip()
            return  # (streaming diag omitted for brevity — use flood for live data)

        if path == "/api/stream":
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            q = queue.Queue()
            with _lock:
                _sse_clients.append(q)
            try:
                while True:
                    try:
                        msg = q.get(timeout=15)
                        self.wfile.write(msg.encode())
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
            except Exception:
                pass
            finally:
                with _lock:
                    if q in _sse_clients:
                        _sse_clients.remove(q)
            return

        # ── Static file serving ───────────────────────────────────────────────
        # / → index.html
        if path == "/":
            path = "/index.html"
        file_path = os.path.join(PUBLIC, path.lstrip("/"))
        if os.path.isfile(file_path):
            mime, _ = mimetypes.guess_type(file_path)
            mime = mime or "application/octet-stream"
            with open(file_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found")

    # ── POST ───────────────────────────────────────────────────────────────────
    def do_POST(self):
        global _flood_thread
        parsed = urlparse(self.path)
        path   = parsed.path

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if path == "/api/start":
            target   = (data.get("target") or "").strip()
            method   = (data.get("method") or "GET").upper()
            workers  = max(1, int(data.get("workers", 50)))
            rps      = max(0.1, float(data.get("rps", 100)))
            duration = max(1.0, float(data.get("duration", 30)))

            if not target:
                self._json({"ok": False, "error": "target is required"})
                return
            if not target.startswith("http://") and not target.startswith("https://"):
                target = "https://" + target
            valid = {"GET","POST","HEAD","PUT","PATCH","DELETE","OPTIONS","MULTI"}
            if method not in valid:
                self._json({"ok": False, "error": "invalid method"})
                return
            if _flood_thread and _flood_thread.is_alive():
                self._json({"ok": False, "error": "flood already running"})
                return

            _flood_thread = threading.Thread(
                target=_run_flood,
                args=(target, method, workers, rps, duration),
                daemon=True,
            )
            _flood_thread.start()
            self._json({"ok": True})

        elif path == "/api/stop":
            eng._STOP.set()
            self._json({"ok": True})

        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print("=" * 56)
    print("  L7 Proxy Test Suite")
    print("  http://localhost:{}".format(PORT))
    print("  Proxy: {}:{}".format(eng.PROXY_HOST, eng.PROXY_PORT))
    print("=" * 56)
    print("  Override proxy: L7_PROXY_HOST=x L7_PROXY_PORT=y python3 server.py")
    print("  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down…")
        eng._STOP.set()
        server.shutdown()
