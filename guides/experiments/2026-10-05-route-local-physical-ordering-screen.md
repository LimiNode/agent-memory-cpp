# Route-local physical ordering screen (2026-10-05)

## Context and hypothesis

This bounded research screen tests whether physical payload order aligned with
the primary IVF cell assignment reduces the read amplification observed for the
canonical `DocumentId` order. Logical IDs are unchanged; the treatment is only
a deterministic physical permutation:

```text
(primary IVF cell, numeric DocumentId)
```

The screen is research-only. It does not change the production Row-KV layout,
stable IDs, routing outputs, codec selection, or quality claims.

## Frozen setup

- Corpus: 1,000,000 DE-1M `multilingual-e5-small` document vectors, 384D.
- Primary assignment: spherical Faiss k-means, `nlist=256`, `seed=20261002`,
  20 iterations, 25,000 training rows.
- Existing Prototype-IVF route manifest: `nlist=256`, 25,000 training rows;
  regenerated centroid SHA-256 matched the manifest route-model SHA-256
  (`8e951e0a55f5bfb805e0b63f301cfbeece0a361086972da0484048ad3106319d`).
- Physical segment size: 4,096 rows.
- Workloads: 305 fresh queries, `route5000` and `top128`, for both
  Prototype-IVF and Modern-R4 fixtures.
- Payloads: LSQ32 (36 B), TQ1 (52 B), TQ1+PQ8 (64 B), and INT8 (392 B).
- Runner: `tools/agent-memory-bench/run-route-local-ordering-screen.py`.
- Auditor: `tools/agent-memory-bench/audit-route-local-ordering-screen.py`.

The compact receipt was generated at:

```text
tmp/route-local-ordering-screen.result.json
receipt SHA-256: 76cf5e890c6f6842b705f310359b9cc312df85dbb6bfa914974de21b1fa90fdc
permutation SHA-256: e086e1b2c581bcb67e14b14c866be95724d1b91abbf94eedb3c0060ad07ba6b6
```

The assignment and permutation arrays are generated evidence in ignored
`tmp/` files. The auditor verifies both hashes and permutation bijectivity.

## Observed locality result

The canonical order touched essentially the whole 4,096-row segment space for
`route5000`. Route-local ordering reduced touched segments substantially while
preserving exactly the same candidate IDs.

| Workload | Canonical touched p50/p95/p99 | Route-local touched p50/p95/p99 |
|---|---:|---:|
| Prototype-IVF route5000 | 245 / 245 / 245 | 57 / 62 / 64 |
| Modern-R4 route5000 | 245 / 245 / 245 | 142 / 181 / 196 |
| Prototype-IVF top128 | 89 / 99 / 102 | 24 / 35 / 39 |
| Modern-R4 top128 | 89 / 100 / 103 | 26 / 41 / 46 |

For TQ1+PQ8 (64 B/doc), the corresponding median fetched bytes changed from
64,000,000 to 14,716,928 for Prototype-IVF `route5000`, and from 64,000,000
to 36,999,168 for Modern-R4 `route5000`. These values account for the short
final physical segment exactly rather than modeling every segment as full.
The receipt contains equivalent
per-codec byte and amplification metrics for all four payload widths.

## Compression result

The physical permutation did not create a meaningful Zstd gain in this fixture:

| Codec | Canonical saving | Route-local saving |
|---|---:|---:|
| LSQ32 | -0.0611% | -0.0611% |
| TQ1 | -0.0605% | -0.0611% |
| TQ1+PQ8 | -0.0605% | -0.0417% |
| INT8 | 3.6986% | 3.7364% |

Thus the strong result is locality/read amplification, not compression. The
small INT8 difference is not evidence of a general Zstd benefit.

## Interpretation and limitations

The hypothesis is supported for the measured routed candidate streams: primary
cell-local physical order can materially reduce touched segments and fetched
bytes without renumbering documents. It is not yet a production storage design.

This is one deterministic primary-cell ordering and one segment size. It does
not measure incremental inserts/deletes, rebuild cost, generation publication,
concurrent readers, cache effects, filesystem/device I/O, or quality changes.
The fixture contains four payloads rather than the complete seven-codec serving
matrix. The route-local order is tied to a routing-model generation; changing
that model can require a physical rebuild and a new `DocumentId -> slot` map.
The result also does not establish that multi-cell or distance-within-cell
orders will improve further.

## Next gate

If physical reads become a measured bottleneck, evaluate this permutation in a
storage-only follow-up with real batch reads and generation/recovery semantics.
Keep it separate from production Row-KV implementation until those lifecycle
costs and worst-case update behavior are measured. A useful next comparison is
primary-cell order versus multi-route locality order at 4,096 and larger block
sizes, with identical candidate streams and an explicit rebuild-cost budget.
