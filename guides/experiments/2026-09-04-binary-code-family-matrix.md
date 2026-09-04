# Binary/scalar code-family matrix

Date: 2026-09-04. This note defines the broad comparison requested after the
NeuRoute audit. It intentionally reports two lanes: a full-document lane and a
K8-local-IVF lane. Their corpora, candidate budgets, and downstream contracts
are different and must not be merged into one quality number.

## Existing full-document evidence

The frozen RU document lane has 22,607 evaluation documents, 1,252 queries,
the disjoint 25k calibration root, five ITQ seeds, 512 binary candidates, and
exact E5 reranking. Existing five-seed means include:

| Method | Payload | Coverage@512 | nDCG@10 |
|---|---:|---:|---:|
| ITQ binary Hamming, 128 bit | 16 B | 0.938626 | 0.792771 |
| ITQ binary ADC, 128 bit | 16 B | 0.984313 | 0.800762 |
| ITQ ternary ADC, 80 trits | 16 B | 0.967955 | 0.798057 |
| ITQ quaternary ADC, 64 symbols | 16 B | 0.951741 | 0.795541 |
| ITQ binary Hamming, 208 bit | 26 B | 0.980942 | 0.800185 |
| ITQ binary ADC, 208 bit | 26 B | 0.997764 | 0.801465 |
| ITQ ternary ADC, 128 trits | 26 B | 0.995543 | 0.801308 |
| ITQ quaternary ADC, 104 symbols | 26 B | 0.992859 | 0.801251 |
| PQ4 / OPQ4, 16 B | 16 B | 0.975895 / 0.986805 | 0.799694 / 0.800730 |
| PQ8 / OPQ8, 16 B | 16 B | 0.984010 / 0.988003 | 0.800640 / 0.800420 |

The same historical line contains linear/nonlinear scalar INT4–INT12,
uncertainty-mask, learned binary ADC, and MIH controls. Those rows need to be
re-emitted under the common matrix schema with explicit payload/model/index
bytes and p05/worst-query fields; their old numbers are not silently treated as
same-fixture replacements.

## K8-local-IVF lane

The K8 lane uses 454,322 prototypes and 152 semantic-anchor queries. Float
prototype IVF from #269 is the quality control (`~0.9996` overlap at `M=4096`).
The new matrix must use one frozen coarse partition and compare FP32, FP16,
scalar INT8, ITQ/Hamming, binary ADC, ternary, INT4–INT12, PQ4/PQ8, OPQ4/OPQ8,
RaBitQ, and BBQ-like corrected scoring inside the same probed lists. Each row
must run both `with_exact_local_k8` and `without_exact_local_k8` through
prototype→address dedup and the complete R4 cascade.

## BBQ and RaBitQ policy

BBQ and RaBitQ are not currently implemented in this repository. They must be
added only from a pinned, license-compatible specification or reference
implementation, including their query-side correction/oversampling metadata.
A homegrown one-bit sign code is not a valid substitute. Until then their
matrix rows remain `not_implemented`, with no fabricated quality or timing.

## Unified comparison of all measured code families

The requested single comparison is below. The fixture and oracle are kept in
the first column because RU-document, R4, and K8-prototype numbers are not
statistically interchangeable. A dash means that the historical run did not
measure that field; it is not a zero or an inferred value.

