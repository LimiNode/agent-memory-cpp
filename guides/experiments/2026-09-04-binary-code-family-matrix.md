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

The matrix now includes two standalone NumPy research references in
`tools/agent-memory-bench/binary_code_references.py`; no dependency is added to
the C++ project. `rabitq_reference` is pinned as **RaBitQ-RR-1** (a
paper-derived research specification; it is not binary-compatible with an
official RaBitQ package): a seeded
orthogonal random rotation, one-bit signs, per-vector L1 gain, norm and
residual-energy correction, and query-side oversampling (default 4x).
`bbq_like_reference` is pinned as **BBQ-block-1 (BBQ-like)**: the same seeded
rotation split into blocks, sign bits plus one L1 scale per block, norm and
residual-energy metadata, and explicit oversampling. It is deliberately named
BBQ-like: it is a transparent, license-compatible implementation of the
published BBQ design pattern, not a claim of binary compatibility with an
external vendor implementation.

For RaBitQ-RR-1, with centered/rotated vector `z`, the stored payload is
`sign(z)` plus `g=mean(abs(z))`, `r=||x-mean||`, and
`e=max(r²-Bg²,0)`. Query scoring reconstructs the corrected inner product
`d=g * sum(sign(z) * z_query)` and ranks by
`d - 0.5*(r² + ||q-mean||² - 2d)`. BBQ-block-1 stores one `g_j` per block and
uses the sum of block corrected products before applying the same norm term.
Candidates are returned at `top_k * oversample` (default 4x), leaving exact
reranking to the caller. These formulas and little-endian `uint64` packing are
the compatibility contract for this study.

The exact equations and byte layout are in the module docstring and are tested
by `test_binary_code_references.py`. Matrix reports must retain the `spec`
field and must not merge these rows with official BBQ/RaBitQ results. The
standalone runner is `evaluate-binary-code-references.py`; it reports quality,
query/search p95, payload/model bytes, working-set estimate, and the effect of
oversampling.

## Full-document reference replay (2026-09-04)

The pinned references were run on the frozen RU E5 fixture (`22,607` documents,
`1,252` queries, dimension `384`, seed `42`, candidate budget `512`, and
oversample `4x`). Exact inner-product reranking was applied to the encoded
candidate set. This is a representation diagnostic: nDCG is against the exact
E5 inner-product top-10 oracle, not the MIRACL qrels metric used above.

| Method | Bits | nDCG@10 | Top-10 overlap | p05 / worst overlap | Encode p95 ms | Search p95 ms | Payload B/doc | Model B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| RaBitQ-RR-1 | 16 | 0.2343 | 0.2250 | 0.0 / 0.0 | 3.801 | 3.807 | 20 | 26,112 |
| BBQ-block-1 | 16 | 0.2867 | 0.2662 | 0.0 / 0.0 | 12.988 | 14.267 | 48 | 26,112 |
| RaBitQ-RR-1 | 32 | 0.4494 | 0.4066 | 0.0 / 0.0 | 5.983 | 6.125 | 20 | 50,688 |
| BBQ-block-1 | 32 | 0.5119 | 0.4599 | 0.0 / 0.0 | 11.293 | 11.902 | 48 | 50,688 |
| RaBitQ-RR-1 | 64 | 0.8388 | 0.7609 | 0.3 / 0.0 | 9.101 | 9.632 | 20 | 99,840 |
| BBQ-block-1 | 64 | 0.8634 | 0.7921 | 0.4 / 0.0 | 17.703 | 17.234 | 48 | 99,840 |
| RaBitQ-RR-1 | 96 | 0.9736 | 0.9329 | 0.7 / 0.3 | 14.069 | 14.414 | 28 | 148,992 |
| BBQ-block-1 | 96 | 0.9786 | 0.9427 | 0.7 / 0.4 | 25.335 | 25.486 | 56 | 148,992 |
| RaBitQ-RR-1 | 128 | 0.9957 | 0.9815 | 0.9 / 0.6 | 24.468 | 24.968 | 28 | 198,144 |
| BBQ-block-1 | 128 | 0.9967 | 0.9840 | 0.9 / 0.6 | 28.182 | 28.283 | 56 | 198,144 |

The curve is strongly monotone through 128 bits and has not saturated at 64
bits. BBQ-block-1 is consistently a little more accurate, at roughly 2x the
per-document correction payload and higher CPU cost. At 128 bits both variants
are close to the exact oracle, but this does not yet establish K8 address
utility or full-cascade quality. The raw replay is
`tmp/binary-reference-full-ru.json`.

## K8 prototype → address → R4 replay (2026-09-04)

The references were also evaluated on the frozen semantic-anchor artifact
(`454,322` prototypes, `152` queries). Prototype ownership was derived from
the authoritative centroid postings and validated for every prototype. For
each address budget, binary prototype scores were deduplicated to addresses;
the exact-local arm rescored a `4x` address pool with exact K8 prototype dots
before truncating to the requested budget. Both arms then ran the frozen
Hamming768 → ADC64 → exact-document top-10 cascade. The most useful `M=4096`
slice is:

| Method | Bits | Local K8 | Final overlap | Rank nDCG@10 | p05 / worst overlap | Mean docs entering Hamming |
|---|---:|:---:|---:|---:|---:|---:|
| RaBitQ-RR-1 | 128 | no | 0.6059 | 0.7414 | 0.3 / 0.1 | 73,884 |
| RaBitQ-RR-1 | 128 | yes | 0.7743 | 0.8723 | 0.5 / 0.3 | 71,831 |
| BBQ-block-1 | 128 | no | 0.6099 | 0.7483 | 0.3 / 0.1 | 73,577 |
| BBQ-block-1 | 128 | yes | 0.7711 | 0.8707 | 0.5 / 0.3 | 71,735 |
| RaBitQ-RR-1 | 192 | no | 0.7230 | 0.8351 | 0.4 / 0.3 | 70,937 |
| RaBitQ-RR-1 | 192 | yes | 0.8066 | 0.8912 | 0.5 / 0.3 | 70,937 |
| BBQ-block-1 | 192 | no | 0.7289 | 0.8402 | 0.4 / 0.2 | 70,913 |
| BBQ-block-1 | 192 | yes | 0.8099 | 0.8937 | 0.5 / 0.3 | 70,913 |
| RaBitQ-RR-1 | 256 | no | 0.7868 | 0.8784 | 0.455 / 0.3 | 70,586 |
| RaBitQ-RR-1 | 256 | yes | 0.8250 | 0.9041 | 0.5 / 0.4 | 70,586 |
| BBQ-block-1 | 256 | no | 0.7908 | 0.8795 | 0.455 / 0.3 | 70,570 |
| BBQ-block-1 | 256 | yes | 0.8283 | 0.9055 | 0.5 / 0.4 | 70,570 |

The complete `M=1024/2048/8192` rows are in
`tmp/binary-reference-k8-cascade-128.json` and
`tmp/binary-reference-k8-cascade-wide.json`. At `M=4096`, 256 bits is still
improving but remains well below the float prototype-IVF control (`~0.9996`
overlap). Exact local K8 is consistently valuable, while BBQ-like and RaBitQ
are nearly tied once local refinement is enabled. The reported Python p95
values are exhaustive research timings, not optimized SIMD serving claims.

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
