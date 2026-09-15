# Native full-corpus codec materialization gate (2026-09-15)

This gate is the first executable step of the post-cleanup native wave.  It
materializes production-shaped, document-ID-addressed tables for the four
decision arms in `2026-09-15-next-native-research-plan.md`:

* packed THQ4 ordinal: 96 logical bytes/document;
* direct INT8 linear: 388 logical bytes/document;
* direct INT8 power-.625: 388 logical bytes/document;
* THQ4 is consumed only as the prefilter in the cascade arms.

The materializer is `tools/agent-memory-bench/materialize-native-full-corpus-codecs.py`.
It is deliberately fail-closed: the manifest must describe exactly 1,000,000
384-dimensional FP32 rows, and every referenced source (document vectors,
queries, teacher IDs, and qrels when present) is checked for byte size and
SHA-256 before any output is written.  Row `i` is document `i`; no subset ID
sidecar is accepted for this gate.

The output payloads are intentionally not committed to Git.  The receipt binds
each payload's size and SHA, the source manifest, training count, and runner
SHA.  Native scoring must consume these files directly and report top-10 IDs,
qrels nDCG, teacher overlap, logical bytes, unique 4-KiB pages, and resident
versus memory-pressure latency.  A successful Python materialization alone is
not a production result.

## Current execution state

The repository contains the fail-closed runner and protocol, but no new
authoritative native replay is claimed by this commit.  Run it only with the
frozen DE-1M manifest and source payloads that match the hashes in the prior
provenance receipts.  If any source is absent or has a different SHA, leave
the receipt pending rather than substituting a regenerated or subset fixture.

The existing 463,258-document finalist remains a query-derived subset and is
not a substitute for this gate.

## Initial native smoke

The first eight DE-1M queries were replayed against the materialized tables by
`native-full-corpus-codec-benchmark.cpp`.  The direct linear INT8 arm averaged
`412.906 ms/query`; direct power-.625 averaged `780.821 ms/query`.  The scalar
THQ4 interval-squared full scan followed by top-128 INT8 rerank averaged
`4819.2 ms/query` (linear) and `4813.86 ms/query` (power-.625).  Top-1 IDs
matched the corresponding direct arm for all eight queries.

This is deliberately recorded as a smoke result only: it has no qrels, uses
eight queries, and does not include the blocked/AVX2 THQ kernel or OS/MDBX page
measurements.  It does establish the immediate optimization gate: a naive
scalar full-corpus THQ scan is roughly an order of magnitude slower than the
direct INT8 scan and cannot be treated as the production cascade.
