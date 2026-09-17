# Native full-corpus codec materialization gate (2026-09-15)

This gate is the first executable step of the post-cleanup native wave.  It
materializes production-shaped, document-ID-addressed tables for the four
decision arms in `2026-09-15-next-native-research-plan.md`:

All worktree copies and temporary payloads for this wave live under
`E:\\_repoz\\agent-memory-workspaces\\native-full-corpus-gate`; the main
checkout is intentionally left untouched because it contains user storage
changes.  Future agents must continue this wave in that worktree (or a named
successor under the same directory), not create another project directly under
`E:\\_repoz`.

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
each payload's size and SHA, the source manifest, training count, explicit
`quantile_method: linear`, NumPy version, and runner SHA.  Native scoring must
consume these files directly and report top-10 IDs,
qrels nDCG, teacher overlap, logical bytes, unique 4-KiB pages, and resident
versus memory-pressure latency.  A successful Python materialization alone is
not a production result.

## Current execution state

The repository contains the fail-closed materializer, independent audit, a
Python reference candidate runner, and a native candidate-gate mode in
`native-full-corpus-codec-benchmark.cpp`.  No authoritative native candidate
replay is claimed until the frozen candidate payload is available and its
output is compared with the Python reference.  Run it only with the
frozen DE-1M manifest and source payloads that match the hashes in the prior
provenance receipts.  If any source is absent or has a different SHA, leave
the receipt pending rather than substituting a regenerated or subset fixture.

The existing 463,258-document finalist remains a query-derived subset and is
not a substitute for this gate.

The independent audit (`audit-native-full-corpus-codecs.py`) now recomputes
every row in chunks and compares THQ4, both INT8 code streams, and both scale
streams byte-for-byte.  The 2026-09-16 receipt records
`rows_audited: 1000000`, the manifest/materializer/receipt/source SHA values,
and every output payload SHA.  The power-.625 sidecar stores transformed-domain
scales; native scoring materializes `pow(scale, 1.6)` once before the query loop.
This proves materialization/parity correctness, not serving quality or latency.
The current materialization receipt SHA is
`113dae55124e43020b1c91bb70421c53885299791a9d580d5352e4c921d9890d`; the
independent audit receipt SHA is
`2f19699b79dff48d68cf658f5eeb86f030b7c8713e89d833c91a48142b4a4a71`.

## Initial native smoke

The first eight DE-1M queries were replayed against the materialized tables by
`native-full-corpus-codec-benchmark.cpp` after the corrective LUT rewrite.  The
full-corpus scalar control averaged `396.15 ms/query` for direct linear INT8,
`576.48 ms/query` for direct power-.625, `90.67 ms/query` for the THQ4 byte-LUT
scan plus top-128 linear rerank, and `89.32 ms/query` for the corresponding
power-.625 cascade.  Top-1 IDs matched the corresponding direct arm for all
eight queries.  An independent coordinate-reference replay found retained-set
top-128 parity for all eight queries; one internal order difference is recorded
because the native byte-LUT and coordinate reference group float32 additions
differ.  The receipt is
`2026-09-16-native-full-corpus-thq-top128-parity.json`.  The old `4.82 s` number is retained only as a
historical pre-LUT measurement and must not be used as an intrinsic THQ cost.

This remains a **full-corpus scalar scan control**, not Gate 1: both arms scan
all 1M documents.  The R4 candidate-stream four-arm runner is separate, is
currently a Python reference implementation rather than a native-kernel
benchmark, and must consume the same frozen approximately 5k-document stream for all four
arms.  Page counts in the corrected control use the full THQ scan namespace plus
distinct code-file and scale-file namespaces; they do not pretend that the two
files are interleaved 388-byte records.  The THQ scan therefore accounts for
all `23,438` 4-KiB pages of the 96-byte-per-document file before shortlisted
INT8 rerank pages are added.

When the canonical candidate payload is available, create its little-endian
offset sidecar with `materialize-native-candidate-offsets.py` and invoke the
native executable with `--candidate-gate`. The mode rejects counts outside
`5000..5099`, duplicate IDs, malformed offsets, and out-of-range document IDs;
its output must still be compared with the Python reference before the receipt
can leave `PENDING`.

This is deliberately recorded as a smoke result only: it has no qrels, uses
eight queries, and does not include blocked/AVX2 THQ kernels, cold/warm OS or
MDBX measurements, or R4 candidate-local quality.  It establishes that the
per-query LUT is necessary for a meaningful scalar reference; it does not by
itself select the production cascade.
