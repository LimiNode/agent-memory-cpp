# Lexical product-path performance qualification

Date: 2026-11-01  
Branch: `research/lexical-product-path-performance`  
Experiment family: `lexical_product_path_performance_qualification_v1`

## Question

How costly is the current `ProjectionAwareLexicalRetriever` path that scans
the scope and rebuilds an `ExactLexicalIndex` for every query, and what can be
observed from the existing persistent lexical adapters without changing
production retrieval?

## Setup

The CTest harness is
`agent_memory_mdbx_lexical_product_path_qualification_test`. It imports a
bounded synthetic corpus of 48 documents per scope into MDBX, uses the public
`ProjectionAwareLexicalRetriever::retrieve()` API for the rebuild baseline,
and exercises `MdbxPersistentLexicalIndex` and `MdbxSegmentedLexicalIndex`
through their existing `upsert()` publication paths. The baseline has three
warm-ups and 12 timed invocations in one process. Persistent adapter timings
are one build sample: these adapters do not expose a product query API, and
their read snapshots pin a transaction. The receipt therefore uses
`PERFORMANCE_INCONCLUSIVE` and makes no speedup claim.

Command:

```text
ctest --test-dir <build> -R agent_memory_mdbx_lexical_product_path_qualification_test --output-on-failure
```

The CTest receipt is written to the build directory as
`lexical-product-path-qualification.json`.

## Directional result

| Path | Samples | Median (ms) | p95 (ms) | Semantics |
| --- | ---: | ---: | ---: | --- |
| `ProjectionAwareLexicalRetriever::retrieve` | 12 | 3.08 | 3.17 | Full public query path, including scope scan, projection reads, tokenization, and per-query index rebuild |
| Persistent flat `upsert` build | 1 | 41.32 | 41.32 | One publication build; not a query latency |
| Segmented `upsert` build | 1 | 501.00 | 501.00 | One publication build; not a query latency |

The public retriever checks pass freshness and scope isolation. Both persistent
adapters complete their bounded publication build, and the harness lifecycle
smoke passes. Adapter-specific close/reopen timing is intentionally not claimed
here; existing persistent and segmented conformance tests cover reopen
durability.

## Interpretation and limits

- This is a local directional snapshot, not production performance evidence.
- `sample_count` means timed invocations in one process; it is not a count of
  independent requests or independent runs.
- p95 is descriptive for this small sample distribution. It does not establish
  a stable p99 or a capacity SLO.
- The persistent adapters currently expose durable state and snapshot views,
  not an equivalent product query API. Any latency comparison against the
  public retriever is therefore `PERFORMANCE_INCONCLUSIVE`.
- The corpus is synthetic and small. The measurement identifies the current
  rebuild bottleneck and confirms the existing adapter seams; it does not
  justify changing production defaults or MDBX schema.

## Follow-up

After #576 is integrated, rerun this harness on the pinned R3A corpus and
retain separate measurements for query execution, index publication, snapshot
materialization, and reopen. A persistent adapter should only be wired into
the product path after an equivalent query contract and freshness/scope proof
exist.
