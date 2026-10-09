#!/usr/bin/env python3
"""
https://dnh9gohi236m8.cloudfront.net/login
Standalone encoded-value test.

Percent-encodes EVERY character of a value and checks the HTTP status
returned. Useful for probing how an L7 edge normalizes fully
percent-encoded path segments.

Run once (default):
    python3 encoded_slash_test.py                     # prompts for a target
    python3 encoded_slash_test.py https://example.com # target on the command line

Run many times:
    python3 encoded_slash_test.py --rounds 5          # 5 passes then stop
    python3 encoded_slash_test.py --loop              # forever, until Ctrl+C
    python3 encoded_slash_test.py --rounds 10 --delay 2   # 2s between passes
    python3 encoded_slash_test.py --interval 0.2      # faster per-probe pacing
"""
import argparse
import sys
import time
from datetime import datetime
from itertools import cycle

import requests

# Playwright is optional — only needed for WAF token acquisition.
# Auto-installed at startup if missing.
try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


# ---------------------------------------------------------------------------
# Global stats collector — accumulates results across all rounds/tests
# ---------------------------------------------------------------------------

class ProbeStats:
    """Accumulates probe results across all rounds for a final summary."""

    def __init__(self):
        self.total_probes = 0
        self.status_counts = {}       # status_code -> count
        self.source_counts = {}       # source label -> count
        self.challenged = 0           # WAF intercepted (challenge/captcha)
        self.errors = 0               # request failures
        self.origin_served = 0        # reached origin
        self.same_page = 0            # encoded form served same page as plain
        self.different_page = 0       # encoded form served different content
        self.unique_paths = set()     # unique probe paths seen

    def record(self, probe, result):
        """Record a single probe result."""
        self.total_probes += 1
        self.unique_paths.add(probe)
        status = result["status"]
        source = result["source"]

        self.status_counts[status] = self.status_counts.get(status, 0) + 1
        self.source_counts[source] = self.source_counts.get(source, 0) + 1

        if status == "ERR":
            self.errors += 1
        elif "WAF" in source:
            self.challenged += 1
        elif "origin" in source.lower():
            self.origin_served += 1

    def record_page_comparison(self, is_same):
        """Record whether an encoded probe served the same page as plain."""
        if is_same:
            self.same_page += 1
        else:
            self.different_page += 1

    def print_summary(self, round_count, target):
        """Print a high-level summary of everything observed."""
        print()
        print(c("1", "=" * 60))
        print(c("1", "FINAL SUMMARY"))
        print(c("1", "=" * 60))
        print(f"  Target:         {target}")
        print(f"  Rounds:         {round_count}")
        print(f"  Total probes:   {self.total_probes}")
        print(f"  Unique paths:   {len(self.unique_paths)}")
        print()

        # Response breakdown
        print(c("1", "  Response source breakdown:"))
        print(f"    Reached origin:     {c('32', str(self.origin_served))}")
        print(f"    WAF intercepted:    {c('33', str(self.challenged))}")
        print(f"    Request errors:     {c('90', str(self.errors))}")
        other = self.total_probes - self.origin_served - self.challenged - self.errors
        if other > 0:
            print(f"    Other (edge/unknown): {other}")
        print()

        # Status code distribution
        print(c("1", "  Status codes:"))
        for code in sorted(self.status_counts, key=lambda x: (isinstance(x, str), x)):
            count = self.status_counts[code]
            pct = count / self.total_probes * 100 if self.total_probes else 0
            print(f"    {color_status(code)}  {count:>5}  ({pct:.1f}%)")
        print()

        # Page-match results (the real answer to "did the server interpret it?")
        comparisons = self.same_page + self.different_page
        if comparisons > 0:
            print(c("1", "  Encoding vs. plain (did the server serve the same page?):"))
            print(f"    Same page served:      {c('32' if self.same_page == 0 else '31', str(self.same_page))}")
            print(f"    Different response:    {c('32', str(self.different_page))}")
            print()
            if self.same_page > 0:
                print(c("31", "    ⚠  RISK: Encoded paths served the SAME real page as plain."))
                print(c("31", "       The WAF did not recognize the path through encoding,"))
                print(c("31", "       but the origin decoded it and served real content."))
                print(c("90", "       Fix: add URL_DECODE text transforms to WAF rules matching these paths."))
            elif self.different_page > 0 and self.same_page == 0:
                print(c("32", "    ✓  No encoded path served the same page as plain."))
                print(c("90", "       Origin rejected the encoded forms (404/different content)."))
            print()

        # Reached-origin check — did the WAF/edge stop anything?
        if self.origin_served > 0 and self.challenged == 0:
            print(c("33", "    ⚠  NOTE: Every probe reached the origin — none were intercepted"))
            print(c("33", "       by the WAF. Check your WAF rules if any paths should be blocked."))
            print()
        elif self.challenged > 0:
            print(f"    WAF intercepted {self.challenged} probe(s)")
            if self.origin_served > 0:
                print(c("33", f"    ⚠  But {self.origin_served} probe(s) still reached origin."))
            else:
                print(c("32", "    ✓  No probes reached the origin — WAF caught everything."))
            print()

        # Source breakdown
        print(c("1", "  Response sources:"))
        for src in sorted(self.source_counts, key=lambda x: -self.source_counts[x]):
            count = self.source_counts[src]
            print(f"    {count:>5}  {src}")

        print(c("1", "=" * 60))


