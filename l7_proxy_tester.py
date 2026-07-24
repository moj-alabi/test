#!/usr/bin/env python3
"""
L7 Proxy Test Suite
-------------------
Tests Layer-7 (HTTP/HTTPS application-layer) events through a
Squid/ProxyChains residential proxy at 10.0.10.118:3128.

Modes
─────
  Normal (default) – 10 sequential diagnostic tests
  Flood  (--flood) – concurrent load test to measure real RPS

Usage:
    python3 l7_proxy_tester.py                        # diagnostic mode
    python3 l7_proxy_tester.py --flood                # flood: 500 reqs, 50 workers
    python3 l7_proxy_tester.py --flood -n 2000 -c 100 # custom
"""

import sys
import time
import socket
import ssl
import json
import argparse
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
    # Don't verify TLS for lab testing; swap to ssl.create_default_context() in prod
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
# 2. External IP leak check  (am I really going out via the proxy/residential?)
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
            # httpbin returns JSON
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
# 4. HTTP HEAD  (check if target responds to HEAD, reveals headers without body)
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
# 5. HTTP OPTIONS  (CORS / allowed methods)
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

    # Manually follow redirects so we can log each hop
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE

    # Override redirect to not follow
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
                # Resolve relative redirect
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
# 7. DNS resolution (via socket, which goes through the OS / proxychains)
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
# 8. TLS/SSL Certificate info (HTTPS targets only)
# ─────────────────────────────────────────────────────────────────────────────
def test_tls(hostname, port=443):
    section(f"TEST 8 – TLS Certificate  →  {hostname}:{port}")
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False   # lab; flip for prod
        ctx.verify_mode    = ssl.CERT_NONE

        # Connect through the HTTP CONNECT tunnel via squid
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
            # cert is a dict[str, Any] at runtime; cast away strict typing
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
# 9. HTTP POST  (send a small benign payload to see how the server responds)
# ─────────────────────────────────────────────────────────────────────────────
def test_http_post(target_url):
    section(f"TEST 9 – HTTP POST  →  {target_url}")
    payload = json.dumps({"test": "l7_probe", "ts": datetime.utcnow().isoformat()}).encode()
    try:
        req = urllib.request.Request(
            target_url,
            data=payload,
            method="POST",
        )
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
# 10. Latency benchmark  (10 quick GETs, measure jitter)
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
# FLOOD MODE  – concurrent HTTP GET storm through the proxy
# ─────────────────────────────────────────────────────────────────────────────

# Thread-safe counters
_lock        = threading.Lock()
_success     = 0
_errors      = 0
_latencies: list[float] = []

# HTTP methods that carry a body
_BODY_METHODS = {"POST", "PUT", "PATCH"}

# Rotating user-agents for flood realism
_USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

def _single_request(target_url: str, idx: int, method: str) -> tuple[bool, float, int]:
    """Fire one request through the proxy; return (ok, latency_ms, status)."""
    opener = build_opener()   # each thread gets its own opener (thread-safe)
    # Rotate user-agents
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


