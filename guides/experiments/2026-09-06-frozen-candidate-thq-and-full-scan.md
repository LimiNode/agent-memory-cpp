# Frozen candidate-pool THQ/codec replay and full scan

## Question

After routing, which document-side codec should rank the fixed candidate
pool, and can THQ4 scan the entire one-million-document corpus well enough to
remove PCA coarse routing?

## Protocol

Candidate IDs are frozen once per route and budget from the corrected native
materialization: PCA threshold, E5 centroid K=8, and Direct4096 top-32 plus
PCA fallback (seed 13), at 32k and 64k documents.  Every pool is evaluated
with ITQ256 Hamming, quantile THQ3, quantile THQ4, and an exact-E5 first-stage
control at K=256/512/768/1024.  The second stage is frozen native ADC64,
INT4/8/10/12 -> 64, or exact64.  Final quality is measured against the same
teacher top-10 and qrels nDCG.

The full-scan arm materializes THQ4 (three thermometer bits per coordinate,
144 bytes/document) using thresholds fitted on the first 100k documents.  A
native AVX2-enabled executable scans all one million codes, keeps top
256/512/768/1024, and exact-reranks those documents.

## Evidence

Frozen-pool report:
`tmp/frozen-candidate-codec-v2/report.json`.
SHA-256:
`7e58df65b1d15101acfd32f9e628ccd90b2ed119f9a356eb9dbb323bde41f3cb`.

THQ full-scan manifest:
`tmp/thq-full-scan-v1/manifest.json`.
SHA-256: `683f9ed47c9904a149c95916f0870d6a47b673511cd058e97746d61098886b08`.

Native full-scan result:
`tmp/thq-full-scan-v1/full-result.json`.
SHA-256:
`aaec04a6acc49506415fbfdd54a43ce7f8a7e856964608a9560b82fc26fa387b`.

## Frozen-pool result (64k, representative K=256)

| Route | First codec | Second stage | Overlap | nDCG |
| --- | --- | --- | ---: | ---: |
| PCA threshold | ITQ256 Hamming | native ADC64 | .503 | .493 |
| PCA threshold | THQ3 | INT10 -> 64 | .720 | .583 |
| PCA threshold | THQ4 | INT10 -> 64 | .720 | .583 |
| PCA threshold | exact control | exact64 | .722 | .581 |
| E5 K8 | ITQ256 Hamming | native ADC64 | .539 | .511 |
| E5 K8 | THQ3 | INT10 -> 64 | .801 | .612 |
| E5 K8 | THQ4 | INT10 -> 64 | .803 | .612 |
| E5 K8 | exact control | exact64 | .805 | .611 |
| Direct hybrid | ITQ256 Hamming | native ADC64 | .499 | .497 |
| Direct hybrid | THQ3 | INT10 -> 64 | .709 | .588 |
| Direct hybrid | THQ4 | INT10 -> 64 | .709 | .588 |

At K=1024 the same ordering remains.  THQ3/THQ4 plus INT10 is within roughly
0.002 overlap of the exact-pool control for E5 K8, while the existing
ITQ256-Hamming -> ADC64 cascade loses about 0.24 overlap on that pool.  INT8
and INT10 are the useful scalar second-stage controls; INT12 does not provide
a material gain over INT10 here.  The 32k frontier has the same pattern, but
the routing ceiling dominates quality.

## Full-corpus result

| Codec | K | Bytes/document | Overlap | nDCG | p95 scan / total ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| ITQ256 Hamming | 256 | 32 | .8526 | .6261 | 8.44 / 12.01 |
| ITQ256 Hamming | 1024 | 32 | .9414 | .6444 | 8.44 / 25.67 |
| THQ4 quantile | 256 | 144 | **.9993** | .6540 | 21.46 / 24.87 |
| THQ4 quantile | 512 | 144 | **1.0000** | .6540 | 21.46 / 29.11 |
| THQ4 quantile | 1024 | 144 | **1.0000** | .6540 | 21.46 / 38.85 |

THQ4 therefore reproduces essentially the complete exact-E5 teacher top-10
with a 256--512 shortlist after a full 1M scan.  Its measured native scan is
about 21.5 ms p95 on this machine, with 144 bytes/document read.  These are
single-process in-memory numbers, not MDBX page or concurrent-serving
latency.

## Interpretation

The current Hamming -> ADC cascade is not a reliable document-side choice for
the routed pools.  THQ3/THQ4 retains almost all of the exact-pool ceiling and
INT10 is sufficient for the second stage.  E5 K8 still wins because its pool
has the highest routing ceiling, but THQ substantially reduces downstream
loss.  Direct hybrid remains limited by routing rather than by THQ ranking.

The full scan changes the architecture decision space: THQ4 can be a serious
flat-search baseline and may eliminate PCA coarse routing when ~22 ms p95 and
144 B/document are acceptable.  A routed THQ path is still attractive when
bytes read or latency must be lower, but it must use THQ (not the old
Hamming/ADC pair) in the downstream comparison.

## Limitations and next checks

- THQ thresholds are raw-E5 quantiles from a detached 100k-document sample;
  residual/local and rotated THQ variants remain untested.
- Frozen-pool scoring is Python directional timing.  Native fused THQ and
  INT10 kernels need a separate benchmark with bytes read and p50/p95/p99.
- Full scan uses in-memory arrays; the next production check is a native
  flat THQ implementation against MDBX-backed routed THQ and E5 K8 routes.
