# Fresh packed quality decomposition and persistence screen (2026-10-03)

## Question

After the merged serving-evidence gate, do the untouched 305 fresh queries
change the codec ordering, and what persistent page footprint should be expected
for the finalist payloads?  This remains a research measurement; it is not a
production selection or an MDBX implementation claim.

## Frozen inputs and command

The run uses the canonical `native-ann-confirmation-v1/de-1m/e5` bundle, the
source-bound THQ4 codes/thresholds, and the frozen 5,000-ID candidate streams:

```text
tools/agent-memory-bench/evaluate-fresh-packed-quality.py
```

The complete raw receipt is retained outside Git at
`tmp/fresh-routed/fresh-packed-quality.result.json`.  It covers both
`prototype_ivf` and `modern_r4`, all seven mandatory codecs, route exact FP32
rerank, THQ4 top-128 exact rerank, and packed final rerank.  The independent
exact baseline is `tmp/fresh-routed/fresh-exact.result.json`.

## Fresh quality result

The exact FP32 oracle is mean nDCG@10 **0.672395**, mean MRR **0.693886**.
The table shows mean nDCG@10 / mean MRR after the packed final scorer:

| Codec | Prototype-IVF | Modern R4 |
|---|---:|---:|
| INT8 | 0.661169 / 0.691213 | 0.646156 / 0.687369 |
| LSQ32 | 0.654126 / 0.689041 | 0.640992 / 0.683077 |
| LSQ48 | 0.645615 / 0.679226 | 0.632933 / 0.673557 |
| TQ1 | **0.666707 / 0.702377** | **0.650387 / 0.697156** |
| TQ1+PQ8 | **0.668428 / 0.706655** | **0.656407 / 0.702440** |
| PLSQ8x6x8 | 0.651938 / 0.682853 | 0.640796 / 0.679183 |
| RSLM1 | 0.654728 / 0.685666 | 0.638493 / 0.682292 |

The exact route stages for context are:

| Mode | route exact FP32 | THQ top-128 + exact FP32 |
|---|---:|---:|
| Prototype-IVF | 0.659746 / 0.689355 | 0.659772 / 0.689355 |
| Modern R4 | 0.645591 / 0.684560 | 0.645591 / 0.684560 |

Thus the decomposition is now explicit: route loss dominates the fresh
screen; THQ loss is negligible at this candidate budget; packed codec loss is
codec-specific and must not be inferred from historical quality rows.

## Persistent layout screen

The existing source-bound page model is
`tmp/fresh-routed/finalist-layouts.result.json`.  It is a deterministic 4,096
row segment/4,096-byte page model, not a measured MDBX run:

| Codec | Logical bytes/doc | Row-KV physical bytes (1M) | Segment physical bytes (1M) |
|---|---:|---:|---:|
| INT8 | 392 | 400,003,072 | 393,003,008 |
| LSQ32 | 36 | 44,003,328 | 37,003,264 |
| LSQ48 | 52 | 60,002,304 | 53,002,240 |
| TQ1 | 52 | 60,002,304 | 53,002,240 |
| TQ1+PQ8 | 68 | 76,001,280 | 69,001,216 |
| PLSQ8x6x8 | 52 | 60,002,304 | 53,002,240 |
| RSLM1 | 52 | 60,002,304 | 53,002,240 |

The model intentionally makes no claims about warm/cold latency, reopen,
rebuild, crash recovery, or concurrent publication.  Those remain the next
MDBX prototype gate for finalists selected only after this fresh quality
screen.

## Interpretation and limitations

TQ1+PQ8 is the strongest fresh quality row in both routed modes, but its
additional payload is visible in the layout model.  INT8 remains a strong
quality/speed control, while LSQ32 remains the smallest payload.  No codec is
eliminated solely from this screen: the candidate streams are a prototype
route, Modern R4 is explicitly a prototype fresh route, and persistence is a
page model rather than measured MDBX I/O.  Final Pareto selection requires
matched fresh latency plus finalist-specific MDBX warm/cold/reopen/recovery
measurements.