# Global stats instance
stats = ProbeStats()


def ensure_playwright():
    """Check for playwright + chromium; install automatically if missing.

    Returns True if playwright is ready to use, False otherwise.
    """
    global HAS_PLAYWRIGHT, sync_playwright
    import subprocess

    # 1. Check if the playwright package is installed.
    if not HAS_PLAYWRIGHT:
        print("[setup] playwright not found — installing...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "playwright", "-q"],
                stdout=subprocess.DEVNULL,
            )
            from playwright.sync_api import sync_playwright as _pw
            sync_playwright = _pw
            HAS_PLAYWRIGHT = True
            print("[setup] playwright package installed.")
        except Exception as e:
            print(f"[setup] failed to install playwright: {e}")
            return False

    # 2. Check if the chromium browser binary is present.
    #    `playwright install --dry-run` isn't available, so we try importing
    #    and launching; a missing-browser error triggers the install.
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
    except Exception:
        print("[setup] chromium browser not found — installing (one-time download)...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "playwright", "install", "chromium"],
            )
            print("[setup] chromium installed.")
        except Exception as e:
            print(f"[setup] failed to install chromium: {e}")
            return False

    return True

# --- ANSI colors (auto-disabled when output is not a TTY) ---
USE_COLOR = sys.stdout.isatty()


def c(code, text):
    if not USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def color_status(status):
    """Color an HTTP status code: 2xx green, 3xx cyan, 4xx yellow, 5xx red."""
    try:
        bucket = int(status) // 100
    except (TypeError, ValueError):
        return c("90", str(status))  # grey for non-numeric (errors)
    code = {2: "32", 3: "36", 4: "33", 5: "31"}.get(bucket, "37")
    return c(code, str(status))


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{c('90', '[' + ts + ']')} {msg}")


def encode_all(value):
    """Percent-encode every single character of value (e.g. '/' -> '%2F')."""
    return "".join(f"%{b:02X}" for b in value.encode("utf-8"))


# --- Realistic browser User-Agent strings (rotated per test) ---
_REALISTIC_UAS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]
_ua_cycle = cycle(_REALISTIC_UAS)


def next_ua():
    """Return the next realistic User-Agent string from the rotation."""
    return next(_ua_cycle)


# ---------------------------------------------------------------------------
# AWS WAF token acquisition via headless browser
# ---------------------------------------------------------------------------

