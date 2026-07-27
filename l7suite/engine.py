#!/usr/bin/env python3
"""
L7 Proxy Test Suite — Flood Engine
====================================
Self-contained flood engine.  Import this module and call run_flood() or
run_multi_vector().  The server.py wrapper uses it directly.

Proxy: Squid at PROXY_HOST:PROXY_PORT (configurable via env vars).
"""

import os
import sys
import time
import signal
import socket
import ssl
import json
import threading
import urllib.request
import urllib.error
import urllib.parse
import http.client
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, Tuple, List, Dict

# ── Proxy config (override via env) ──────────────────────────────────────────
PROXY_HOST = os.environ.get("L7_PROXY_HOST", "10.0.10.118")
PROXY_PORT = int(os.environ.get("L7_PROXY_PORT", "3128"))
PROXY_URL  = "http://{}:{}".format(PROXY_HOST, PROXY_PORT)

# ── Global graceful-stop event ────────────────────────────────────────────────
_STOP = threading.Event()

# ── Build a urllib opener through the proxy ────────────────────────────────────
def build_opener():
    proxy_handler = urllib.request.ProxyHandler({
        "http":  PROXY_URL,
        "https": PROXY_URL,
    })
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    https_handler = urllib.request.HTTPSHandler(context=ctx)
    opener = urllib.request.build_opener(proxy_handler, https_handler)
    opener.addheaders = [
        ("User-Agent",
         "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        ("Accept",          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
        ("Accept-Language", "en-US,en;q=0.5"),
        ("Connection",      "keep-alive"),
    ]
    return opener

OPENER = build_opener()

# ── Proxy reachability ────────────────────────────────────────────────────────
def check_proxy_reachability():
    # type: () -> Tuple[bool, float]
    """Return (reachable, latency_ms)."""
    try:
        t0 = time.time()
        s  = socket.create_connection((PROXY_HOST, PROXY_PORT), timeout=5)
        ms = (time.time() - t0) * 1000
        s.close()
        return True, round(ms, 1)
    except Exception:
        return False, 0.0

# ── Diagnostic tests ──────────────────────────────────────────────────────────
def test_egress_ip():
    # type: () -> Optional[str]
    for url in ["http://httpbin.org/ip", "http://ifconfig.me/ip"]:
        try:
            req = urllib.request.Request(url)
            with OPENER.open(req, timeout=10) as resp:
                body = resp.read().decode().strip()
            try:
                return json.loads(body).get("origin", body)
            except Exception:
                return body
        except Exception:
            continue
    return None

def test_http_get(target_url):
    # type: (str) -> Dict
    try:
        req = urllib.request.Request(target_url, method="GET")
        t0  = time.time()
        with OPENER.open(req, timeout=15) as resp:
            status  = resp.status
            headers = dict(resp.headers)
            body    = resp.read()
        ms = (time.time() - t0) * 1000
        return {"ok": True, "status": status, "ms": round(ms,1),
                "body_len": len(body), "server": headers.get("Server",""),
                "content_type": headers.get("Content-Type","")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "ms": 0, "error": e.reason}
    except Exception as e:
        return {"ok": False, "status": 0, "ms": 0, "error": str(e)}

def test_dns(hostname):
    # type: (str) -> Dict
    try:
        t0      = time.time()
        results = socket.getaddrinfo(hostname, 80)
        ms      = (time.time() - t0) * 1000
        ips     = list({str(r[4][0]) for r in results})
        return {"ok": True, "ips": ips, "ms": round(ms,1)}
    except Exception as e:
        return {"ok": False, "ips": [], "ms": 0, "error": str(e)}

def test_tls(hostname, port=443):
    # type: (str, int) -> Dict
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        proxy_conn = http.client.HTTPConnection(PROXY_HOST, PROXY_PORT, timeout=10)
        proxy_conn.set_tunnel(hostname, port)
        proxy_conn.connect()
        tls_sock = ctx.wrap_socket(proxy_conn.sock, server_hostname=hostname)
        cert   = tls_sock.getpeercert()
        cipher = tls_sock.cipher()
        proto  = tls_sock.version()
        tls_sock.close()
        subject = {}
        if cert:
            for rdn in cert.get("subject", []):
                for attr in rdn:
                    subject[attr[0]] = attr[1]
        return {"ok": True, "proto": proto,
                "cipher": cipher[0] if cipher else "",
                "cn": subject.get("commonName",""),
                "not_after": cert.get("notAfter","") if cert else ""}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── Flood engine ──────────────────────────────────────────────────────────────
_BODY_METHODS = {"POST", "PUT", "PATCH"}

_USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]


