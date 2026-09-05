# Cell-aware ordinal router

Date: 2026-09-05. Follow-up to the shared supervised ordinal projection
study.

## Question

Continuous metric training and E5 document hard negatives did not optimize the
actual router operation. This experiment trains against the exact
query-specific threshold-crossing cost used by the inference scheduler:

```text
query -> ranked cells -> opened postings
```

The first variant keeps the PCA12 document partition frozen and trains a
query-only head. The remaining variants use alternating shared-projection
rounds: mine cells, train the projection with frozen cell assignments, rebuild
document cells and quantile thresholds, and repeat.

## Protocol

Runner:

```text
tools/agent-memory-bench/run-cell-aware-ordinal-router.py
```

Positive cells are all cells containing exact E5 top-10 documents. Routing hard
negatives are the lowest-cost cells that are not positive cells, including
cells that consume probe budget but contain no teacher documents. The loss is
pairwise softplus on positive-versus-negative cell cost. Shared rounds add an
orthogonality penalty and a small trust-region anchor to the previous
projection. Thresholds remain train-document quantiles and replication is
fixed at `R=1`.

The evaluator reports exact teacher overlap, qrels nDCG@10, candidate count,
p95 (probing + posting union + exact candidate scoring), payload/model bytes,
and teacher-cell rank diagnostics.

## DE-1M result

Cache manifest SHA-256:
`25f10151c60461edbfd0ac52e66caf5777834c80ba4637a8986e03de14f352ae`.
Raw result SHA-256:
`564c194e64971f136d735ba98df372b71c033f86333c9c58cbb827286c0f33c4`.

The table aggregates configuration and internal queries (152 total), with
`K=256` exact document selection:

| Router | P | Overlap | qrels nDCG@10 | Unique candidates | p95 ms |
|---|---:|---:|---:|---:|---:|
| Corrected PCA12 baseline | 256 | 0.726 | 0.590 | 64,909 | 96.6 |
| Frozen PCA12 + cell-ranking query head | 256 | 0.189 | 0.172 | 62,021 | 91.7 |
| Shared 12x2 alternating | 128 | 0.689 | 0.546 | 77,877 | 133.9 |
| Shared 12x2 alternating | 256 | 0.780 | 0.600 | 124,770 | 228.3 |
| Shared 8x3 alternating | 256 | 0.628 | 0.524 | 71,250 | 116.3 |
| Shared 6x4 alternating | 256 | 0.669 | 0.549 | 74,136 | 102.6 |

Payload is two bytes per cell ID. Model sizes are 20,016 bytes for 12x2,
13,888 bytes for 8x3, and 10,824 bytes for 6x4.

Teacher-cell rank diagnostics (all top-10 teacher documents) were:

| Router | Mean rank | p90 rank | Survival <=256 |
|---|---:|---:|---:|
| Corrected PCA12 baseline | 246 | 702 | 0.726 |
| Frozen query head | 1,264 | 2,824 | 0.190 |
| Shared 12x2 | 245 | 754 | 0.780 |
| Shared 8x3 | 552 | 1,739 | 0.629 |
| Shared 6x4 | 362 | 1,055 | 0.669 |

## Interpretation

The frozen single-anchor cell-ranking head is a negative result: optimizing
positive-versus-negative cell costs can move the query away from the useful
PCA cells when one query has many multimodal positive cells. It does not beat
the untrained corrected PCA query coordinates.

Alternating shared projection improves the low-dimensional PCA geometries, but
not at a matched candidate budget. Shared 12x2 reaches 0.689 at 77.9k
candidates and 0.780 at 124.8k; Shared 8x3 and 6x4 reach only 0.628/0.669 at
about 71–74k. Corrected PCA12 remains the strongest 60–70k control at
0.726/0.590.

The rank diagnostics agree with the candidate results and expose the same
trade-off directly: shared 12x2 reduces teacher-cell rank substantially, but
the improvement requires opening many more cells. Therefore the cell-aware
objective is not yet a product solution, and a multi-anchor query model is a
more plausible next test for the multimodal target than further single-anchor
loss tuning.

## Limitations

- One deterministic seed and 153 training queries; no cross-corpus validation.
- The frozen query-head model is retained only during the run; no checkpoint
  is committed.
- Shared alternating rounds use hard cell assignments between rounds, so the
  objective is piecewise rather than globally differentiable.
- No replication, learned thresholds, soft top-P loss, ITQ/ADC, local K8, or
  full R4 cascade is applied.