def acquire_waf_token(target, timeout=15):
    """Use a headless Chromium browser to solve the AWS WAF challenge and
    extract the 'aws-waf-token' cookie.

    AWS WAF Challenge actions return a 202 with inline JavaScript that the
    client must execute.  A bare requests.get() can't run JS, but a real
    browser can.  After the challenge JS runs, WAF sets the
    'aws-waf-token' cookie which all subsequent requests can carry to skip
    the challenge gate.

    Returns a dict of cookies (may be empty if the token isn't set).
    """
    if not HAS_PLAYWRIGHT:
        log(c("33", "playwright not installed — skipping token acquisition"))
        log(c("90", "  install: pip install playwright && python -m playwright install chromium"))
        return {}, None

    log(c("1", "=== WAF TOKEN ACQUISITION ==="))
    log(f"  Opening headless browser -> {target}")
    cookies = {}
    browser_ua = None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()

            # Capture the browser's real User-Agent — WAF ties the token to
            # the UA that solved the challenge, so probes MUST reuse it.
            browser_ua = page.evaluate("() => navigator.userAgent")

            page.goto(target, wait_until="networkidle", timeout=timeout * 1000)

            # Give the challenge JS a moment to set the cookie.
            page.wait_for_timeout(3000)

            for ck in ctx.cookies():
                if "aws-waf-token" in ck["name"].lower():
                    cookies[ck["name"]] = ck["value"]
                    log(c("32", f"  acquired token: {ck['name']}={ck['value'][:40]}..."))

            browser.close()

    except Exception as e:
        log(c("31", f"  browser error: {e}"))

    if not cookies:
        log(c("33", "  no aws-waf-token cookie found (WAF may not be challenging)"))
    if browser_ua:
        log(c("90", f"  browser UA: {browser_ua}"))

    return cookies, browser_ua


def classify_source(status, headers):
    """Identify WHO produced the response: origin server or WAF/edge.

    Only reports the source — does NOT interpret the status code's meaning.
    The status code (200, 403, 404, etc.) speaks for itself; the user knows
    whether a 403 is an auth gate or a WAF block from context.

    Uses concrete header signals:
      - x-amzn-waf-action header  -> WAF intercepted (challenge/captcha)
      - server: Apache/nginx/etc  -> reached origin
      - x-cache / via headers     -> CloudFront edge involvement
    """
    waf_action = headers.get("x-amzn-waf-action", "").lower()
    server = headers.get("server", "")
    x_cache = headers.get("x-cache", "")

    # WAF explicitly intercepted (challenge/captcha action).
    if waf_action in ("challenge", "captcha"):
        return f"WAF {waf_action}"

    # Origin server identified — the request reached your server.
    if server:
        return f"origin ({server})"

    # No server header but CloudFront is in the chain.
    if x_cache:
        return f"via CloudFront ({x_cache})"

    return "unknown"


def run_probes(target, probes, session, interval):
    """Send each probe through a persistent session and report real signals.

    Uses a shared requests.Session so the WAF token cookie persists AND so
    any rotated token WAF returns via Set-Cookie is picked up automatically
    for the next request. The session's User-Agent is fixed (set by the
    caller to match the browser that solved the challenge).

    Returns a dict mapping each probe to a record:
        {"status": <int|'ERR'>, "source": <label>, "waf": <action>,
         "server": <server hdr>, "x_cache": <x-cache hdr>}
    """
    width = max(len(p) for p in probes)
    results = {}
    for probe in probes:
        try:
            r = session.get(
                target + probe,
                timeout=5,
                allow_redirects=False,
            )
            h = r.headers
            source = classify_source(r.status_code, h)

            # Extract a page identifier from the response body so we can
            # compare whether the encoded form served the same real page.
            body = r.text
            body_len = len(body)
            # Pull the <title> tag if present — quick page identity check.
            title = ""
            import re
            m = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
            if m:
                title = m.group(1).strip()[:80]

            results[probe] = {
                "status": r.status_code,
                "source": source,
                "waf": h.get("x-amzn-waf-action", ""),
                "server": h.get("server", ""),
                "x_cache": h.get("x-cache", ""),
                "body_len": body_len,
                "title": title,
            }
            title_info = f' title="{title}"' if title else ""
            status = f"{color_status(r.status_code)}  {c('90', source)}  [{body_len}B{title_info}]"
        except Exception as e:
            results[probe] = {
                "status": "ERR", "source": "request error",
                "waf": "", "server": "", "x_cache": "",
                "body_len": 0, "title": "",
            }
            status = f"{color_status('ERR')} {e}"
        log(f"  {probe.ljust(width)}  ->  {status}")
        stats.record(probe, results[probe])
        time.sleep(interval)
    return results


