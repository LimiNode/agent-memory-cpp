# Provisional historical serving profile screen (2026-10-03)

This is a historical serving screen, not a final product Pareto. It uses the
matched three-mode receipts from the merged serving study and computes a
complete-axis historical frontier where all quality coordinates are present.
Rows with missing comparable quality are kept in a separate eligibility set.
No weighted score is used.

## Historical complete-axis frontier

| Codec | Bytes/doc | Flat p50 | Prototype-IVF p50 | Modern R4 p50 | Historical nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| LSQ32 | 36 | 178.670 ms | 3.386 ms | 3.479 ms | 0.657264 |
| PLSQ8x6x8 | 52 | **177.522 ms** | 3.392 ms | **3.106 ms** | 0.656438 |
| TQ1 | 52 | 238.600 ms | **2.564 ms** | **2.511 ms** | 0.659176 |
| LSQ48 | 52 | 228.908 ms | 4.482 ms | 4.596 ms | **0.661515** |

RSLM1 is historically dominated by TQ1 under these complete axes: comparable
payload, slower flat and routed timings, and lower historical quality. It
remains listed in the underlying serving matrix, but is not on this frontier.

## Missing-quality frontier eligibility

The following finalist rows cannot be placed on a complete-axis frontier yet:

| Codec | Bytes/doc | Flat p50 | Prototype-IVF p50 | Modern R4 p50 | Quality status |
| --- | ---: | ---: | ---: | ---: | --- |
| INT8 exact cosine | 392 | 407.166 ms | **0.7485 ms** | **0.7927 ms** | unknown comparable quality; routed-speed eligible |
| TQ1+PQ8 | 64/68 | 289.122 ms | 2.773 ms | 2.613 ms | comparable historical quality unavailable |

Unknown quality is neither treated as zero nor silently treated as complete.
TQ1+PQ8 therefore remains explicitly `eligibility unresolved`, rather than
being removed from the candidate set by the TQ1 row.

## Representative presets

These presets are labels for follow-up experiments, not claims that the
selected codec is the unique Pareto choice:

- `compact` → LSQ32: smallest verified finalist payload;
- `routed-balanced` → TQ1: representative routed-balanced profile under the
  current historical screen;
- `routed-speed` → INT8 exact cosine: fastest matched downstream reranker;
- `flat-throughput-contender` → PLSQ8x6x8: fastest historical full-flat
  finalist and strong Modern-R4 latency;
- `historical-quality-contender` → LSQ48: highest historical nDCG@10.

## Fresh-quality boundary

The untouched 305-query bundle currently provides only route diagnostics:

- exact FP32 ceiling: nDCG@10 `0.672395`, MRR `0.693886`;
- Prototype-IVF + exact FP32 rerank: nDCG@10 `0.659746`, MRR `0.692147`,
  Recall@128 `0.862807`;
- three-seed R4-shaped prototype + exact FP32 rerank: nDCG@10 `0.645591`,
  MRR `0.686523`, Recall@128 `0.812628`.

These are not fresh packed-codec quality results. Therefore no finalist is
eliminated until the following decomposition is available for each arm:

```text
exact oracle → route → THQ top128 → packed final scorer
```

Persistent figures are also still deterministic page models, not MDBX
measurements. Cold/reopen/recovery/concurrent-publication tests remain a
separate gate.