def run_flood(target_url: str, total: int, workers: int, method: str) -> None:
    section(
        f"FLOOD MODE — {total:,} × {BOLD}{method}{RESET}{CYAN}  |  {workers} workers\n"
        f"  {CYAN}Target : {target_url}{RESET}\n"
        f"  {CYAN}Proxy  : {PROXY_URL}{RESET}"
    )

    global _success, _errors, _latencies
    _success   = 0
    _errors    = 0
    _latencies = []

    status_counts: dict[int, int] = {}
    wall_start = time.time()

    # Live progress bar variables
    done_count = 0
    bar_width  = 40

    def _print_progress(done: int) -> None:
        pct   = done / total
        filled = int(bar_width * pct)
        bar   = "█" * filled + "░" * (bar_width - filled)
        elapsed = time.time() - wall_start
        rps   = done / elapsed if elapsed > 0 else 0
        print(
            f"\r  [{bar}] {done}/{total}  "
            f"{GREEN}{_success}✓{RESET} {RED}{_errors}✗{RESET}  "
            f"{CYAN}{rps:.1f} RPS{RESET}   ",
            end="", flush=True
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_single_request, target_url, i, method): i for i in range(total)}
        for future in as_completed(futures):
            success, ms, status = future.result()
            with _lock:
                done_count += 1
                if success:
                    _success += 1
                else:
                    _errors += 1
                if ms > 0:
                    _latencies.append(ms)
                status_counts[status] = status_counts.get(status, 0) + 1
            _print_progress(done_count)

    wall_elapsed = time.time() - wall_start
    print()  # newline after progress bar

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{BOLD}  ── Flood Summary ──────────────────────────────────{RESET}")
    ok(f"Total sent     : {total:,}")
    ok(f"Succeeded (2xx): {GREEN}{_success:,}{RESET}")
    if _errors:
        warn(f"Failed         : {RED}{_errors:,}{RESET}")
    ok(f"Wall time      : {wall_elapsed:.2f} s")
    ok(f"Throughput     : {BOLD}{total / wall_elapsed:.1f} RPS{RESET}")

    if _latencies:
        _latencies.sort()
        avg = sum(_latencies) / len(_latencies)
        p50 = _latencies[int(len(_latencies) * 0.50)]
        p90 = _latencies[int(len(_latencies) * 0.90)]
        p99 = _latencies[int(len(_latencies) * 0.99)]
        ok(f"Latency avg    : {avg:.1f} ms")
        ok(f"Latency p50    : {p50:.1f} ms")
        ok(f"Latency p90    : {p90:.1f} ms")
        ok(f"Latency p99    : {p99:.1f} ms")

    print(f"\n  {DIM}HTTP status breakdown:{RESET}")
    for code in sorted(status_counts):
        colour = GREEN if str(code).startswith("2") else (
                 YELLOW if str(code).startswith("3") else RED)
        print(f"    {colour}HTTP {code}{RESET} : {status_counts[code]:,}")

    print(f"\n{BOLD}{GREEN}{'='*60}")
    print(f"  Flood complete.")
    print(f"{'='*60}{RESET}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="L7 Proxy Test Suite — Squid @ 10.0.10.118:3128",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 l7_proxy_tester.py                         # diagnostic\n"
            "  python3 l7_proxy_tester.py --flood                 # 500 reqs, 50 workers\n"
            "  python3 l7_proxy_tester.py --flood -n 2000 -c 100  # custom flood\n"
        ),
    )
    parser.add_argument("--flood",  action="store_true",
                        help="Run concurrent flood test instead of diagnostic suite")
    parser.add_argument("-n", "--count",   type=int, default=500,
                        help="Total number of requests in flood mode (default: 500)")
    parser.add_argument("-c", "--workers", type=int, default=50,
                        help="Concurrent worker threads in flood mode (default: 50)")
    parser.add_argument(
        "-m", "--method",
        type=str,
        default="GET",
        choices=["GET", "POST", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"],
        help="HTTP method to use in flood mode (default: GET)",
    )
    args = parser.parse_args()

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  L7 Proxy Test Suite — Squid @ {PROXY_HOST}:{PROXY_PORT}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    print(f"{DIM}  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}")

    # ── Get target URL from user ──────────────────────────────────────────────
    try:
        raw = input(f"\n{BOLD}Enter target URL (e.g. https://example.com or http://10.0.0.1): {RESET}").strip()
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
    print(f"{DIM}  Proxy      : {PROXY_URL}{RESET}\n")

    # ── Always check proxy is up first ────────────────────────────────────────
    if not test_proxy_reachability():
        fail("Proxy is unreachable — aborting.")
        sys.exit(1)

    # ── Branch: flood vs diagnostic ───────────────────────────────────────────
    if args.flood:
        method = args.method.upper()
        info(f"Flood mode  → {args.count:,} requests  |  {args.workers} workers  |  {method}")
        run_flood(target_url, total=args.count, workers=args.workers, method=method)
        return

    # ── Diagnostic mode ───────────────────────────────────────────────────────
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


if __name__ == "__main__":
    main()
