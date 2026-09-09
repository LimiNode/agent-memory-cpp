# NeuRoute routing-architecture bake-off

Date: 2026-09-05. Follow-up PR after #297.

## Scope

This study puts four routing families under one evaluator:

1. direct document IVF;
2. learned 12/14/16-bit semantic router with replication 1--4;
3. LTHQ/ordinal router;
4. float IVF followed by an exact-document scoring control.

Every route is evaluated both as a routing ceiling (selected documents are
exactly reranked) and with the common document-stage cascade when a downstream
cache is supplied.  Metrics include nDCG@10, top-10 overlap, p05/worst query,
candidate count, payload bytes/document, and p95 route time, split into config
and internal queries.

## Methodological boundaries

The learned and LTHQ lanes in the reference runner use exhaustive code scans.
They are intentionally diagnostic controls for representation and replication,
not a product ANN implementation.  A global scan of all K8 prototypes remains
outside the product path.  The exact-document IVF control reconstructs the
original FP32 dot algebraically inside probed cells; it is not compact
residual K8 storage and does not license a native production route.

## Reproduction

```text
python tools/agent-memory-bench/plan-neuroute-routing-architecture-bakeoff.py
python tools/agent-memory-bench/run-neuroute-routing-architecture-bakeoff.py --self-test
python tools/agent-memory-bench/run-neuroute-routing-architecture-bakeoff.py --input <documents-queries-teacher.npz> --output <routing.json>
```

The NPZ must contain `documents`, `queries`, and `teacher_top10`; optional
`teacher_gains [query, document]` enables qrels nDCG instead of rank-derived
surrogate gains.  No architecture is promoted from overlap alone.  Native
confirmation and production selection require a later frozen-cascade replay.
