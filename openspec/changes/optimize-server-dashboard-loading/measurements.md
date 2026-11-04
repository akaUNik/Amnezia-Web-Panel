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

## After

The harness waits for the delayed live check to complete after recording the row and summary, so the repeat page also receives a populated status snapshot. Transport contention is injected on both paths but only a real SSH collector waits on it.

```json
[
  {
    "path": "cold",
    "first_row_ms": 115,
    "summary_ms": 185,
    "ssh_commands": 1,
    "transport_wait_ms": 50
  },
  {
    "path": "repeat",
    "first_row_ms": 121,
    "summary_ms": 103,
    "ssh_commands": 0,
    "transport_wait_ms": 0
  }
]
```

Summary command count: 6 → 1 cold, 6 → 0 warm. First row: 490 → 115 ms cold, 471 → 121 ms warm. Summary response: 374 → 185 ms cold, 356 → 103 ms warm. Warm transport wait: 53 → 0 ms. Page/static asset overhead is included; these single synthetic samples are evidence of request ordering and remote work reduction, not a production SLA.

## Validation

- Targeted backend and real-browser regressions pass, including stalled SSH ACK/status/stdin, output windows, rekey cleanup, lifecycle fencing, delayed state races, cache TTL/retention/cancellation/invalidation, warm/stale/error UI, and authorization.
- `openspec validate optimize-server-dashboard-loading --strict` and `git diff --check` pass.
- The first macOS full-suite run exposed fixture updates plus host differences: BSD `chmod 600 --` and symlinked `/var` temporary paths. Fixture updates pass; backup tests pass with `TMPDIR=/private/tmp`. The unchanged Linux shell tests will be verified by the standard GitHub Linux CI job.
