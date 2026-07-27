#!/usr/bin/env python3
"""
L7 Proxy Test Suite — Web Backend
===================================
Runs a lightweight HTTP server on port 5000.
Exposes:
  GET  /proxy-status   – check Squid TCP reachability
  POST /start          – launch a flood (JSON body)
  POST /stop           – graceful stop (sets _STOP event)
  GET  /stream         – Server-Sent Events with live metrics

No third-party dependencies — pure stdlib (http.server + threading).
"""

import sys
import os
import json
import time
import threading
import socket

# Add the parent directory so we can import l7_proxy_tester
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import l7_proxy_tester as l7
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

PORT = 5000

# ── Shared state ──────────────────────────────────────────────────────────────
_lock          = threading.Lock()
_flood_thread  = None           # Thread running the current flood
_sse_clients   = []             # list of queue.Queue per SSE connection
_current_stats = {              # live stats visible to all SSE streams
    "total": 0, "success": 0, "errors": 0,
    "latencies": [], "status_counts": {},
    "wall_start": None, "target": "", "method": "",
}

# ── Import queue ──────────────────────────────────────────────────────────────
import queue as _queue_mod


def _broadcast(event_dict):
    """Push a JSON event to every connected SSE client."""
    msg = "data: {}\n\n".format(json.dumps(event_dict))
    with _lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)


# ── Ticker thread: sends live stats every 500 ms while a flood is running ─────
_ticker_stop = threading.Event()

def _ticker():
    while not _ticker_stop.is_set():
        time.sleep(0.5)
        with _lock:
            s = _current_stats.copy()
            lats = sorted(s["latencies"])
        if s["wall_start"] is None:
            continue
        p50 = lats[int(len(lats)*0.50)] if lats else 0
        p99 = lats[int(len(lats)*0.99)] if lats else 0
        _broadcast({
            "type":         "tick",
            "total":        s["total"],
            "success":      s["success"],
            "errors":       s["errors"],
            "p50":          round(p50, 1),
            "p99":          round(p99, 1),
            "status_counts": s["status_counts"],
        })


# ── Flood runner ──────────────────────────────────────────────────────────────
def _run_flood(target, method, workers, rps, duration):
    """
    Monkey-patches l7._single_request to intercept results and push them
    to SSE clients in real time, then broadcasts a 'done' event at the end.
    """
    # Reset state
    with _lock:
        _current_stats.update({
            "total": 0, "success": 0, "errors": 0,
            "latencies": [], "status_counts": {},
            "wall_start": time.time(),
            "target": target, "method": method,
        })

    # Reset stop flag
    l7._STOP.clear()

    # Start ticker
    _ticker_stop.clear()
    tick_thread = threading.Thread(target=_ticker, daemon=True)
    tick_thread.start()

    _broadcast({"type": "log", "level": "section",
                "msg": "▶ {} flood → {}".format(method, target)})
    _broadcast({"type": "log", "level": "info",
                "msg": "  Workers: {}  RPS: {}  Duration: {}s".format(workers, rps, duration)})

    # Wrap _single_request to capture results
    original_single = l7._single_request

    def patched_single(url, idx, meth):
        ok_flag, ms, status = original_single(url, idx, meth)
        with _lock:
            _current_stats["total"] += 1
            if ok_flag:
                _current_stats["success"] += 1
            else:
                _current_stats["errors"] += 1
            if ms > 0:
                _current_stats["latencies"].append(ms)
            sc = str(status) if status else "0"
            _current_stats["status_counts"][sc] = \
                _current_stats["status_counts"].get(sc, 0) + 1
        return ok_flag, ms, status

    l7._single_request = patched_single

    try:
        if method == "multi":
            # Multi-vector: use run_multi_vector
            methods = ["GET", "POST", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"]
            n       = len(methods)
            w_each  = max(1, workers // n)
            r_each  = max(1.0, rps / n)
            result_list = []
            rl = threading.Lock()

            def _launch(m):
                r = l7.run_flood(target_url=target, workers=w_each, rps=r_each,
                                 duration=duration, method=m, label=m)
                with rl:
                    result_list.append(r)

            threads = [threading.Thread(target=_launch, args=(m,), daemon=True)
                       for m in methods]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            result = {
                "method":     "multi",
                "total":      sum(r["total"] for r in result_list),
                "success":    sum(r["success"] for r in result_list),
                "errors":     sum(r["errors"] for r in result_list),
                "wall":       max(r["wall"] for r in result_list),
                "rps_actual": sum(r["rps_actual"] for r in result_list),
            }
        else:
            result = l7.run_flood(
                target_url=target, workers=workers, rps=rps,
                duration=duration, method=method,
            )
    finally:
        l7._single_request = original_single
        _ticker_stop.set()

    # Compute final latency percentiles
    with _lock:
        lats = sorted(_current_stats["latencies"])
        sc   = _current_stats["status_counts"].copy()
    p50 = lats[int(len(lats)*0.50)] if lats else 0
    p99 = lats[int(len(lats)*0.99)] if lats else 0

    stopped = l7._STOP.is_set()
    _broadcast({
        "type":         "done",
        "method":       method,
        "target":       target,
        "total":        result.get("total", 0),
        "success":      result.get("success", 0),
        "errors":       result.get("errors", 0),
        "rps_actual":   round(result.get("rps_actual", 0), 1),
        "wall":         round(result.get("wall", 0), 2),
        "p50":          round(p50, 1),
        "p99":          round(p99, 1),
        "status_counts": sc,
        "stopped_early": stopped,
    })
    _broadcast({"type": "log", "level": "ok",
                "msg": "✔ {} — {} sent, {:.1f} RPS".format(
                    "Stopped" if stopped else "Complete",
                    result.get("total", 0),
                    result.get("rps_actual", 0))})

    with _lock:
        _current_stats["wall_start"] = None


# ── HTTP Request Handler ───────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass  # suppress default access log

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/proxy-status":
            t0 = time.time()
            try:
                s = socket.create_connection((l7.PROXY_HOST, l7.PROXY_PORT), timeout=4)
                ms = int((time.time() - t0) * 1000)
                s.close()
                self._json({"ok": True, "ms": ms})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        elif path == "/stream":
            # Server-Sent Events
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            q = _queue_mod.Queue()
            with _lock:
                _sse_clients.append(q)

            try:
                while True:
                    try:
                        msg = q.get(timeout=15)
                        self.wfile.write(msg.encode())
                        self.wfile.flush()
                    except _queue_mod.Empty:
                        # heartbeat
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
            except Exception:
                pass
            finally:
                with _lock:
                    if q in _sse_clients:
                        _sse_clients.remove(q)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global _flood_thread
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if path == "/start":
            # Validate
            target   = data.get("target", "").strip()
            method   = data.get("method", "GET").upper()
            workers  = int(data.get("workers", 50))
            rps      = float(data.get("rps", 100))
            duration = float(data.get("duration", 30))

            if not target:
                self._json({"ok": False, "error": "target required"})
                return

            if not target.startswith("http://") and not target.startswith("https://"):
                target = "https://" + target

            valid_methods = {"GET","POST","HEAD","PUT","PATCH","DELETE","OPTIONS","multi"}
            if method not in valid_methods:
                self._json({"ok": False, "error": "invalid method"})
                return

            # Don't allow two concurrent floods
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

        elif path == "/stop":
            l7._STOP.set()
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
    print("L7 Proxy Test Suite backend running on http://localhost:{}".format(PORT))
    print("Open ui/index.html in your browser (or serve it via a static file server).")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        l7._STOP.set()
        server.shutdown()
