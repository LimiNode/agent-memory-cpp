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

## Common metrics and decision rule

The contract is [`binary-code-family-matrix.example.json`](../../tools/agent-memory-bench/binary-code-family-matrix.example.json).
Every measured row records candidate recall, final nDCG/top-10 overlap, p05 and
worst-query values, query encoding/search/rerank p95, payload/model/index bytes,
peak working set, and add/delete behavior. Full-document flat scans and local
IVF are presented side by side only as separate lanes. No method is selected by
quality alone; the relevant frontier is quality × request cost × resident
weight × update cost.
