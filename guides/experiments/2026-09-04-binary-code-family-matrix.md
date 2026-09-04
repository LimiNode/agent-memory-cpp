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

## Full-document reference replay (2026-09-04, corrected)

The pinned references were run on the frozen RU E5 fixture (`22,607` documents,
`1,252` queries, dimension `384`, seed `42`, candidate budget `512`, and
oversample `4x`). Exact inner-product reranking was applied to the encoded
candidate set. This is a representation diagnostic: nDCG is against the exact
E5 inner-product top-10 oracle, not the MIRACL qrels metric used above.

| Method | Bits | nDCG@10 | Top-10 overlap | p05 / worst overlap | Encode p95 ms | Search p95 ms | Payload B/doc | Model B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| RaBitQ-RR-1 | 128 | 0.998268 | 0.990735 | — | — | — | 20 | 198,144 |
| BBQ-block-1 | 128 | 0.998315 | 0.990735 | — | — | — | 48 | 198,144 |
| RaBitQ-RR-1 | 256 | 0.999996 | 0.999681 | — | — | — | 36 | 395,520 |
| BBQ-block-1 | 256 | 0.999996 | 0.999681 | — | — | — | 64 | 395,520 |
| RaBitQ-one-bit-reference | 384 | 1.000000 | 1.000000 | — | — | — | 52 | 592,896 |
| BBQ-block-1 | 384 | 1.000000 | 0.999920 | — | — | — | 80 | 592,896 |

The corrected replay supersedes the pre-correction quality table. The double-
dot L2 term and gain estimator were fixed, and serving payload excludes
diagnostic residual-energy fields. At 384 bits RaBitQ is the one-bit-per-input
dimension reference and reaches the exact oracle on this fixture. The raw
replay is `tmp/binary-reference-full-ru-corrected-v2.json`.

The serving scorer now uses packed-byte lookup tables instead of expanding all
codes with `unpackbits` on every query. On a synthetic `50,000 × 256` probe,
the portable packed path reduced p95 from `72.82 ms` to `18.62 ms` (3.9×),
with maximum score error `1.5e-5`. This is a correctness/performance fix for
the research implementation; native SIMD numbers still require a C++ replay.

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
| RaBitQ-RR-1 | 128 | no / yes | 0.6605 / 0.7822 | — | — | — |
| BBQ-block-1 | 128 | no / yes | 0.6638 / 0.7836 | — | — | — |
| RaBitQ-RR-1 | 256 | no / yes | 0.7171 / 0.8099 | — | — | — |
| BBQ-block-1 | 256 | no / yes | 0.7184 / 0.8099 | — | — | — |
| RaBitQ-one-bit-reference | 384 | no / yes | 0.7428 / 0.8178 | — | — | — |
| BBQ-block-1 | 384 | no / yes | 0.7362 / 0.8171 | — | — | — |

The corrected global replay is `tmp/binary-reference-k8-cascade-corrected-v2.json`.
It confirms that global compact scoring is not a product path: even 384 bits
remain far below the float prototype-IVF control (`~0.9996` overlap). Exact
local K8 is consistently valuable, while BBQ-like and RaBitQ are nearly tied
after refinement. The reported Python p95 values are exhaustive research
timings, not optimized SIMD serving claims.

## Local residual-IVF measurements (nlist=4096)

The first fixed-partition run used `nprobe=64`, generator budget `M=4096`,
and no exact local-K8 arm. Values below are address top-10 overlap against
the exact global K8 address oracle; they are not full native-R4 quality.

| Codec (residual) | Bits / payload | Overlap |
|---|---:|---:|
| INT4 power-.5 | 4 / 192 B | .8553 |
| INT5 power-.5 | 5 / 240 B | .9191 |
| INT6 power-.5 | 6 / 288 B | .9474 |
| INT8 linear | 8 / 384 B | .9684 |
| INT8 power-.5 | 8 / 384 B | .9737 |
| INT10 power-.5 | 10 / 480 B | .9796 |
| INT12 power-.5 | 12 / 576 B | .9816 |
| ITQ208 ADC | 208 / 26 B | .2691 |
| ITQ256 ADC | 256 / 32 B | .2954 |
| ITQ384 ADC | 384 / 48 B | .3039 |
| PQ8 | 8×4-bit / 16 B | .2888 |
| OPQ8 | 8×4-bit / 16 B | .3224 |
| RaBitQ208 | 208 / 34 B | .2849 |
| BBQ208 (FP16 scales) | 208 / 52 B | .2836 |
| RaBitQ384 | 384 / 56 B | .5987 |
| BBQ384 (FP16 scales) | 384 / 68 B | .5967 |

