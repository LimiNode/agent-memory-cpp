# NeuRoute dense production-path performance audit

Date: 2026-08-31
PR context: #259, stacked on the frozen #258 kernel closure

## Question

Does the frozen R4 K32 cascade retain avoidable per-query implementation work
after the routing-kernel closure, and can that work be removed without changing
any score, shortlist, or final document identity?

## Audit and fixes

The audit found four safe implementation costs:

- the batched scorer allocated roughly 0.8 MiB of normalized, local-hidden, and
  joined scratch on every query;
- cascade order and scored-row vectors were allocated on every query;
- candidate materialization converted selected rows to addresses and then used
  `lower_bound` to recover the same rows;
- diagnostic SHA-256 construction and copies of candidates, Hamming, ADC, and
  exact lists were included in `timing.total` even though a production query
  does not serialize benchmark evidence.

Scratch is now reused per worker, selected rows flow directly into candidate
materialization, and diagnostic copies/hashes happen after the total timer.
The scorer arithmetic, codec bytes, route boundary, and downstream algorithms
are unchanged.

## Matched setup

The old #258 commit `e2ee741` and the audited binary were built with the same
MSVC toolchain in separate worktrees. The runner alternated old/new subprocess
order across homogeneous INT8 and fused-AVX2 nonlinear INT5, three seeds,
resident workers 1/8/16, and 256 MiB pressure at 8 workers. Each native report
retained the existing two measured batches and 304 query rows.

Frozen #258 reports are identity baselines, not latency gates. A first draft
incorrectly compared current timings directly with those older reports; its
pressure p95 moved by up to 1.49x. The corrected registered comparison uses
fresh matched binaries. It gates cross-seed resident mean latency and treats
pressure latency as descriptive because separate process working-set trimming
remains materially variable.

## Result

All six identities matched the frozen parent for all 7,296 audited query rows
in both binaries: score, selected addresses, candidates, Hamming top-768, ADC
top-64, and final top-10.

| Codec | Condition | Workers | Audited mean ms | Control mean ms | Ratio |
| --- | --- | ---: | ---: | ---: | ---: |
| INT8 | resident | 1 | 9.642 | 9.869 | 0.977 |
| INT8 | resident | 8 | 9.979 | 11.041 | 0.904 |
| INT8 | resident | 16 | 12.342 | 13.439 | 0.918 |
| nonlinear INT5 | resident | 1 | 10.400 | 10.619 | 0.979 |
| nonlinear INT5 | resident | 8 | 10.768 | 11.622 | 0.926 |
| nonlinear INT5 | resident | 16 | 11.879 | 13.550 | 0.877 |
| INT8 | 256 MiB | 8 | 22.625 | 21.117 | 1.071 |
| nonlinear INT5 | 256 MiB | 8 | 10.439 | 11.612 | 0.899 |

The worst resident cross-seed mean ratio is `0.9793`, below the registered
`1.02` no-regression cap. The audited path is frozen for the final-rerank and
external-comparison follow-ups. The pressure rows preserve the earlier compact
INT5 advantage, but their old/new delta is not interpreted causally.

Raw reports, result, and byte-replay evidence are under
`tmp/neuroute-dense-performance-audit/` and are not committed.

## Limits and next check

This PR removes implementation overhead; it does not change the scorer family
or prove a hardware ceiling. The R4 cascade still uses FP32 in its final top-64
stage. The next experiment replaces that measurement path with the selected
uniform INT5/SIMDComp physical codec and profiles fetch, unpack, conversion,
dot, selection, and total retrieval separately.
