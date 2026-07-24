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

## Features

- ✅ Zero dependencies — pure Python stdlib
- ✅ Fully interactive menu, no CLI flags to remember
- ✅ Rate-limited flood engine (token bucket per RPS target)
- ✅ Rotating user-agent strings for realism
- ✅ Live progress bar with real-time RPS counter
- ✅ Latency percentiles (avg, p50, p90, p99)
- ✅ HTTP status code breakdown per run
- ✅ Multi-vector attack with aggregate summary
- ✅ Compatible with Python 3.7+