def _single_request(target_url, idx, method):
    # type: (str, int, str) -> Tuple[bool, float, int]
    """Fire one request through the proxy; return (ok, latency_ms, status)."""
    opener = build_opener()
    opener.addheaders = [
        ("User-Agent", _USER_AGENTS[idx % len(_USER_AGENTS)]),
        ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
        ("Accept-Language", "en-US,en;q=0.5"),
        ("Connection", "keep-alive"),
    ]
    t0 = time.time()
    try:
        body_data = None  # type: Optional[bytes]
        if method in _BODY_METHODS:
            body_data = json.dumps({
                "probe": "l7_flood", "idx": idx,
                "ts": datetime.utcnow().isoformat(),
            }).encode()
        req = urllib.request.Request(target_url, data=body_data, method=method)
        if body_data:
            req.add_header("Content-Type", "application/json")
        with opener.open(req, timeout=10) as resp:
            resp.read()
            status = resp.status
        ms = (time.time() - t0) * 1000
        return True, ms, status
    except urllib.error.HTTPError as e:
        ms = (time.time() - t0) * 1000
        return False, ms, e.code
    except Exception:
        return False, 0.0, 0


def run_flood(target_url, workers, rps, duration, method, label=None,
              on_result=None):
    # type: (str, int, float, float, str, Optional[str], Optional[callable]) -> Dict
    """
    Run a flood.  on_result(ok, ms, status) called for every completed request
    — used by server.py to stream live metrics.
    Returns a summary dict.
    """
    _STOP.clear()

    success       = [0]
    errors        = [0]
    total_sent    = [0]
    latencies     = []   # type: List[float]
    status_counts = {}   # type: Dict[int, int]
    lock          = threading.Lock()

    interval   = 1.0 / rps if rps > 0 else 0.0
    wall_start = time.time()
    deadline   = wall_start + duration

    def _worker(idx):
        ok_flag, ms, status = _single_request(target_url, idx, method)
        with lock:
            total_sent[0] += 1
            if ok_flag:
                success[0] += 1
            else:
                errors[0] += 1
            if ms > 0:
                latencies.append(ms)
            status_counts[status] = status_counts.get(status, 0) + 1
        if on_result:
            on_result(ok_flag, ms, status)

    idx = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []
        while time.time() < deadline and not _STOP.is_set():
            submit_time = time.time()
            futures.append(pool.submit(_worker, idx))
            idx += 1
            futures = [f for f in futures if not f.done()]
            elapsed_submit = time.time() - submit_time
            sleep_needed   = interval - elapsed_submit
            if sleep_needed > 0:
                time.sleep(sleep_needed)
        for f in futures:
            try:
                f.result(timeout=15)
            except Exception:
                pass

    wall_elapsed = time.time() - wall_start
    lats_sorted  = sorted(latencies)
    p50 = lats_sorted[int(len(lats_sorted)*0.50)] if lats_sorted else 0
    p90 = lats_sorted[int(len(lats_sorted)*0.90)] if lats_sorted else 0
    p99 = lats_sorted[int(len(lats_sorted)*0.99)] if lats_sorted else 0

    return {
        "method":        label or method,
        "total":         total_sent[0],
        "success":       success[0],
        "errors":        errors[0],
        "wall":          round(wall_elapsed, 2),
        "rps_actual":    round(total_sent[0] / wall_elapsed, 1) if wall_elapsed > 0 else 0,
        "p50":           round(p50, 1),
        "p90":           round(p90, 1),
        "p99":           round(p99, 1),
        "status_counts": {str(k): v for k, v in status_counts.items()},
    }


def run_multi_vector(target_url, workers, rps, duration, on_result=None):
    # type: (str, int, float, float, Optional[callable]) -> Dict
    methods = ["GET", "POST", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"]
    n       = len(methods)
    w_each  = max(1, workers // n)
    r_each  = max(1.0, rps / n)

    results     = []   # type: List[Dict]
    result_lock = threading.Lock()

    def _launch(m):
        r = run_flood(target_url=target_url, workers=w_each, rps=r_each,
                      duration=duration, method=m, label=m, on_result=on_result)
        with result_lock:
            results.append(r)

    threads = [threading.Thread(target=_launch, args=(m,), daemon=True) for m in methods]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Merge status_counts
    merged_sc = {}  # type: Dict[str, int]
    for r in results:
        for code, cnt in r["status_counts"].items():
            merged_sc[code] = merged_sc.get(code, 0) + cnt

    all_lats = []
    for r in results:
        pass  # individual latencies not preserved in aggregation; use per-method p50/p99

    return {
        "method":        "multi",
        "total":         sum(r["total"] for r in results),
        "success":       sum(r["success"] for r in results),
        "errors":        sum(r["errors"] for r in results),
        "wall":          max(r["wall"] for r in results) if results else 0,
        "rps_actual":    round(sum(r["rps_actual"] for r in results), 1),
        "p50":           round(sum(r["p50"] for r in results) / n, 1) if results else 0,
        "p99":           round(sum(r["p99"] for r in results) / n, 1) if results else 0,
        "status_counts": merged_sc,
        "breakdown":     results,
    }
