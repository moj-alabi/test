# Tempest 1.0

Self-contained L7 load-testing tool with a web UI.  
All traffic is routed through a Squid proxy.

## Quick Start

```bash
cd l7suite
python3 server.py
# Open http://localhost:5000
```

## Structure

```
l7suite/
├── engine.py        # Flood engine (pure stdlib)
├── server.py        # HTTP server — serves UI + REST/SSE API
└── public/
    ├── index.html   # Dashboard
    ├── styles.css   # IronFlood-style UI
    └── app.js       # Chart.js, SSE live metrics, history
```

## Configuration

| Env var         | Default        | Description          |
|-----------------|----------------|----------------------|
| `L7_PROXY_HOST` | `10.0.10.118`  | Squid proxy host     |
| `L7_PROXY_PORT` | `3128`         | Squid proxy port     |
| `PORT`          | `5000`         | Web server port      |

```bash
L7_PROXY_HOST=1.2.3.4 L7_PROXY_PORT=8080 python3 server.py
```

## API

| Method | Path               | Description                         |
|--------|--------------------|-------------------------------------|
| GET    | `/api/proxy-status`| Proxy TCP reachability check        |
| POST   | `/api/start`       | Start flood `{target,method,workers,rps,duration}` |
| POST   | `/api/stop`        | Graceful stop                       |
| GET    | `/api/stream`      | Server-Sent Events (live metrics)   |

## Attack Vectors

- GET, POST, HEAD, PUT, PATCH, DELETE, OPTIONS
- MULTI — all 7 methods simultaneously

## Features

- Live RPS chart (Chart.js)
- HTTP status doughnut (2xx/3xx/4xx/5xx)
- Progress bar + elapsed time
- 🛑 Stop button (graceful, prints final metrics)
- Run history (localStorage, 50 entries)
- Quick presets: Light / Medium / Heavy / Blitz
