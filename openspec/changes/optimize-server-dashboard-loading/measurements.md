# Synthetic dashboard measurements

Run `PANEL_BROWSER_EXECUTABLE="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" uv run --locked python -m scripts.benchmark_dashboard` (omit the executable override on CI). All network requests are intercepted and all SSH is fake. Fresh temporary state, 350 ms check response delay, 30 ms per summary command, 50 ms competing transport owner. First-row timing comes from a DOM observer. Summary timing includes page load and the API response. These are synthetic samples, not production latency estimates.

## Before

```json
[
  {
    "path": "cold",
    "first_row_ms": 490,
    "summary_ms": 374,
    "ssh_commands": 6,
    "transport_wait_ms": 52
  },
  {
    "path": "repeat",
    "first_row_ms": 471,
    "summary_ms": 356,
    "ssh_commands": 6,
    "transport_wait_ms": 53
  }
]
```
