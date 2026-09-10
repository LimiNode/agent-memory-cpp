# Post-backlog THQ4 flat gate

Date: 2026-09-10. This note records the corrective full-dimensional replay
that follows the RP-THQ validation. It is deliberately evidence-bound: the
result is a representation/flat-scan ceiling, not an ANN-index claim.

## Protocol

The frozen DE-1M fixture contains 1,000,000 normalized E5 document vectors,
152 semantic queries, and exact E5 top-10 teacher IDs. THQ4 means four ordinal
levels and three thermometer bits per coordinate. Ranking is exhaustive plain
Hamming over 1,152 bits, with `(distance, document_id)` ordering. For valid
thermometer states `000, 100, 110, 111`, this is ordinal L1 over 384 levels.

The earlier `.9993 @256` orthogonal result was independently reproduced. The
matched-layout control used the first 100k documents for thresholds and the
full fixture for evaluation.

## Corrected locality result

| transform | @256 mean | @256 worst | @1k mean | r95 | r99 |
|---|---:|---:|---:|---:|---:|
| THQ4-384 / raw identity | .999342 | .9 | 1.000000 | 28 | 68.86 |
| Orthogonal-THQ4-384 | .998026 | .9 | 1.000000 | 26 | 68.24 |
| Hadamard-THQ4-384 | .998026 | .9 | 1.000000 | 30 | 85.43 |
| ITQ-THQ4-384 | .996711 | .8 | .999342 | 30.05 | 91.81 |
| PCA-THQ4-384 | .984868 | .7 | .996053 | 93.05 | 339.96 |

The primary finding is therefore raw THQ4-384, not random rotation:
full-dimensional raw THQ4 already has near-ideal locality on this fixture.
Random orthogonal/Hadamard transforms do not improve it, ITQ is close, and PCA
is materially worse. The previous claim that rotation repaired a weak raw-axis
representation is superseded. This does not invalidate reduced-dimensional
RP-THQ or any earlier k-means-affinity result.

Five orthogonal seeds remain stable: @256 mean `.998421`, population SD
`.001289`, range `.996053--.999342`; @1k and @5k are `1.0` for every seed.
The worst observed query at @256 retains 8/10 teacher documents and must remain
in every future gate.

## Absolute distance and shell geometry

The semantic query codes were regenerated with the same raw-coordinate
quartile thresholds and evaluated against the full 1M payload. Teacher
distances are much larger than a small Hamming radius despite the excellent
rank:

| statistic | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|
| teacher `dH` | 322 | 361 | 376.81 | 392 |
| top-256 cutoff | 383 | 397.45 | 400 | 404 |
| top-1k cutoff | 398 | 409 | 411.98 | 414 |
| top-5k cutoff | 416 | 425 | 426.98 | 429 |

The number of documents on the cutoff shell is itself non-trivial: p50/p95/max
are `25/36/43` at K=256, `91/132.05/153` at K=1k, and
`445/573.4/633` at K=5k. Across the 1520 teacher pairs, ordinal level deltas
`0/1/2/3` occur `235018/233809/94702/20151` times. These measurements explain
why a radius-one bit-MIH enumerator can miss the useful neighborhood: the
relevant rank lies around an ordinal-L1 shell of hundreds of steps.

## Native flat reference

The contiguous 144-byte payload was scanned with a GCC `-O3 -std=c++17
-march=native` XOR+POPCOUNT kernel. The run used three repetitions. Its 152
throughput queries were the first 152 materialized rows (a kernel workload, not
semantic query codes), so the timing is a ceiling rather than a quality replay.

| scope | p50 | p95 |
|---|---:|---:|
| distance scan, per query | 19.976 ms | 20.724 ms |
| scan + deterministic top-256, per query | 31.995 ms | not collected |

The selector accounts for roughly 12 ms/query in this implementation. A fused
integer-distance histogram and one pass over the saved `uint16` distances was
then measured at `20.994 ms/query` p50 (versus `32.295 ms/query` for the
`nth_element` selector), essentially removing the selector overhead. This
optimized flat reference is the baseline for any index claim.

## Interpretation and next gate

Previously tested partial-sphere, band, and radius-one implementations remain
negative for their concrete candidate enumeration strategies. They do not
prove that every ordinal-aware index is impossible. The open question is now:

> Can the first 1k--5k points in 384-dimensional ordinal-L1 space be enumerated
> below flat-scan cost?

The next experiment must therefore compare three systems under matched
candidate mass and bytes: flat THQ4-384, ordinary bitwise MIH over the same
1,152 bits, and a coordinate-aware ordinal multi-index/multiprobe. It must
report teacher survival, worst-query survival, tie-shell mass, postings and
candidate counts, bytes, query encoding, candidate generation, ordinal rerank,
exact rerank, and total p50/p95. MDBX persistence is deferred until this oracle
beats the optimized flat reference.

`production_activation: false`. Classical cosine-LSH remains a separate
untested retrieval architecture.

Raw reports are retained outside Git. The compact receipts record their hashes
and the source/build contract:

* `tmp/postbacklog-rthq4-384-seed20260908.json` through `...12.json`;
* `tmp/postbacklog-rthq-orthogonal-replay.json`;
* `tmp/postbacklog-rthq-native-benchmark.json`;
* `tmp/postbacklog-rthq4-orientation-controls.json`;
* `tmp/postbacklog-rthq4-itq-control.json`.
