# THQ-aware directional MIH triage

Date: 2026-09-06. Exploratory follow-up to the flat THQ study.

## Hypothesis

Use query-dependent confidence (distance from each thermometer threshold) to
choose a subset of MIH bands, probe radius-one neighbours, and avoid reading
the full 96--144 MB sequential THQ payload while retaining the THQ top-256
and exact-E5 top-10 results.

## Triage implementation

`tools/agent-memory-bench/evaluate-thq-mih.py` indexes THQ4 as 36 x 32-bit
bands and probes radius-one keys in the selected 8/16/36 bands.  It reports
candidate count, touched bytes, query time and exact-teacher top-10 survival.
The implementation is intentionally labelled a Python reference; it is not a
native/MDBX latency claim.

Smoke results on the frozen DE-1M materialization:

| selected bands | radius | queries | mean candidates | mean top-10 survival | touched bytes/query |
|---:|---:|---:|---:|---:|---:|
| 8 | 1 | 8 | 56.8 | .025 | 8.2 KiB |
| 16 | 1 | 4 | 115.3 | .050 | 16.6 KiB |
| 36 | 1 | 1 | 235.0 | .100 | 33.0 KiB |

The corresponding raw outputs are in the ignored `tmp/thq-full-scan-v2/`
directory (`mih-smoke.json`, `mih-smoke8-low.json`, `mih-one36.json`).

## Interpretation

The naive exact-band/radius-one construction fails the preregistered `.995`
top-10 survival gate by a wide margin.  Its low touched-byte count is not a
win because the thermometer code's local 32-bit neighbourhood does not cover
the semantic THQ nearest neighbours.  This does not disprove a native
THQ-aware MIH design: it rules out promoting this simple band subset as the
algorithm.

The next implementation must use THQ structure explicitly (coordinate-level
thermometer transitions or multi-radius weighted probes), and must be
benchmarked in native code against sequential THQ with complete p95/p99 and
bytes-read accounting.  Until then, sequential THQ remains the flat baseline.