def test_encoded_pages(target, interval, session):
    """Request real app routes plain and single-encoded, then compare.

    For each route we send two forms:
      - plain:          /login
      - single-encoded: /%6C%6F%67%69%6E
    If both return the same status and page, the origin decoded the
    encoding and served real content — meaning encoded paths bypass
    any WAF rule matching the plain string.

    The root route "/" has no segment to encode, so it is probed plain
    only as a reachability baseline.
    """
    routes = ["/", "/teams", "/users", "/scoreboard", "/login", "/register"]
    log(c("1", "=== ENCODED REAL-ROUTE TEST ==="))

    # Build the probe forms per route.
    probes = []
    forms = {}  # route -> {"plain": probe, "single": probe|None}
    for route in routes:
        plain_p = route
        segment = route.lstrip("/")               # 'login'; '' for root
        if segment:
            single = encode_all(segment)          # 'login' -> %6C%6F...
            single_p = f"/{single}"
            probes.extend([plain_p, single_p])
        else:
            single_p = None                       # nothing to encode for "/"
            probes.append(plain_p)
        forms[route] = {"plain": plain_p, "single": single_p}

    results = run_probes(target, probes, session, interval)

    # Per-route summary: plain vs single-encoded.
    empty = {"status": None, "source": "-", "body_len": 0, "title": ""}
    log(c("1", "--- SUMMARY (status | source | page served?) ---"))
    for route in routes:
        plain = results.get(forms[route]["plain"], empty)
        single_p = forms[route]["single"]

        if single_p is None:
            log(
                f"  {route.ljust(12)} "
                f"plain={color_status(plain['status'])} [{plain['source']}] "
                f"{plain['body_len']}B  title=\"{plain['title']}\""
            )
            continue

        single = results.get(single_p, empty)

        def page_match(base, variant):
            """Check if variant served the same page as the plain request.
            Returns (label, is_same_page_bool)."""
            if base["status"] != variant["status"]:
                return c("31", "DIFFERENT status"), False
            if base["title"] and variant["title"] and base["title"] == variant["title"]:
                return c("32", "SAME PAGE (title match)"), True
            if base["body_len"] > 0 and variant["body_len"] > 0:
                ratio = variant["body_len"] / base["body_len"]
                if 0.8 <= ratio <= 1.2:
                    return c("32", "SAME PAGE (body size ~match)"), True
                return c("31", f"DIFFERENT body ({variant['body_len']}B vs {base['body_len']}B)"), False
            return c("90", "inconclusive"), False

        single_match, single_same = page_match(plain, single)
        stats.record_page_comparison(single_same)

        log(
            f"  {route.ljust(12)}\n"
            f"      plain  : {color_status(plain['status'])} [{plain['source']}]  "
            f"{plain['body_len']}B  title=\"{plain['title']}\"\n"
            f"      encoded: {color_status(single['status'])} [{single['source']}]  "
            f"{single['body_len']}B  title=\"{single['title']}\"  ->  {single_match}"
        )


def run_pass(target, interval, session):
    """Run one full pass of all tests."""
    test_encoded_pages(target, interval, session)


def normalize_target(raw):
    """Trim input and ensure it has a scheme; strip any trailing slash."""
    t = raw.strip()
    if not t:
        return ""
    if not t.startswith(("http://", "https://")):
        t = "https://" + t
    return t.rstrip("/")


