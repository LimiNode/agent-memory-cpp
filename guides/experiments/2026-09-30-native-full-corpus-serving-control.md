# Native full-corpus serving control (2026-09-30)

## Question

What does the current native THQ4 cascade cost on the complete 1M-document
materialization, before measuring persistent MDBX layout and OS-page behavior?

## Setup

The current branch build ran
`agent-memory-native-full-corpus-codec-benchmark.exe` over the frozen 1M
THQ4/INT8 materialization and the 152-query historical control fixture.  The
runner performed direct scalar INT8 scans and THQ4 byte-LUT top-128 cascades
for the linear and power-0.625 controls.  Inputs and runner hashes are in
`2026-09-30-native-full-corpus-serving-control.result.json`.

## Result

| Path | Mean ms/query |
|---|---:|
| Direct linear INT8 | 417.694 |
| THQ cascade + linear INT8 | 96.2208 |
| Direct power-0.625 INT8 | 618.174 |
| THQ cascade + power-0.625 INT8 | 96.4203 |

The native cascade's page counters are namespace-local proxies, not OS page
fault measurements:

| Counter | p50 | p95 | p99 |
|---|---:|---:|---:|
| Direct code/scale pages | 94,727 | 94,727 | 94,727 |
| THQ scan pages | 23,438 | 23,438 | 23,438 |
| Cascade rerank payload pages | 232 | 251 | 252 |
| Total namespaced cascade pages | 23,670 | 23,689 | 23,690 |

## Interpretation and limits

This is the first real 1M native serving baseline for the product phase.  It
shows the cost of the complete corpus and gives a control for later codec
comparisons, but it does not measure MDBX value reads, mmap locality, cold
faults, transaction duration, write amplification, insert/update, rebuild, or
generation publication.  It also uses the historical 152-query fixture, so it
does not close the fresh-quality gate.  No production profile or winner is
selected from this run.

The next measurements are the actual persistence gates: materialize each
frozen arm into the MDBX-backed layout, measure warm/cold p50/p95/p99 and page
faults, then measure single inserts, batch inserts, update/delete visibility,
and full 1M rebuild/publication cost under a fixed durability mode.
