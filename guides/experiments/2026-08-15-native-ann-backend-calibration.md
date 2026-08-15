# Native Binary ANN Backend Calibration

Date: 2026-08-15  
Measured source: `7022e5927f26c0c3ef36e31edc5ca9dab1722de0`  
Scope: calibration only; this is not a backend or production claim.

## Hypothesis

With one shared ITQ-256 representation and cascade, a native BinaryFlat or
BinaryHNSW candidate generator might provide a more useful latency/quality/
memory point than the frozen fixed-r56 native MIH `m19` configuration.

## Predeclared setup

The calibration materialization is the established disjoint RU root with
manifest SHA-256
`fb5af79a70a8f61e27c9615c178203599ed5dc10f287d0741d132d97f0218856`.

All treatments use ITQ seed 52, 50 iterations, Hamming top-768, binary ADC
top-256, and exact E5 rerank top-256. Timing uses all 4,326 calibration queries,
one warmup and five measured repeats on a warm immutable native index. Candidate
generator p50 is the primary objective; independently timed component values are
not added together as an alternative latency.

The only MIH treatment is the already frozen `m19` schedule: nine 14-bit plus
ten 13-bit bands, local radius two in every band, preserving fixed-r56 Hamming
candidate inclusion. Flat has no routing parameter. HNSW is a predeclared native
`hnswlib` v0.8.0 grid with `M ∈ {16,24,32}`, `efConstruction=200`, and
`efSearch ∈ {768,1024}`. Its pinned source revision is
`3f3429661187e4c24a490a0f148fc6bc89042b3d`.

Every native run exports its own Hamming and ADC shortlists. A separate evaluator
uses those exports and the E5/qrels root; it does not substitute a Python HNSW
implementation. Admissibility requires 95% bootstrap lower bounds of at least
0.90 for E5-oracle survival after ADC and at least 0.98 for reranked/full-E5
nDCG retention, auxiliary index bytes at most 8 MiB, and total resident bytes at
most 10 MiB. HNSW selection is then deterministic by candidate-generator p50,
cascade p50, total bytes, and identifier.

## Results

| treatment | admissible | candidate-generator p50 ms/query | cascade p50 ms/query | auxiliary bytes | ADC oracle lower 95% | nDCG-retention lower 95% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| MIH `m19`, fixed-r56 | yes | 0.5045 | 0.7177 | 4,439,764 | 0.9034 | 0.9851 |
| BinaryFlat-256 | yes | 0.6620 | 0.8785 | 0 | 0.9767 | 0.9993 |
| HNSW `M16`, ef 768 | yes | 0.8280 | 1.0385 | 6,782,088 | 0.9762 | 0.9987 |
| HNSW `M16`, ef 1024 | yes | 1.1068 | 1.3374 | 6,782,088 | 0.9764 | 0.9987 |
| HNSW `M24`, ef 768 | no: bytes | 1.0748 | 1.3050 | 9,178,716 | 0.9768 | 0.9988 |
| HNSW `M24`, ef 1024 | no: bytes | 1.3249 | 1.5520 | 9,178,716 | 0.9770 | 0.9994 |
| HNSW `M32`, ef 768 | no: bytes | 1.2628 | 1.4903 | 11,576,780 | 0.9770 | 0.9994 |
| HNSW `M32`, ef 1024 | no: bytes | 1.5742 | 1.7992 | 11,576,780 | 0.9767 | 0.9994 |

The frozen representatives are therefore MIH `m19`, BinaryFlat-256, and HNSW
`M16`/ef 768. This selects the fastest admissible HNSW variant, not a global
winner.

## Interpretation and limits

On this calibration corpus, fixed-r56 MIH is faster than both challengers while
remaining inside the same memory gate. Flat and HNSW retain more of the E5 oracle
after ADC, but that quality margin does not compensate for their greater measured
candidate-generator latency under the predeclared rule. The HNSW result is still
useful: a native graph implementation was tested under the same executable,
Hamming, ADC, and exact-rerank path, rather than against Python overhead.

These data have now been used for selection. No member of this table may be
retuned using the confirmatory corpus, and fixed-r56 remains a candidate-inclusion
property rather than exact E5 kNN.

## Next check

Prepare a previously unused MIRACL German corpus from pinned raw revisions. Check
that its language-qualified document and query identifiers are disjoint from the
RU calibration root, materialize it once, then evaluate the three frozen backends
at 25k documents. Reuse those frozen configurations for real, non-duplicated
100k and 1M document scales. Report build time, index bytes, warm latency p50/
p95/p99, candidate-generator total, cascade total, and backend diagnostics; do
not tune a backend per scale or on the fresh split.