The scalar and corrected RaBitQ/BBQ rows show that residualization is useful
for scalar/correlation-corrected codes, while residual ITQ/PQ does not inherit
the strong document-level ITQ result. The 208→384 jump for RaBitQ/BBQ is real,
but remains an intrinsic prototype/address result until replayed through the
authoritative native R4 executable.

Scalar payloads above are complete 384-coordinate records: `ceil(384 × bits /
8)`. Model-side per-coordinate scales are reported separately and are not
mistaken for per-prototype payload.

The historical `nlist=1024, nprobe=256` control gives FP16 residual overlap
`1.0`; under the same coarse partition RaBitQ384 residual is `.57895` and
BBQ384 residual `.58092`. This separates coarse-navigation quality from the
compact-code bottleneck.

### Native-R4 replay status

The Python residual runner intentionally uses an NPZ research cascade. It is
not a substitute for the authoritative #269 native `K32/R0 → Hamming768 →
ADC64 → exact` replay. Native replay requires materialized codec-generated
address-row manifests bound to the historical layout and executable. The
corrected codec implementation and all 208/384 matrix rows are ready for that
binding; no production selection is licensed from the NPZ numbers alone.

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
| K8 RaBitQ-RR-1, 128b, M4096 + local K8 | 20 B/prototype | overlap .7822; corrected global replay | Python exhaustive | 12.7 MB + 198 KB | research |
| K8 BBQ-block-1, 128b, M4096 + local K8 | 48 B/prototype | overlap .7836; corrected global replay | Python exhaustive | 25.4 MB + 198 KB | research |
| K8 RaBitQ-RR-1, 256b, M4096 + local K8 | 36 B/prototype | overlap .8099; corrected global replay | Python exhaustive | 20.0 MB + 395 KB | research |
| K8 BBQ-block-1, 256b, M4096 + local K8 | 64 B/prototype | overlap .8099; corrected global replay | Python exhaustive | 32.7 MB + 395 KB | research |
| K8 RaBitQ-one-bit-reference, 384b, M4096 + local K8 | 52 B/prototype | overlap .8178; corrected global replay | Python exhaustive | 23.6 MB + 593 KB | research |
| K8 BBQ-block-1, 384b, M4096 + local K8 | 80 B/prototype | overlap .8171; corrected global replay | Python exhaustive | 36.3 MB + 593 KB | research |
| ANN Faiss exact flat | FP32 | R4 nDCG .661003; overlap 1.0000 | p95 145.862 ms | 1.536 GB class | control |
| ANN Faiss float IVF nprobe 512 | FP32 + IVF | R4 nDCG .668508; overlap .9737 | p95 30.445 ms | 3.086 GB class | control |
| ANN Faiss binary flat | 256-bit binary | R4 nDCG .643086; overlap .9013 | p95 8.423 ms | 1.568 GB class | control |
| ANN historical MIH m19/r56 | ITQ-256 | R4 nDCG .643241 | p95 25.737 ms | raw report | historical |

This is a comparison index, not a single leaderboard: quality columns use
different fixtures and oracles. It does expose the engineering frontier: R4
document codecs trade bytes against a passed quality gate, whereas K8 binary
references still need 256 bits plus local exact refinement and remain below
the float prototype-IVF control.

## Residual-IVF follow-up

The next factorized experiment is executable as
`tools/agent-memory-bench/run-local-residual-ivf.py`. It trains one frozen
float IVF partition, probes identical cells for every method, and compares
`original` versus `prototype_minus_cell_centroid` representations. The runner
includes ITQ Hamming/ADC at 128, 208, 256, and 384 bits, RaBitQ-RR-1,
BBQ-block-1, identical `nprobe × M` settings, and the complete downstream
cascade. `scores_subset` in the two reference codecs avoids materializing or
decoding the global 454k-row code matrix during local scoring; its p95 is the
appropriate local-appendix measurement.

The first full run is intentionally recorded separately from the global #289
results. Its raw output is `tmp/residual-ivf-full-4096.json` and uses
`nlist=4096`, `nprobe={16,64}`, `M={1024,4096}`. Wider `nlist` values and the
full `8/16/32/64/128 × 512/1024/2048/4096` grid remain a follow-up if this
first fixed-partition run shows a residual advantage.

## Common metrics and decision rule

The contract is [`binary-code-family-matrix.example.json`](../../tools/agent-memory-bench/binary-code-family-matrix.example.json).
Every measured row records candidate recall, final nDCG/top-10 overlap, p05 and
worst-query values, query encoding/search/rerank p95, payload/model/index bytes,
peak working set, and add/delete behavior. Full-document flat scans and local
IVF are presented side by side only as separate lanes. No method is selected by
quality alone; the relevant frontier is quality × request cost × resident
weight × update cost.
