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

import requests

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


def run_probes(target, probes, ua, interval):
    """Send each probe and print its path with a colored status.

    Returns a dict mapping each probe string to its raw status code
    (an int, or the string "ERR" when the request failed) so callers
    can build summaries from the results.
    """
    width = max(len(p) for p in probes)
    results = {}
    for probe in probes:
        try:
            r = requests.get(
                target + probe,
                headers={"User-Agent": ua},
                timeout=5,
                allow_redirects=False,
            )
            results[probe] = r.status_code
            status = color_status(r.status_code)
        except Exception as e:
            results[probe] = "ERR"
            status = f"{color_status('ERR')} {e}"
        log(f"  {probe.ljust(width)}  ->  {status}")
        time.sleep(interval)
    return results


def test_encoded_slash(target, interval):
    """Fully encode "/" and report the HTTP status for each probe."""
    log(c("1", "=== ENCODED SLASH TEST ==="))
    encoded = encode_all("/")  # "%2F"
    log(f"  encoded '/' -> {encoded}")

    probes = [
        f"/api{encoded}v1{encoded}users",
        f"/{encoded}{encoded}etc{encoded}passwd",
        f"/admin{encoded}config",
    ]
    run_probes(target, probes, "encoded-slash-tester", interval)


def test_encoded_index_html(target, interval):
    """Fully encode "index.html" and report the HTTP status for each probe."""
    log(c("1", "=== ENCODED index.html TEST ==="))
    encoded = encode_all("index.html")  # every char encoded
    log(f"  encoded 'index.html' -> {encoded}")

    probes = [
        f"/{encoded}",
        f"/public/{encoded}",
        f"/../{encoded}",
    ]
    run_probes(target, probes, "encoded-index-tester", interval)


def test_encoded_pages(target, interval):
    """Request real app routes plain, single-encoded, and double-encoded.

    For each route we send three forms and compare the HTTP status:
      - plain:         /login
      - single-encoded segment:    /%6C%6F%67%69%6E
      - double-encoded segment:    /%256C%256F...
    If plain and encoded forms return the SAME status, the edge/WAF is
    normalizing (decoding) the path before it decides. If they differ,
    the WAF is matching the literal bytes and missing the encoded form.

    The root route "/" has no segment to encode, so it is probed plain
    only as a reachability baseline.
    """
    routes = ["/", "/teams", "/users", "/scoreboard", "/login", "/register"]
    log(c("1", "=== ENCODED REAL-ROUTE TEST ==="))

    # Build the probe forms per route and remember which is which.
    probes = []
    forms = {}  # route -> {"plain": probe, "single": probe|None, "double": probe|None}
    for route in routes:
        plain_p = route
        segment = route.lstrip("/")               # 'login'; '' for root
        if segment:
            single = encode_all(segment)          # 'login' -> %6C%6F...
            double = single.replace("%", "%25")   # double-encode: % -> %25
            single_p, double_p = f"/{single}", f"/{double}"
            probes.extend([plain_p, single_p, double_p])
        else:
            single_p = double_p = None            # nothing to encode for "/"
            probes.append(plain_p)
        forms[route] = {"plain": plain_p, "single": single_p, "double": double_p}

    results = run_probes(target, probes, "encoded-route-tester", interval)

    # Per-route verdict: compare the plain status against the encoded forms.
    log(c("1", "--- SUMMARY ---"))
    for route in routes:
        plain = results.get(forms[route]["plain"])
        single_p = forms[route]["single"]
        double_p = forms[route]["double"]

        if single_p is None:
            # Root route: baseline only, no encoding comparison possible.
            log(
                f"  {route.ljust(12)} "
                f"plain={color_status(plain)}  ->  {c('90', 'baseline only (no segment to encode)')}"
            )
            continue

        single = results.get(single_p)
        double = results.get(double_p)

        if "ERR" in (plain, single, double):
            verdict = c("90", "request error - rerun")
        elif plain == single == double:
            verdict = c("32", "WAF normalizes encoding (all forms match)")
        elif plain == single and single != double:
            verdict = c("33", "single-decode only (double-encoded differs)")
        else:
            verdict = c("31", "WAF NOT normalizing (encoded form differs from plain)")

        log(
            f"  {route.ljust(12)} "
            f"plain={color_status(plain)} "
            f"single={color_status(single)} "
            f"double={color_status(double)}  ->  {verdict}"
        )


def run_pass(target, interval):
    """Run one full pass of all tests."""
    test_encoded_slash(target, interval)
    test_encoded_index_html(target, interval)
    test_encoded_pages(target, interval)


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

    round_num = 0
    try:
        while True:
            round_num += 1
            log(c("1;35", f"########## ROUND {round_num} ##########"))
            run_pass(target, args.interval)

            if not args.loop and round_num >= args.rounds:
                break
            time.sleep(args.delay)
    except KeyboardInterrupt:
        print()
        log("Interrupted by user. Stopping.")

    print("\n" + c("1", "=" * 60))
    log(f"Completed {round_num} round(s).")
    print(c("1", "=" * 60))
