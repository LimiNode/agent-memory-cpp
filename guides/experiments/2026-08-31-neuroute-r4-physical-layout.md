# NeuRoute R4 representative physical layout

## Context

- Date: 2026-08-31
- PR: stacked on the representative codec frontier
- Status: full DE-1M physical materialization and paired native measurement complete

## Question

After the codec study selects INT8 for the FF32/K32 representative basis, how
much does address-major locality change real fetch, decode, dot-product, learned
address-score, and total latency relative to indirect document-ID gathers?

## Frozen protocol

The exact same one million documents are stored once in each measured file.
For every route seed, address-major files place the selected FF32 documents
first inside each 16-bit bucket, followed by the remaining documents in stable
global-position order. The document-ID-major INT8 control retains global
document order and gathers every representative indirectly.

The three layouts are address-major FP32, address-major INT8, and indirect
document-major INT8. Both compact layouts contain byte-identical INT8 records.
All use the same 152 paired requests per seed, exact K8 top-1024 address traces,
FF32 IDs, scalar features, and frozen learned scorer weights.

Warm-page-cache means sequentially prefaulting each file, one untimed request
pass, then three measured passes in deterministic shuffled order. The
fresh-process treatment starts a new executable for each of 15 preregistered
paired requests per seed and layout. The latter does not reset or control the OS
page cache and is not cold-disk evidence.

## Results

All 4,104 warm samples and 135 fresh-process samples replayed identical learned
address-score hashes between the two INT8 layouts. Median work is about 18.5k
representatives and 7.17 MB of INT8 bytes per query.

Warm-page-cache latency:

| Layout | Random reads p50 | Fetch p50/p95 ms | Decode p50/p95 ms | Address score p50/p95 ms | Total p50/p95/p99 ms |
|---|---:|---:|---:|---:|---:|
| Address-major FP32 | 1,024 | 70.055 / 80.245 | 11.962 / 13.886 | 12.508 / 12.981 | 102.146 / 115.079 / 120.691 |
| Address-major INT8 | 1,024 | 25.614 / 28.959 | 14.563 / 16.813 | 12.517 / 12.969 | **60.253 / 66.665 / 68.829** |
| Indirect INT8 | 18,478 | 166.720 / 190.105 | 14.626 / 16.780 | 12.513 / 13.001 | 201.270 / 227.645 / 239.295 |

Address-major INT8 is faster than indirect INT8 in every one of the 1,368
paired warm samples. The mean paired total delta is `-141.44 ms`; negative
means address-major is faster. The compact file reduces logical bytes by about
74.7% versus address-major FP32 and also avoids roughly 17.5k seeks per query.

Fresh-process first-request totals preserve the same sign:

| Layout | Total p50/p95/p99 ms |
|---|---:|
| Address-major FP32 | 104.459 / 116.336 / 120.433 |
| Address-major INT8 | **61.365 / 66.153 / 68.102** |
| Indirect INT8 | 203.585 / 230.693 / 238.746 |

Address-major INT8 wins all 45 paired fresh-process requests. This is evidence
about first request in a fresh process with the shared OS page cache left
uncontrolled, not evidence about cold storage media.

## Interpretation

Physical organization dominates this serving component. Compact records alone
do not make indirect gathers cheap: the two INT8 layouts decode, dot, and score
in essentially the same time, while fetch p95 falls from `190.10 ms` to
`28.96 ms` when about 18.5k document-record reads become at most 1,024
address-block reads with sequential scans inside each block.

Address-major INT8 also beats address-major FP32 because it reads roughly
7.17 MB rather than 28.38 MB per median request, despite paying a small INT8
decode cost. The selected physical candidate is therefore an address-major
full-corpus INT8 layout with FF32 records first; a separate representative
side-store is unnecessary.

This licenses the next, separately causal, full native cascade integration.
Production selection remains deferred until MDBX/container behavior,
concurrency, and all surrounding stages are included.

## Limitations

- Timings are directional single-host measurements.
- The files are fixed-record benchmark stores, not MDBX pages or transactions.
- Requests are single-threaded and do not model concurrent query contention.
- Sequential prefault gives warm-page-cache evidence, not steady-state service
  under memory pressure.
- Fresh process does not imply OS-page-cache cold or physical-device cold.
- The learned scorer is a direct native scalar implementation; further SIMD or
  batched matrix optimization is outside this layout comparison.

## Evidence

```text
materialization SHA-256: 95886a3b62eb0c2fc9182b721e94a252097395edc34d7604f7d11872bb5c039c
warm report SHA-256:     ba585b83c246ed0bd10d0de5ddc12e6727a0a7220ddce578139af3cdbeedfbfd
result SHA-256:          4bd3ad3e33a77cc1390aff40acaf8823a56fd8967b0f75764cb5e22aa8fce49a
evidence SHA-256:        73f1b2a82201f8434c9d5a42749100005bacfb907225fbe26f97c38180098a24
```

The evidence writer rehashes every physical file and sidecar, recomputes all
saved summaries and compact-layout identity checks, reruns the native
self-test, and independently replays one deterministic fresh-process request
per layout without requiring timing samples to reproduce byte for byte.
