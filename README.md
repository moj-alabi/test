# L7 Proxy Test Suite

A Python 3 tool for testing Layer-7 (HTTP/HTTPS) traffic through a Squid residential proxy at `10.0.10.118:3128`.

---

## Requirements

- Python 3.7+ (no third-party packages — stdlib only)
- A running Squid proxy at `10.0.10.118:3128`

---

## Usage

```bash
python3 l7_proxy_tester.py
```

The script is fully interactive — no flags or command-line arguments needed.

1. **Enter target URL** — e.g. `https://example.com` or `http://10.0.0.1`
2. **Proxy reachability check** runs automatically
3. **Select attack vector** from the numbered menu
4. For flood modes, enter your **concurrent connections**, **target RPS**, and **duration**

---

## Attack Vector Menu

```
  ┌─────────────────────────────────────────────┐
  │          SELECT ATTACK VECTOR                │
  ├─────────────────────────────────────────────┤
  │   1. 🔍  Diagnostic (10 sequential tests)   │
  │   2. 💥  GET Flood                          │
  │   3. 💥  POST Flood                         │
  │   4. 💥  HEAD Flood                         │
  │   5. 💥  PUT Flood                          │
  │   6. 💥  PATCH Flood                        │
  │   7. 💥  DELETE Flood                       │
  │   8. 💥  OPTIONS Flood                      │
  │   9. ⚡  Multi-Vector (all methods at once) │
  └─────────────────────────────────────────────┘
```

---

## Modes

### 1 — Diagnostic
Runs 10 sequential probe tests against the target:

| # | Test |
|---|------|
| 1 | Proxy TCP reachability |
| 2 | Egress IP leak check |
| 3 | HTTP GET |
| 4 | HTTP HEAD (security headers) |
| 5 | HTTP OPTIONS (CORS/allowed methods) |
| 6 | Redirect chain tracing |
| 7 | DNS resolution |
| 8 | TLS/SSL certificate info |
| 9 | HTTP POST |
| 10 | Latency benchmark (10× samples, min/avg/max/jitter) |

### 2–8 — Single-Method Flood
Sends continuous requests using one HTTP method for a set duration.

**Prompted parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| Concurrent connections | `50` | Number of parallel worker threads |
| Target RPS | `100` | Requests per second to aim for |
| Duration (seconds) | `30` | How long to run the flood |

**Live progress bar** shows elapsed time, success/fail counts, and actual RPS.

**Summary** includes total sent, throughput, and latency percentiles (avg / p50 / p90 / p99).

### 9 — Multi-Vector Flood ⚡
Fires **all 7 HTTP methods simultaneously** (GET, POST, HEAD, PUT, PATCH, DELETE, OPTIONS), each in its own thread pool.

- Your worker and RPS values are split evenly across the 7 vectors
- Each vector prints its own summary
- A combined aggregate table is printed at the end:

```
  Method       Sent      2xx     Fail      RPS
  ──────────────────────────────────────────────
  DELETE        142      142        0     14.2
  GET           143      143        0     14.3
  HEAD          144      143        1     14.4
  OPTIONS       141      141        0     14.1
  PATCH         142      138        4     14.2
  POST          143      143        0     14.3
  PUT           142      142        0     14.2
```

---

## Proxy Configuration

Edit the constants at the top of `l7_proxy_tester.py` to change the proxy:

```python
PROXY_HOST = "10.0.10.118"
PROXY_PORT = 3128
```

All traffic (HTTP and HTTPS) is routed through this proxy. TLS certificate verification is disabled for lab use.

---

## Flood Preset

After selecting an attack vector, you pick a **Flood Preset** — a single numbered choice that sets everything at once (connections, RPS, duration, UA pool, header shuffle, and query-string injection). No separate prompts.

```
  Preset                           Conns    RPS   Dur  UA Profile          Shuffle   QS
  ──────────────────────────────────────────────────────────────────────────────────────
   1. Light  – legit desktop          25     50   30s  legit_desktop          ✗      off
   2. Medium – legit desktop          50    100   60s  legit_desktop          ✗      off
   3. Heavy  – legit desktop         100    250   60s  legit_desktop          ✗      off
   4. Light  – legit mobile           25     50   30s  legit_mobile           ✗      off
   5. Medium – legit mobile           50    100   60s  legit_mobile           ✗      off
   6. Light  – spoofed bots           25     50   30s  spoofed_bots           ✗      off
   7. Medium – spoofed bots + shuffle 50    100   60s  spoofed_bots           ✓      random
   8. Heavy  – full evasion + QS     100    250   60s  spoofed_evasion        ✓      always
   9. Blitz  – rotate all + shuffle  150    500   30s  rotate                 ✓      random
  10. Custom – set your own values     –      –    –   you choose             ?       ?
```

**Custom** (option 10) asks you to enter connections, RPS, duration, then lets you pick:
- UA profile (rotate / legit_desktop / legit_mobile / spoofed_bots / spoofed_evasion)
- Header shuffle on/off
- Query-string injection (off / random 50% / always)

### What gets randomised per request

- **User-Agent** — drawn randomly from the chosen pool
- **Accept** — rotates across 7 real-browser Accept values
- **Accept-Language** — rotates across 11 locales (en-US, fr-FR, zh-CN, ar-SA, etc.)
- **Connection** — randomly `keep-alive` or `close`
- **Header order** — shuffled per request **only when the preset has shuffle=✓**
- **Extra headers (0–4 randomly injected per request):**
  - `X-Forwarded-For`, `X-Real-IP`, `X-Originating-IP` (random IPs)
  - `Referer` (Google, Bing, Twitter, Facebook, DuckDuckGo)
  - `Cache-Control`, `Pragma`, `DNT`
  - `Sec-Fetch-Mode`, `Sec-Fetch-Site`, `Sec-Fetch-Dest`
- **Query string** — random `key=value` pairs appended to URL per the preset's QS setting

---

## Features

- ✅ Zero dependencies — pure Python stdlib
- ✅ Fully interactive menu, no CLI flags to remember
- ✅ Rate-limited flood engine (token bucket per RPS target)
- ✅ 5 evasion profiles: legit desktop/mobile, spoofed bots, evasion strings, random mix
- ✅ Per-request randomised UA, header order, Accept, Accept-Language, and extra headers
- ✅ Random query-string injection to bypass caching / WAF fingerprinting
- ✅ Live progress bar with real-time RPS counter
- ✅ Latency percentiles (avg, p50, p90, p99)
- ✅ HTTP status code breakdown per run
- ✅ Multi-vector attack with aggregate summary
- ✅ Compatible with Python 3.7+
