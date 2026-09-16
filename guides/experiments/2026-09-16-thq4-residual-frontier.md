# THQ4 reconstruction plus residual side-code frontier (2026-09-16)

## Status

`EXECUTED` as a bounded NumPy reference diagnostic.  This is a new research
line stacked on PR #419 (`28744f389aaefe7df8498ba42fe1d631d8b98694`).  It is
not a native latency gate and does not replace the pending canonical
152-query Gate 1.

## Question

Can a frozen 4-level THQ representation reconstruct enough of the document
vector that a small residual side-code (8/16/32 B per document) approaches the
quality of the 388 B/document INT8 final scorer?

The first check separates three effects that must not be conflated:

1. THQ interval-squared ranking;
2. THQ conditional-centroid reconstruction;
3. PCA residual correction on top of the reconstruction.

All residual codebook vectors and scalar scales were fitted from the detached
25,000-row training vector file.  Queries and qrels were not used for fitting.

## Setup

The runner is `tools/agent-memory-bench/run-thq4-residual-frontier.py`.
The source corpus is the frozen DE-1M E5 materialization:

* documents: 1,000,000 x 384 FP32, SHA-256
  `d4f67ebe91faa159eaaeb7884281ad0d0057c27cdb67c4007f260c6442636007`;
* training vectors: 25,000 x 384 FP32, SHA-256
  `1f581860cff679989f0661fb27623c650bc130e8ed4593b0abe08e5474c00b80`;
* THQ4 payload and thresholds are the independently audited #419 files;
* query slice: the first eight rows of the 305-query DE evaluation materialization,
  query-vector SHA-256 `fa6c467e01bbe8a8e725d75fd5ac84c90008235be921c4a4d360d756d0d0d7b2`.

The raw results and 8/16/32-byte code streams are retained outside Git under
`E:\_repoz\agent-memory-workspaces\thq-residual-frontier-artifacts`.
The PCA result is `thq4-residual-diagnostic-8q.json`; the residual-PQ result is
`thq4-residual-pq-diagnostic-8q.json`.

## Observed result

| scorer | logical bytes/document | mean teacher top-10 overlap | minimum overlap | ordered top-10 parity |
| --- | ---: | ---: | ---: | ---: |
| THQ4 interval-squared | 96 | 0.875 | 0.5 | 0/8 |
| THQ4 centroid reconstruction | 96 | 0.700 | 0.5 | 0/8 |
| THQ4 + PCA residual, 8 B | 104 | 0.725 | 0.6 | 0/8 |
| THQ4 + PCA residual, 16 B | 112 | 0.738 | 0.6 | 0/8 |
| THQ4 + PCA residual, 32 B | 128 | 0.738 | 0.6 | 0/8 |
| direct INT8 linear control | 388 | 1.000 | 1.0 | 6/8 |

The follow-up residual-PQ diagnostic used 8-bit subquantizers with 8, 16, or
32 subspaces, so its document payload is also 8/16/32 B:

| scorer | logical bytes/document | mean teacher top-10 overlap | minimum overlap | qrels nDCG@10 mean |
| --- | ---: | ---: | ---: | ---: |
| THQ4 + residual PQ, 8 B | 104 | 0.788 | 0.6 | 0.588 |
| THQ4 + residual PQ, 16 B | 112 | 0.813 | 0.6 | 0.616 |
| THQ4 + residual PQ, 32 B | 128 | 0.850 | 0.7 | 0.565 |

An independent decode check compared the fast PQ lookup score with an
explicit `faiss.ProductQuantizer.decode` reconstruction on 64 documents.  The
maximum absolute score discrepancy was below `1.8e-7` for all three payloads;
this verifies the arithmetic path, not ranking quality.

The centroid reconstruction residual has mean squared energy `0.038365` of
the unit-norm source energy.  The first 32 PCA components explain only
`0.129800` of that residual energy.  This is a broad residual, not a sharply
low-rank correction.  Training-only scales caused document-side saturation of
`2.85e-5`, `2.16e-5`, and `1.78e-5` of scalar values for the 8/16/32-byte
streams respectively; the codes are clipped and the saturation is reported,
not silently absorbed.

## Interpretation

The diagnostic does not support the naive claim that THQ4 centroid plus a
small PCA side-code is already a replacement for INT8.  Residual PQ is more
effective than PCA on this slice: the 16-byte PQ row exceeds the INT8 qrels
nDCG mean numerically, while its teacher overlap is still only `0.813` and it
has no ordered top-10 parity.  The 32-byte PQ row approaches the interval
THQ4 teacher overlap (`0.850` versus `0.875`) but does not improve qrels nDCG
monotonically.  These are useful signs for a PQ follow-up, not a production
selection result.  The residual spectrum remains a negative signal for a
linear PCA side-code specifically.

This is not a rejection of all structured residual coding.  It is a bounded
disconfirmation of the first PCA residual hypothesis.  Residual PQ/OPQ and
ordinal/rotated low-bit controls remain separate hypotheses and must be
trained on the same detached sample before a final decision.

## Limitations

* Eight queries are a diagnostic slice, not the canonical 152-query quality
  payload and not a held-out production acceptance gate.
* The measurements are NumPy reference rankings; no native SIMD, page, cache,
  or MDBX latency is established.
* The THQ4 base is the #419 payload, while centroids are newly fit from the
  25,000-row training split; this is intentional but means centroid and
  interval rows are different scorers.

## Next check

Run residual OPQ at 8/16/32 B and independently check PQ decode/ADC parity
against an explicit reconstructed-vector scorer.  Then promote the question
to the canonical quality gate only after the 152-query payload is restored;
do not use this eight-query result to select a production codec.
