# Segmented lexical persistence benchmark

Date: 2026-10-25
PR context: PR-D stacked on PR-C (`feat/lexical-segmented-benchmark`)
Question: does the segmented MDBX lexical adapter have a useful persistence and
reopen profile against the dependency-free oracle and the existing `flat-v2`
adapter?

## Setup

The benchmark is built as `agent_memory_segmented_lexical_benchmark` when both
`AGENT_MEMORY_BUILD_BENCHMARKS` and `AGENT_MEMORY_ENABLE_MDBX` are enabled. It
prints TSV rows for four deterministic token workloads:

- `unique+anchor`: one per-unit token plus a shared anchor;
- `shared-hot`: a shared hot token with repeated occurrences plus the anchor;
- `zipf-like`: a deterministic 50/25/13/7/5 bucket distribution;
- `mixed-utf8`: UTF-8, code-like, and anchor tokens.

Each durable run creates a fresh MDBX data file and lock file. The TSV separates
environment creation, adapter initialization, writes, full validated snapshot
read, close, reopen/adapter initialization, and full validated snapshot read
after reopen. The dependency-free `FlatLexicalIndex` row is an in-process
reference and therefore has no durable or reopen timing. Durable rows optionally
carry benchmark-only telemetry from exactly two observations: one after the
write snapshot and one after reopen. The reported live-row and key/value-byte
counters come from the final (post-reopen) observation; the scan count is `0`
when telemetry is disabled and `2` when it is enabled. These counters are
logical observations, not adapter-internal events and do not represent physical
disk writes.

Example command:

```text
agent_memory_segmented_lexical_benchmark.exe 128 5 <temporary-base-path> 1
```

The optional final flag enables the post-write diagnostic scans (`1`, the
default). Use `0` for a matched telemetry-disabled control run; correctness
and timed write/read phases are unchanged.

## Directional smoke result

MinGW Release, bundled MDBX dependencies, 128 units, two repeats. Values below
are representative rows from the first repeat; the command emits every repeat
as TSV. `write_ms` is isolated from adapter initialization, snapshot validation,
and diagnostic scans. `snapshot_ms` measures only the validated snapshot read,
`write_diagnostic_ms` measures the separate post-write scan, and
`reopen_diagnostic_ms` measures the separate post-reopen scan. The read columns
are full snapshot validation, not selective query latency.

| workload | backend | write ms | snapshot ms | reopen snapshot ms | database bytes |
| --- | --- | ---: | ---: | ---: | ---: |
| unique+anchor | flat-v2 | 141.3 | 0.84 | 0.87 | 65,536 |
| unique+anchor | segmented-v1 | 1,776.9 | 2.96 | 3.77 | 16,777,216 |
| shared-hot | flat-v2 | 111.1 | 0.92 | 0.65 | 65,536 |
| shared-hot | segmented-v1 | 557.8 | 1.44 | 1.60 | 16,777,216 |
| zipf-like | flat-v2 | 118.7 | 0.55 | 0.61 | 65,536 |
| zipf-like | segmented-v1 | 700.4 | 1.59 | 2.27 | 16,777,216 |
| mixed-utf8 | flat-v2 | 135.1 | 0.82 | 0.96 | 65,536 |
| mixed-utf8 | segmented-v1 | 1,895.2 | 3.25 | 3.46 | 16,777,216 |

The adapter preserved 128 active units and the expected token totals for every
row. This smoke run is evidence that the lifecycle and reopen path are wired,
not a scalability claim. In this profile the segmented adapter is slower and
uses a larger MDBX map than `flat-v2`; the implementation still performs one
durable transaction per unit and is intentionally an experimental storage
slice. A follow-up should measure larger corpora, transaction batching, map
growth, and physical I/O before any layout or default change is proposed.

### Timing-integrity control

The earlier smoke receipt reported a median `write_ms` of 2,229.45 ms with
diagnostics disabled and 2,226.52 ms with diagnostics enabled. The corrective
pass supersedes that directional sample; its scans are measured in separate
phases and do not enter the reported write timing. The
figures are directional local evidence, not a hardware-normalized performance
claim.

The corrective pass measured `128 units × 5 repeats` in the same MinGW Release
build. Segmented `write_ms` medians (diagnostics off/on) were 2,361.3/2,322.3
ms for `unique+anchor`, 581.9/597.0 ms for `shared-hot`, 782.5/768.7 ms for
`zipf-like`, and 2,675.7/2,682.0 ms for `mixed-utf8`. Snapshot medians stayed
separate (off/on: 2.563/2.256, 1.224/1.236, 1.601/1.576, and 2.674/2.846 ms
respectively); diagnostic scans were measured separately. Telemetry-enabled
rows report exactly two scans and disabled rows report none. A 512-unit × 5
sweep exceeded the local runtime budget and remains a follow-up measurement.

## Limitations and next checks

- The checked-in smoke table uses two repeats; comparative decisions require
  at least five repeats with median and spread.
- The workloads are synthetic and use short ASCII tokens, not technical UTF-8
  documents.
- `db_bytes` is the final MDBX file size, not a write-amplification counter.
- `live_rows_observed`, `live_value_bytes_observed`, and
  `observed_live_key_value_bytes` describe the final logical MDBX state observed
  by the benchmark, not storage-engine page I/O.
- The benchmark does not yet expose MDBX live-page or physical-write counters;
  file geometry must not be interpreted as write amplification.
- The benchmark does not claim cross-store atomicity or production throughput.
- The next performance experiment should compare bounded transactional batches
  while keeping the lexical-only publication and pinned-reader contracts fixed.
