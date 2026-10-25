# Segmented lexical persistence benchmark

Date: 2026-10-25
PR context: PR-D stacked on PR-C (`feat/lexical-segmented-benchmark`)
Question: does the segmented MDBX lexical adapter have a useful persistence and
reopen profile against the dependency-free oracle and the existing `flat-v2`
adapter?

## Setup

The benchmark is built as `agent_memory_segmented_lexical_benchmark` when both
`AGENT_MEMORY_BUILD_BENCHMARKS` and `AGENT_MEMORY_ENABLE_MDBX` are enabled. It
prints TSV rows for three deterministic token workloads:

- `unique`: one per-unit token plus a shared anchor;
- `shared`: a shared token with repeated occurrences plus the anchor;
- `zipf`: a small hot vocabulary with a deterministic long-tail pattern.

Each durable run creates a fresh MDBX data file and lock file, writes all units,
reads the anchor postings from a pinned snapshot, closes the first adapter,
reopens the environment, and verifies the active-unit and token totals. The
dependency-free `FlatLexicalIndex` row is an in-process reference and therefore
has no durable or reopen timing.

Example command:

```text
agent_memory_segmented_lexical_benchmark.exe 128 2 <temporary-base-path>
```

## Directional smoke result

MinGW Release, bundled MDBX dependencies, 128 units, two repeats. Values below
are representative rows from the first repeat; the command emits every repeat
as TSV.

| workload | backend | write ms | read ms | reopen ms | database bytes |
| --- | --- | ---: | ---: | ---: | ---: |
| unique | flat-v2 | 139.7 | 0.79 | 15.4 | 65,536 |
| unique | segmented-v1 | 1,902.3 | 2.98 | 13.0 | 16,777,216 |
| shared | flat-v2 | 108.4 | 0.54 | 12.3 | 65,536 |
| shared | segmented-v1 | 568.2 | 1.64 | 14.3 | 16,777,216 |
| zipf | flat-v2 | 120.1 | 0.60 | 14.0 | 65,536 |
| zipf | segmented-v1 | 961.4 | 2.22 | 14.5 | 16,777,216 |

The adapter preserved 128 active units and the expected token totals for every
row. This smoke run is evidence that the lifecycle and reopen path are wired,
not a scalability claim. In this profile the segmented adapter is slower and
uses a larger MDBX map than `flat-v2`; the implementation still performs one
durable transaction per unit and is intentionally an experimental storage
slice. A follow-up should measure larger corpora, transaction batching, map
growth, and physical I/O before any layout or default change is proposed.

## Limitations and next checks

- Two repeats are insufficient for stable timing statistics; report medians and
  spread for comparative decisions.
- The workloads are synthetic and use short ASCII tokens, not technical UTF-8
  documents.
- `db_bytes` is the final MDBX file size, not a write-amplification counter.
- The benchmark does not claim cross-store atomicity or production throughput.
- The next performance experiment should compare bounded transactional batches
  while keeping the lexical-only publication and pinned-reader contracts fixed.
