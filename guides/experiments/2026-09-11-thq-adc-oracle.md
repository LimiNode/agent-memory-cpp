# 2026-09-11 THQ asymmetric interval-distance oracle

This experiment tests whether the continuous FP32 query value can improve the
excellent raw THQ4-384 ordinal locality.  It is an exhaustive ranking oracle,
not an ANN index, persistence benchmark, or production authorization.

## Protocol

The frozen DE-1M fixture contains 1,000,000 normalized E5 document vectors,
152 semantic queries, and exact E5 top-10 teacher IDs.  Documents use the
canonical raw THQ4 representation (four levels, three thermometer bits per
coordinate, 1,152 bits / 144 B).  Thresholds are quartiles fitted on the first
100,000 training documents only.  Every arm ranks all documents with the
deterministic `(score, document_id)` order and uses the same teacher queries.

Compared scores are:

* plain ordinal L1 (identical to Hamming for valid thermometer states);
* asymmetric interval L1: distance from the FP32 query coordinate to the
  candidate level's threshold interval;
* squared interval distance;
* interval L1 normalized by the train-only per-coordinate inter-quartile span.

The output records top-64/128/256/512/1k/2k teacher survival and teacher rank
quantiles.  Full per-query JSON is retained outside Git; the compact receipt
below records its SHA-256 and the source hash.

## Result

The full replay improves the low-budget tail substantially.  Plain Hamming
survival is `.999342 @256` (worst `.9`), while interval L1, squared interval,
and IQR-normalized interval all reach `1.0 @256` (worst `1.0`).  At `@64`,
the means are `.989474`, `.996711`, `.997368`, and `.996711`, respectively.
Squared interval has the best mean teacher-rank p95 (`16.60` versus `26.99`
for Hamming), with interval L1 close behind (`17.43`).  The scan is still an
exhaustive pass; the diagnostic batch-derived p50 is about `10.5 s/query` in
this Python implementation and is not a native latency claim.

The compact receipt is
`2026-09-11-thq-adc-oracle-result.json`; the raw report is retained outside
Git as `postbacklog-thq-adc-oracle.json` (SHA-256
`439acba58397d76f88dc2d2b4e6d74a987269e1ba8fe72aac5e069617f3acb1`).

`production_activation: false`.

## Interpretation and gate

An interval score is useful only if its held-out rank/survival improvement is
material and stable, while remaining an exhaustive flat scan.  A positive
result licenses a subsequent candidate-generation oracle, not an index.  This
replay passes that representation-level gate: continuous query margins are a
promising reranking signal.  It does not establish a cheaper selective index;
candidate enumeration and native cost remain open.  A negative result for any
single score would close only that control, not other ordinal multiprobe or
classical cosine-LSH architectures.
