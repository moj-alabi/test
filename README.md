# L7 Proxy Test Suite

A Python 3 tool for testing **Layer-7 (HTTP/HTTPS) application-layer behaviour**
through a Squid HTTP proxy with residential proxies (ProxyChains).

All traffic is routed through **`10.0.10.118:3128`** (Squid).

---

## Requirements

- Python **3.10+** (uses `match`/`|` union syntax internally)
- No third-party packages — stdlib only (`urllib`, `ssl`, `socket`, `concurrent.futures`)
- Squid proxy running and reachable at `10.0.10.118:3128`

---

## Installation

```bash
# Copy to your Kali lab box, then:
chmod +x l7_proxy_tester.py
```

No `pip install` required.

---

## Modes

| Mode | Flag | Description |
|------|------|-------------|
| **Diagnostic** | *(default)* | 10 sequential L7 tests — egress IP, DNS, TLS, headers, etc. |
| **Flood** | `--flood` | Concurrent HTTP request storm — measures real RPS & latency |

---

## Usage

### Diagnostic mode (default)

Run all 10 sequential L7 tests against a target:

```bash
python3 l7_proxy_tester.py
```

You will be prompted:

```
Enter target URL (e.g. https://example.com or http://10.0.0.1):
```

Paste a full URL. If you omit the scheme (`http://` / `https://`) it defaults to `https://`.

---

### Flood mode

Send a burst of concurrent requests through the proxy to measure throughput and latency.

```bash
# Default: 500 requests, 50 concurrent workers, GET method
python3 l7_proxy_tester.py --flood

# Custom request count and worker count
python3 l7_proxy_tester.py --flood -n 1000 -c 100

# Choose a different HTTP method
python3 l7_proxy_tester.py --flood -m POST
python3 l7_proxy_tester.py --flood -m HEAD
python3 l7_proxy_tester.py --flood -m PUT

# Full custom example
python3 l7_proxy_tester.py --flood -n 2000 -c 150 -m POST
```

---

## All CLI Flags

| Flag | Long form | Default | Description |
|------|-----------|---------|-------------|
| | `--flood` | off | Enable flood/load-test mode |
| `-n` | `--count` | `500` | Total number of requests to send (flood mode) |
| `-c` | `--workers` | `50` | Number of concurrent threads (flood mode) |
| `-m` | `--method` | `GET` | HTTP method: `GET` `POST` `HEAD` `PUT` `PATCH` `DELETE` `OPTIONS` |

---

## Diagnostic Tests (default mode)

| # | Test | What it checks |
|---|------|----------------|
| 1 | **Proxy TCP reachability** | Squid is up and accepting connections on port 3128 |
| 2 | **Egress IP leak check** | Confirms egress IP is the residential proxy, not your real IP |
| 3 | **HTTP GET** | Full response: status code, body size, Server / Content-Type / X-Cache |
| 4 | **HTTP HEAD** | Security headers: HSTS, CSP, X-Frame-Options, X-Powered-By, etc. |
| 5 | **HTTP OPTIONS** | CORS posture: allowed methods, Access-Control-Allow-Origin |
| 6 | **Redirect chain** | Follows every 3xx hop (up to 10 deep), logs each URL + status |
| 7 | **DNS resolution** | Resolves the hostname and returns all A/AAAA records |
| 8 | **TLS certificate** | TLS version, cipher suite, CN, issuer, validity dates via CONNECT tunnel |
| 9 | **HTTP POST** | Sends a JSON probe body; shows how the server handles request bodies |
| 10 | **Latency benchmark** | 10 timed HEAD requests → min / avg / max / jitter |

---

## Flood Mode Output

A live progress bar shows real-time status while the flood runs:

```
  [████████████████░░░░░░░░░░░░░░░░░░░░░░░░] 400/500  398✓ 2✗  42.3 RPS
```

Final summary:

```
  [+] Total sent     : 500
  [+] Succeeded (2xx): 498
  [!] Failed         : 2
  [+] Wall time      : 11.83 s
  [+] Throughput     : 42.3 RPS
  [+] Latency avg    : 1182.4 ms
  [+] Latency p50    : 1103.2 ms
  [+] Latency p90    : 1891.5 ms
  [+] Latency p99    : 2340.1 ms

  HTTP status breakdown:
    HTTP 200 : 498
    HTTP 403 : 2
```

---

## HTTP Method Reference

| Method | Body? | Typical use |
|--------|-------|-------------|
| `GET` | No | Standard page request — highest compatibility |
| `POST` | Yes (JSON) | Form submit / API endpoint stress |
| `HEAD` | No | Header-only probe — lowest bandwidth, fastest RPS |
| `PUT` | Yes (JSON) | REST resource update |
| `PATCH` | Yes (JSON) | Partial resource update |
| `DELETE` | No | REST resource deletion test |
| `OPTIONS` | No | CORS preflight / allowed-methods probe |

For `POST`, `PUT`, and `PATCH` the script automatically attaches a JSON body:

```json
{"probe": "l7_flood", "idx": <request_number>, "ts": "<ISO timestamp>"}
```

---

## Example Commands

```bash
# Quick diagnostic run
python3 l7_proxy_tester.py

# Flood with GET — 1000 requests, 75 workers
python3 l7_proxy_tester.py --flood -n 1000 -c 75

# POST flood — simulate form/API spam, 500 reqs, 50 workers
python3 l7_proxy_tester.py --flood -m POST -n 500 -c 50

# HEAD flood — minimal bandwidth, max RPS measurement
python3 l7_proxy_tester.py --flood -m HEAD -n 2000 -c 100

# OPTIONS flood — CORS/firewall probe
python3 l7_proxy_tester.py --flood -m OPTIONS -n 300 -c 30

# Heavy DELETE flood
python3 l7_proxy_tester.py --flood -m DELETE -n 5000 -c 200
```

---

## Proxy Configuration

The proxy is hardcoded at the top of `l7_proxy_tester.py`:

```python
PROXY_HOST = "10.0.10.118"
PROXY_PORT = 3128
```

Change these values if your Squid instance is on a different address.

---

## Notes

- **TLS verification is disabled** (`CERT_NONE`) — intentional for lab use. Change `ctx.verify_mode` to `ssl.CERT_REQUIRED` in production.
- Each flood worker creates its own `urllib` opener — fully thread-safe, no shared state.
- User-agents rotate across 5 realistic browser strings per request to avoid trivial fingerprinting.
- The script uses **stdlib only** — no `requests`, `aiohttp`, or any third-party dependency.

---

## Disclaimer

> This tool is intended for **authorised lab testing only**.  
> Only run it against systems you own or have explicit written permission to test.  
> Misuse may be illegal under the Computer Misuse Act or equivalent legislation.
