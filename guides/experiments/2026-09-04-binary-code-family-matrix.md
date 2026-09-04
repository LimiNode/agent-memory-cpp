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

## Common metrics and decision rule

The contract is [`binary-code-family-matrix.example.json`](../../tools/agent-memory-bench/binary-code-family-matrix.example.json).
Every measured row records candidate recall, final nDCG/top-10 overlap, p05 and
worst-query values, query encoding/search/rerank p95, payload/model/index bytes,
peak working set, and add/delete behavior. Full-document flat scans and local
IVF are presented side by side only as separate lanes. No method is selected by
quality alone; the relevant frontier is quality × request cost × resident
weight × update cost.