| Lane / method | Payload | Quality result | Latency result | Model/index bytes | Status |
|---|---:|---|---|---:|---|
| RU exact FP32 flat | 1,536 B/doc | nDCG 0.80145 | not recorded | — | exact control |
| RU PCA sign Hamming 128 | 16 B/doc | coverage .93347; nDCG .79295 | 8.05 s/full ordering | separate | measured |
| RU ITQ Hamming 128 | 16 B/doc | coverage .93746; nDCG .79005 | 8.05 s | separate | measured |
| RU ITQ binary ADC 128 | 16 B/doc | coverage .984313; nDCG .800762 | — | 197,632 B | measured |
| RU ITQ binary ADC 208 | 26 B/doc | coverage .997764; nDCG .801465 | — | 296,448 B | measured |
| RU ITQ ternary ADC 80 | 16 B/doc | coverage .97061; nDCG .79856 | 29.46 s | separate | measured |
| RU ITQ ternary ADC 128 | 26 B/doc | coverage .99577; nDCG .80150 | 43.28 s | separate | measured |
| RU ITQ quaternary ADC 64 | 16 B/doc | coverage .951741; nDCG .795541 | — | separate | measured |
| RU PQ4 / OPQ4, 16 B | 16 B/doc | nDCG .799694 / .800730 | — | 24,576 / 614,400 B | measured |
| RU PQ8 / OPQ8, 16 B | 16 B/doc | nDCG .800640 / .800420 | — | 393,216 / 983,040 B | measured |
| R4 FP16 | 768 B/doc | cross-dataset loss −.000018 | — | store-dependent | passed gate |
| R4 symmetric INT8 | 388 B/doc | loss −.001978; overlap .9918 | max p95 .030891 ms | store-dependent | passed gate |
| R4 symmetric INT4 | 196 B/doc | loss .007753 | — | store-dependent | failed gate |
| R4 five-level scalar | 148 B/doc | loss .086297 | — | store-dependent | failed gate |
| R4 ternary 2-bit | 100 B/doc | loss .345993 | — | store-dependent | failed gate |
| R4 nonlinear INT5 SIMDComp | 244 B/doc | fixed-pool loss −.001303 | rank-top10 p95 .035607 ms | store-dependent | retained |
| R4 existing ADC256 | 32 B/doc | loss .056336 | — | store-dependent | failed gate |
| R4 coordinate ADC384 | 48 B/doc | loss .031455 | — | store-dependent | failed gate |
| K8 RaBitQ-RR-1, 128b, M4096 + local K8 | 28 B/prototype | overlap .7743; nDCG .8723 | Python exhaustive | 12.7 MB + 198 KB | research |
| K8 BBQ-block-1, 128b, M4096 + local K8 | 56 B/prototype | overlap .7711; nDCG .8707 | Python exhaustive | 25.4 MB + 198 KB | research |
| K8 RaBitQ-RR-1, 256b, M4096 + local K8 | 44 B/prototype | overlap .8250; nDCG .9041 | Python exhaustive | 20.0 MB + 395 KB | research |
| K8 BBQ-block-1, 256b, M4096 + local K8 | 72 B/prototype | overlap .8283; nDCG .9055 | Python exhaustive | 32.7 MB + 395 KB | research |
| ANN Faiss exact flat | FP32 | R4 nDCG .661003; overlap 1.0000 | p95 145.862 ms | 1.536 GB class | control |
| ANN Faiss float IVF nprobe 512 | FP32 + IVF | R4 nDCG .668508; overlap .9737 | p95 30.445 ms | 3.086 GB class | control |
| ANN Faiss binary flat | 256-bit binary | R4 nDCG .643086; overlap .9013 | p95 8.423 ms | 1.568 GB class | control |
| ANN historical MIH m19/r56 | ITQ-256 | R4 nDCG .643241 | p95 25.737 ms | raw report | historical |

This is a comparison index, not a single leaderboard: quality columns use
different fixtures and oracles. It does expose the engineering frontier: R4
document codecs trade bytes against a passed quality gate, whereas K8 binary
references still need 256 bits plus local exact refinement and remain below
the float prototype-IVF control.

## Common metrics and decision rule

The contract is [`binary-code-family-matrix.example.json`](../../tools/agent-memory-bench/binary-code-family-matrix.example.json).
Every measured row records candidate recall, final nDCG/top-10 overlap, p05 and
worst-query values, query encoding/search/rerank p95, payload/model/index bytes,
peak working set, and add/delete behavior. Full-document flat scans and local
IVF are presented side by side only as separate lanes. No method is selected by
quality alone; the relevant frontier is quality × request cost × resident
weight × update cost.