def prompt_for_target():
    """Interactively ask the user for a target URL until a non-empty one is given."""
    while True:
        try:
            raw = input("Enter target URL (e.g. https://example.com): ")
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit("No target provided. Exiting.")
        target = normalize_target(raw)
        if target:
            return target
        print("  Target cannot be empty. Try again.")


def parse_args():
    p = argparse.ArgumentParser(description="Fully-encoded path probe (status codes only).")
    p.add_argument("target", nargs="?", default=None,
                   help="Base URL to probe. If omitted, you'll be prompted for one.")
    p.add_argument("--rounds", type=int, default=1,
                   help="Number of full passes to run (default: 1).")
    p.add_argument("--loop", action="store_true",
                   help="Run forever until Ctrl+C (overrides --rounds).")
    p.add_argument("--delay", type=float, default=1.0,
                   help="Seconds to wait between passes (default: 1.0).")
    p.add_argument("--interval", type=float, default=0.5,
                   help="Seconds to wait between individual probes (default: 0.5).")
    p.add_argument("--no-token", action="store_true",
                   help="Skip headless-browser WAF token acquisition (raw requests only).")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # Target comes from the CLI arg, or an interactive prompt if omitted.
    target = normalize_target(args.target) if args.target else prompt_for_target()

    print(c("1", "=" * 60))
    print(c("1", "ENCODED VALUE TEST - CONTROLLED LAB ENVIRONMENT"))
    print(f"Target: {target}")
    mode = "loop (Ctrl+C to stop)" if args.loop else f"{args.rounds} round(s)"
    print(f"Mode:   {mode} | probe interval {args.interval}s | pass delay {args.delay}s")
    print(c("90", "-" * 60))
    print(c("90", "Options: [target]  --rounds N  --loop  --delay S  --interval S  -h/--help"))
    print(c("90", "Examples: --rounds 5  |  --loop  |  --rounds 10 --delay 2"))
    print(c("1", "=" * 60) + "\n")

    # Default UA used only when token acquisition is skipped/unavailable.
    fallback_ua = next_ua()

    def build_session():
        """Create a persistent session seeded with a fresh WAF token.

        A single Session reuses the connection and keeps a live cookie jar,
        so the WAF token persists AND any rotated token WAF hands back via
        Set-Cookie is applied to the next request automatically. The UA is
        fixed to match the browser that solved the challenge — WAF ties the
        token to that UA, so a mismatched/rotating UA invalidates it.
        """
        s = requests.Session()
        token_cookies, browser_ua = ({}, None)
        if not args.no_token and ensure_playwright():
            token_cookies, browser_ua = acquire_waf_token(target)

        # Fix the User-Agent for the whole session.
        s.headers.update({"User-Agent": browser_ua or fallback_ua})

        # Seed the token cookie(s) into the session jar.
        for name, value in (token_cookies or {}).items():
            s.cookies.set(name, value)

        if token_cookies:
            log(c("32", f"Session ready with WAF token ({len(token_cookies)} cookie(s)), UA fixed"))
        elif args.no_token:
            log(c("90", "Token acquisition skipped (--no-token)"))
        else:
            log(c("33", "Proceeding without WAF token (may get challenged)"))
        return s

    session = build_session()
    print()

    round_num = 0
    try:
        while True:
            round_num += 1
            log(c("1;35", f"########## ROUND {round_num} ##########"))
            run_pass(target, args.interval, session)

            if not args.loop and round_num >= args.rounds:
                break

            # Refresh the token/session before the next round so a rotated
            # or expired token doesn't start triggering challenges mid-run.
            if not args.no_token:
                session = build_session()
            time.sleep(args.delay)
    except KeyboardInterrupt:
        print()
        log("Interrupted by user. Stopping.")

    # Always print the final summary — even after Ctrl+C.
    stats.print_summary(round_num, target)
