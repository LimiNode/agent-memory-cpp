# Native production-kernel normalization v2 (2026-09-30)

## Context and question

PR #454 continues the frozen 1M-document THQ4/INT8 control. The review
required a finalist-shaped measurement rather than treating the earlier THQ +
INT8 control as a codec comparison. The question was whether dense THQ
scoring, bounded selection and INT8 reranking remain ordered-parity correct
when query preparation and stage timings are exposed separately.

## Protocol

- corpus: the frozen 1M THQ4 and INT8 materialisation used by the earlier gate;
- queries: 152 fixed queries, fixture SHA-256
  `abe14a8790bd488fc91b01b4b1d6ab664db1d2f2d69e67147ed8439f54c73191`;
- two warmups and ten repetitions per query, with fixed-seed per-query arm
  randomisation;
- direct INT8: register-accumulating float-query AVX2 score, bounded top-10;
- dense fallback: block32 THQ layout with AVX2 byte-LUT gathers, bounded
  top-128, then INT8 top-10;
- query preparation, score, top-k and rerank are reported separately and sum
  to `total`; page proxies and parity audits run outside the timed region;
- both flat samples and median-per-query percentiles are reported.

The run used the same Intel Xeon E5-2696 v3 host as the preceding control,
single-threaded and without pinned affinity, NUMA placement or power policy.
The runner was compiled with MSVC 19.44 (`/O2 /arch:AVX2`, C++17); these
single-host timings remain directional.

The first dense `pair-fp32` attempt was rejected during audit: two queries
changed a top-128 boundary because vector accumulation changed floating-point
addition order. This is recorded as a diagnostic, not silently folded into the
result. The accepted v2 path uses byte-LUT gathers with four accumulators and
matches the doc-major `unrolled4` addition grouping.

## Accepted result

| Arm | Mean total ms | Flat p50 | Flat p95 | Flat p99 | Query-median p50 | Query-median p95 | Query-median p99 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Direct INT8 top-10 | 106.748 | 106.384 | 110.238 | 112.434 | 106.403 | 108.423 | 109.845 |
| THQ block32 → INT8 top-10 | 84.233 | 83.672 | 88.938 | 92.266 | 83.717 | 86.358 | 87.831 |

Stage means were:

| Arm | Prepare | Score | Top-k | Rerank |
|---|---:|---:|---:|---:|
| Direct INT8 | 0.000 | 102.120 | 4.629 | — |
| THQ cascade | 0.104 | 80.111 | 3.949 | 0.069 |

The independent audit receipt reports:

- THQ block32 vs doc-major ordered top-128: `152/152`;
- AVX2 INT8 vs scalar ordered top-10: `152/152`;
- direct vs cascade ordered top-10: `152/152`;
- maximum INT8 score error: `1.37091e-6` absolute and `1.9054e-6` relative;
- rerank page proxy includes separate code and scale namespaces.

## Interpretation and limits

This is normalized in-memory kernel evidence. The cascade is about 1.27x faster
than the direct INT8 control on this host, but the result is not a codec winner,
not a quality result, and not an MDBX serving claim. It does not compare
LSQ/PLSQ/TQ/RSLM1 native scorers, R4 candidate topology, warm/cold persistence,
or fresh qrels.

The exact source, binary, raw JSONL, summary and input hashes are recorded in
`2026-09-30-native-production-kernel-control-v2.result.json`; raw and binary
artifacts remain outside Git under the local fidelity workspace.

## Reproduction and next gate

The runner is `tools/agent-memory-bench/native-full-corpus-codec-benchmark.cpp`
with `--production-control`. Recompute the compact receipt with
`tools/agent-memory-bench/audit-native-production-kernel-control.py` before
using any timing or parity number. The next production-shaped gate is a
candidate-stream workload (R4/random IDs → THQ → top-128 → finalist scorer),
followed by layout-specific MDBX measurements. No finalist is promoted from
this control alone.
