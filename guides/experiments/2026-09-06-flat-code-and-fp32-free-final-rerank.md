# Flat compact codes and FP32-free final rerank

Date: 2026-09-06. Follow-up to `ec606d3` (THQ codec study); native source
changes are in the current research PR branch.

## Question

Can a flat compact-code scan replace the document FP32 read in the final
rerank, while retaining the previously validated float-IVF/K8/R4 path as a
quality-oriented alternative?

## Protocol

The DE-1M frozen materialization contains 1,000,000 document E5 vectors,
152 evaluation queries, teacher top-10 IDs and qrels.  THQ3 uses two
per-coordinate quantile thresholds (96 bytes/document); THQ4 uses three
thresholds (144 bytes/document).  Both are scanned sequentially with native
Hamming and shortlist sizes 128/256/512/1024.  The final stage is evaluated
against the same shortlist using either the FP32 dot-product control or a
true little-endian packed scalar payload.  Scalar levels use per-coordinate
training min/max and unsigned linear reconstruction:

| final payload | physical bytes/document |
|---|---:|
| packed INT8 | 384 |
| packed INT10 | 480 |
| packed INT12 | 576 |

Thus a THQ4 + INT10 index is 144 + 480 = 624 bytes/document (the earlier
628-byte estimate included a four-byte per-document scale; this materialization
uses a shared 384-coordinate min/max model, so no per-document scale is stored).

These are physical packed sizes; they must not be confused with the older
int16 storage control used by `ScalarScorer`.  The online scalar path reads
only the packed payload plus the small min/max model and the query E5 vector;
document FP32 vectors are used only by the control arm.

Materialization manifest:

* `tmp/thq-full-scan-v2/manifest.json`
* SHA-256 `f58e074e481dc910ca7b12b35bc27dc51097640704fb2b0c749018bcf2edd57b`

Native result:

* `tmp/thq-full-scan-v2/full-result.json`

The result contains per-query p05/worst overlap, qrels nDCG, p95/p99 scan and
top-K timings, packed-final timings and bytes-read accounting.  The native
runner is `agent-memory-native-thq-full-scan` and reports portable/AVX2 build
provenance through the build directory.

## Results (152 queries)

| route | final | K | overlap | nDCG@10 | p05 / worst overlap | p95 scan | p95 final |
|---|---|---:|---:|---:|---:|---:|---:|
| THQ3 | FP32 | 256 | .9967 | .6541 | 1.00 / .90 | 16.12 ms | .22 ms |
| THQ3 | INT8 | 256 | .9895 | .6572 | .90 / .90 | 16.12 ms | 2.58 ms |
| THQ3 | INT10 | 256 | .9947 | .6559 | .90 / .90 | 16.12 ms | 3.26 ms |
| THQ3 | INT12 | 256 | .9954 | .6540 | 1.00 / .90 | 16.12 ms | 3.45 ms |
| THQ4 | FP32 | 256 | .9993 | .6540 | 1.00 / .90 | 21.19 ms | .20 ms |
| THQ4 | INT8 | 256 | .9921 | .6572 | .90 / .90 | 21.19 ms | 2.35 ms |
| THQ4 | INT10 | 256 | .9974 | .6559 | 1.00 / .90 | 21.19 ms | 2.87 ms |
| THQ4 | INT12 | 256 | .9980 | .6540 | 1.00 / .90 | 21.19 ms | 3.30 ms |
| THQ4 | FP32 | 512 | 1.0000 | .6540 | 1.00 / 1.00 | 21.19 ms | .40 ms |
| THQ4 | INT10 | 512 | .9980 | .6559 | 1.00 / .90 | 21.19 ms | 5.67 ms |
| THQ4 | INT12 | 512 | .9987 | .6540 | 1.00 / .90 | 21.19 ms | 6.61 ms |

The qrels nDCG values are not interchangeable with teacher overlap: the
scalar reconstruction can change a tie/order among relevant documents, so a
slightly higher qrels value does not mean it exceeds the FP32 oracle.  The
quality gate remains both metrics plus tail behavior.

## Decision

* THQ4 K256 -> packed INT10 is the strongest current FP32-free candidate:
  .9974 teacher overlap, .6559 nDCG and 480 bytes/document, with a 2.87 ms
  p95 final stage.  INT12 is closer to the FP32 ordering (.9980) but costs
  576 bytes/document and is slower; it is the quality-biased option.
* INT8 is the compact option (384 bytes/document), but its .9921 overlap tail
  is materially weaker and needs a corpus/tail confirmation before promotion.
* THQ4 K512 reaches a perfect FP32 shortlist ceiling, but increases top-K
  maintenance and final work; K256 is the default frontier to optimize first.
* This does not retire the complex float-IVF -> local K8 -> K32/R0 -> R4
  cascade.  That remains the quality profile for large collections and an
  apples-to-apples MDBX replay is still required.

The R4 line was in fact improved after the original global-K8 experiments.
PR #269 introduced the float K8-prototype IVF generator:

```text
float K8-prototype IVF -> top-1024 addresses
-> K32 actual-document representatives -> learned R0
-> approximately 5k documents -> compact cascade -> top10
```

At M=4096, prototype IVF preserved about .9996 of the successful R4 routing
result; the configuration replay reported roughly 13.5 ms generator p95 and
15.7 ms local-K8 p95, with native total about 39.3 ms before adding external
generator time.  This is a real quality/architecture improvement over the
global K8 scan, whose roughly 63--69 ms cost remains an offline teacher
diagnostic.  It is not yet a production winner because native in-process
prototype-IVF, index footprint and MDBX page behavior were not measured under
the final serving contract.

Therefore the next meaningful comparison is `flat THQ` versus the improved
`float K8-prototype IVF -> K32/local refinement -> compact rerank`, with the
same downstream MDBX and answer-quality contract.  A new cheap gate/MIH is
required to beat this prototype-IVF quality while reducing total latency and
bytes, not merely to beat the obsolete global K8 implementation.

## Follow-ups

1. Repeat INT10/INT12 on an independent corpus and add a true int16 physical
   control; profile p99 and fused decode/dot kernels.
2. Complete the broad flat-code table (ITQ/ADC, PQ/OPQ, RaBitQ/BBQ, ternary,
   nonlinear INT4-12) under the same K and byte-read contract.
3. Benchmark directional/gradient-aware THQ-MIH against sequential THQ,
   including random reads, bytes and p95/p99.  The initial acceptance target is
   >= .995 exact-E5 top-10 survival with materially fewer touched bytes.
4. Replay the flat and float-IVF/K8/R4 profiles through the same MDBX postings
   and final answer-quality evaluation before selecting a production default.
