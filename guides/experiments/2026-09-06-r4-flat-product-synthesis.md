# R4, flat THQ and product-profile synthesis

Date: 2026-09-06. This note consolidates the post-#269 R4 improvement with
the flat THQ/FP32-free results from `fbf7f7c`.

## What changed after the original R4 result

The original quality path used a global K8 scan over roughly 454k prototypes.
That scan cost about 63--69 ms/query and was the dominant bottleneck.  PR #269
introduced float K8-prototype IVF: coarse IVF selects prototype regions before
local K8, then the unchanged K32/R0 and R4 document cascade runs inside those
regions.  At M=4096 it preserved about `.9996` of the successful R4 routing
result.  The replay measured approximately 13.5 ms generator p95, 15.7 ms
local-K8 p95 and 39.3 ms native total before external-generator cost.

Therefore the product quality reference is now:

```text
float K8-prototype IVF -> local exact K8 -> K32 actual-doc representatives
-> learned R0 -> R4 postings -> document codec cascade
```

The global K8 scan is retained only as an offline teacher/ceiling.  Native
in-process IVF, serialized footprint and MDBX page behavior remain open.

## Flat comparison

The independent DE-1M flat replay showed:

| Profile | K | Teacher overlap | nDCG@10 | p95 scan | Final payload |
|---|---:|---:|---:|---:|---:|
| THQ3 -> FP32 | 256 | .9967 | .6541 | 16.12 ms | 1536 B control |
| THQ4 -> FP32 | 256 | .9993 | .6540 | 21.19 ms | 1536 B control |
| THQ4 -> packed INT10 | 256 | .9974 | .6559 | 21.19 ms | 480 B |
| THQ4 -> packed INT12 | 256 | .9980 | .6540 | 21.19 ms | 576 B |

THQ3 is therefore a real low-weight contender, not merely a THQ4 ablation.
INT10 is the current storage-balanced FP32-free choice; INT12 is the
quality-biased choice.  The measured scalar final kernel is still slower than
the FP32 control because the current packed decoder is scalar.  Existing native
kernel measurements (`tmp/document-codec-native-optimized-5000-v1.json`) show
that integer and fused kernels can materially reduce this gap, but packed
INT10/12 require a dedicated AVX2 implementation before latency claims are
made.

## Agreed research batch

1. Run the same native/MDBX contract for flat THQ3/4 and post-#269 prototype
   IVF/R4, including p50/p95/p99, bytes read and qrels tails.
2. Add fused packed INT10/12 and an explicit int16 physical-storage control.
3. Implement THQ-aware directional MIH.  Its first gate is >= `.995`
   exact-E5 top-10 survival while touching materially fewer bytes than the
   sequential THQ scan; random access and p99 are mandatory.
4. Test a cheap gate before K32/local refinement and compare it against the
   improved prototype-IVF path, not against obsolete global K8.
5. Replay all survivors through identical MDBX postings and answer-quality
   evaluation before selecting a default.

The decisive replay has three, not two, controls:

```text
A. flat THQ4 -> K256 -> INT10
B. prototype-IVF -> candidate documents -> THQ4 -> INT10
C. prototype-IVF -> local K8 -> K32/R0 -> ~5k -> THQ4 -> INT10
```

The candidate pool sweep is `5k / 10k / 20k / 40k / 64k / 100k / 200k / 1M`.
This exposes whether K8/K32/R0 actually pays for itself once THQ can cheaply
rerank a larger pool, and identifies the flat/IVF crossover as corpus size
grows.  Every row must include stage-by-stage teacher survival, qrels nDCG,
p05/worst query, p95/p99, bytes touched and MDBX page behavior. The `.9996`
figure from #269 is specifically prototype-generator teacher survival at
M=4096, not the final retrieval score.

The raw-bit THQ-MIH triage is closed as a product candidate.  A separate
ordinal/threshold-transition index remains open: its probes must represent
valid quantized-coordinate level transitions and charge each threshold
crossing by query-specific margin, rather than enumerating arbitrary flipped
bits.

The intended product matrix is two-dimensional: routing profile (flat,
balanced IVF, quality prototype-IVF/R4) crossed with final representation
(FP32, FP16, INT10, INT12).  FP32-free is not a separate routing architecture.
