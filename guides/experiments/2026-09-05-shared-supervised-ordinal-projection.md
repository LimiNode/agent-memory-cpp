# Shared supervised ordinal projection

Date: 2026-09-05. Follow-up to corrected PR #299 replay.

## Question

The historical learned Binary12 router predicts independently supervised bits
inside a frozen PCA document partition. This can lose joint cell structure:
the marginal bit probabilities of multimodal teacher addresses may form a
cell which contains none of the relevant documents. The experiment tests
whether a shared query/document projection improves the geometry before
ordinal quantisation.

## Protocol

Runner:

```text
tools/agent-memory-bench/run-shared-supervised-ordinal-projection.py
```

The projection is initialized from PCA fitted on `documents[::4]`. For each
training query, exact E5 top-10 documents are positives and uniformly sampled
documents are negatives. A shared linear matrix is trained for both queries
and documents with:

```text
softplus(L1(query, positive) - L1(query, negative) + margin)
+ occupancy regularizer
+ PCA-anchor regularizer
```

Quantile thresholds are fitted after training on the document sample. Runtime
cell probing uses the query-specific distance to the crossed threshold; it does
not use a global bin-width cost. Replication is fixed at `R=1`. Evaluation is
the routing ceiling with exact inner-product ranking inside the candidate set,
`K=256`.

The cache contains exact top-10 labels but not ranks 11-1000, so this first
run uses uniform negatives. It is therefore a geometry experiment, not yet a
hard-negative training result.

## Target multimodality diagnostic

Under the PCA12 document partition, training top-10 targets contain on average
8.73 distinct binary addresses and the modal address contains only 19.7% of
the ten documents. Mean marginal bit entropy is 0.511 bits. This supports the
Frankenstein-address concern: independent bit probabilities are a poor proxy
for the joint teacher-address distribution.

## DE-1M result

Manifest SHA-256:
`25f10151c60461edbfd0ac52e66caf5777834c80ba4637a8986e03de14f352ae`.
Raw result SHA-256:
`018299ffc955d694cedd4c3bd3490aa37823d3080cbeecc914e8a483f588c476`.

The table below aggregates configuration and internal queries (152 total).
All rows use exact FP32 ranking of the selected candidate documents.

| Router | P | Mean overlap | qrels nDCG@10 | Unique candidates | p95 ms |
|---|---:|---:|---:|---:|---:|
| PCA 12x2 | 256 | 0.726 | 0.590 | 64,909 | 95.5 |
| Shared 8x3 | 32 | 0.607 | 0.487 | 58,837 | 88.6 |
| Shared 12x2 | 64 | 0.713 | 0.560 | 76,535 | 109.3 |
| Shared 6x4 | 32 | 0.656 | 0.527 | 96,873 | 140.8 |
| Shared 8x3 | 256 | 0.808 | 0.586 | 164,899 | 224.9 |
| Shared 12x2 | 256 | 0.835 | 0.607 | 175,064 | 241.7 |
| Shared 6x4 | 256 | 0.816 | 0.579 | 224,393 | 309.5 |

Payload is two bytes per ordinal cell ID for these configurations. Projection
model sizes are 13,888 bytes for 8x3, 10,824 bytes for 6x4, and 20,016 bytes
for 12x2, including mean, projection, and thresholds.

## Interpretation

The shared projection clearly improves the corresponding PCA ordinal geometry:
at `P=256`, PCA 8x3 reaches 0.551 overlap while shared 8x3 reaches 0.808;
PCA 6x4 reaches 0.568 while shared 6x4 reaches 0.816. However, this gain is
not yet a product win at a matched candidate budget. The corrected PCA 12x2
control reaches 0.726 overlap and 0.590 qrels nDCG with only 64.9k candidates
at `P=256`. Shared 8x3 at a comparable 58.8k candidates reaches 0.607/0.487;
shared 12x2 at 76.5k reaches 0.713/0.560.

The apparent large gain at `P=256` is therefore partly a budget effect: shared
projections open much larger posting pools. It is also important that the
query-specific threshold-cost correction materially strengthens the PCA
control; the old #299 global-bin-width scheduler is not a valid comparison.

This result supports the diagnosis that shared retrieval geometry is useful,
but it does not yet justify replacing corrected PCA12 routing. The next fair
step is to add E5 ranks 11-1000 as hard negatives, select `P` by matched
60-70k unique candidates, and compare qrels nDCG plus tail quality. Learned
thresholds and soft top-P survival loss should wait until that comparison.

## Limitations

- The original PR #176 checkpoint is unavailable; historical Binary12 remains
  a deterministic retraining reference, not a byte-identical replay.
- Negatives are uniform because the current cache stores only top-10 teacher
  IDs; no claim is made about a hard-negative-trained model.
- The projection is linear and trained on 153 queries; no multi-seed or full
  cross-corpus validation has been run.
- Reported p95 includes probing, posting union, and exact candidate scoring;
  it is not an isolated projection-inference benchmark.
- No ITQ/ADC, INT, local K8, or full R4 cascade is applied.
