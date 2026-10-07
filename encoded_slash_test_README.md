# encoded_slash_test.py

A standalone probe that **percent-encodes every character** of a value and reports the
HTTP status code returned by a target. It's meant for checking how an L7 edge
(CDN / WAF / reverse proxy) normalizes fully percent-encoded path segments.

All traffic is directed at a single configurable target. Point it only at
infrastructure you own or are authorized to test.

## What it does

Each run performs one or more **passes**. A pass runs two tests:

- **Encoded slash test** — encodes `/` as `%2F` and sends paths built with it, e.g.
  `/api%2Fv1%2Fusers`, `/%2F%2Fetc%2Fpasswd`, `/admin%2Fconfig`.
- **Encoded index.html test** — encodes every character of `index.html`
  (`%69%6E%64%65%78%2E%68%74%6D%6C`) and sends it as `/<encoded>`,
  `/public/<encoded>`, and `/../<encoded>`.

Every request uses `GET`, a 5-second timeout, and `allow_redirects=False` (so a
redirect shows as its own 3xx status rather than being followed).

## Requirements

- Python 3
- The `requests` library:

  ```bash
  pip install requests
  ```

## Usage

```bash
python3 encoded_slash_test.py [target] [options]
```

### Positional argument

| Argument | Description | Default |
| --- | --- | --- |
| `target` | Base URL to probe. | `https://dnh9gohi236m8.cloudfront.net` |

### Options

| Option | Description | Default |
| --- | --- | --- |
| `--rounds N` | Run the full pass `N` times, then stop. | `1` |
| `--loop` | Run forever until `Ctrl+C`. Overrides `--rounds`. | off |
| `--delay S` | Seconds to wait between passes. | `1.0` |
| `--interval S` | Seconds to wait between individual probes. | `0.5` |
| `-h`, `--help` | Show the built-in help and exit. | — |

## Examples

```bash
# One pass against the default target
python3 encoded_slash_test.py

# One pass against a custom target
python3 encoded_slash_test.py https://example.com

# Five passes, then stop
python3 encoded_slash_test.py --rounds 5

# Run continuously until Ctrl+C
python3 encoded_slash_test.py --loop

# Ten passes with a 2-second gap between them
python3 encoded_slash_test.py --rounds 10 --delay 2

# Faster per-probe pacing against a custom target, looping
python3 encoded_slash_test.py https://example.com --loop --interval 0.2
```

## Output

Output is timestamped and status codes are color-coded in a terminal:

- **2xx** green
- **3xx** cyan
- **4xx** yellow
- **5xx** red
- **errors** grey

Colors are automatically disabled when output is not a TTY (for example when
piping to a file or another command), so logs stay clean.

Example (colors not shown):

```
============================================================
ENCODED VALUE TEST - CONTROLLED LAB ENVIRONMENT
Target: https://dnh9gohi236m8.cloudfront.net
Mode:   3 round(s) | probe interval 0.5s | pass delay 1.0s
============================================================

[14:22:01] ########## ROUND 1 ##########
[14:22:01] === ENCODED SLASH TEST ===
[14:22:01]   encoded '/' -> %2F
[14:22:02]   /api%2Fv1%2Fusers       ->  403
[14:22:02]   /%2F%2Fetc%2Fpasswd     ->  404
[14:22:03]   /admin%2Fconfig         ->  404
[14:22:03] === ENCODED index.html TEST ===
[14:22:03]   encoded 'index.html' -> %69%6E%64%65%78%2E%68%74%6D%6C
...

============================================================
[14:22:30] Completed 3 round(s).
============================================================
```

`Ctrl+C` stops cleanly and still prints the number of completed rounds.

## Notes

- Only the HTTP status code is reported per probe — no response body or headers.
- The encoded values are built at runtime by `encode_all()`, which percent-encodes
  each UTF-8 byte of the input.
