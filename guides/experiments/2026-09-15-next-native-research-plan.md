# Next native retrieval research plan (2026-09-15)

The merged #409/#412/#415/#416 wave closes the corrected logical oracle and
the provenance/materialization audit. It does not select a production codec.
The next work must compare complete persistent representations and end-to-end
latency, not another candidate-local Python frontier.

## Gate 1: production-shaped document codec — EXECUTED materialization/audit; candidate native replay PENDING

Materialize a full 1,000,000-document, document-major table with doc ID equal
to row index:

* shared INT8 linear, 388 logical bytes/document;
* shared INT8 power-.625, 388 logical bytes/document;
* packed THQ4 ordinal, 96 logical bytes/document;
* no subset `document_ids` indirection in the authoritative benchmark.

Run four matched arms on the same frozen candidate stream and on a held-out
query split:

```text
A direct INT8 linear
B THQ4 interval-squared -> top128 -> INT8 linear
C direct INT8 power-.625
D THQ4 interval-squared -> top128 -> INT8 power-.625
```

The full-corpus scalar executable is not this gate.  The current
`tools/agent-memory-bench/run-native-full-corpus-candidate-gate.py` is a
fail-closed Python reference runner; it consumes the frozen candidate flat
stream, gathers only those document rows, and keeps the four arms on identical
candidate IDs.  It is not evidence of native-kernel latency.  Its page accounting treats the
THQ, INT8-code, and INT8-scale files as separate namespaces.  The executable
`native-full-corpus-codec-benchmark.cpp` is retained as the independent
full-corpus scalar control for THQ Flow and decoder parity.

Record deterministic `(score, document_id)` ties, top-10 IDs, qrels nDCG,
teacher overlap, paired deltas, p05/minimum quality, p50/p95/p99 latency,
logical bytes touched, unique 4-KiB pages, and resident versus memory-pressure
behavior. Useful-byte reduction must not be interpreted as equivalent cold-page
reduction without this measurement.

As a detached storage control, test lossless zstd compression of the shared
INT8 table with independently decodable blocks (for example, tile-sized
chunks). Measure compression ratio, block decode CPU, random-candidate read
amplification, and cold/warm latency. Compression may reduce disk footprint,
but it is not a substitute for an uncompressed random-access representation
unless the native gate shows an acceptable decode/page trade-off.

## Gate 2: shared K16 representation — STORAGE EXECUTED; native quality/latency PENDING

Replay the real `3 x A8192` route with both representative layouts:

```text
duplicated address-major INT8 representatives
rep_doc_id sidecars -> one shared document-major INT8 table
```

Require address-prefix parity, candidate quality parity, representative and
document-table bytes, cache/TLB/page counters, and native p50/p95/p99 per seed
and for the complete cascade. This gate decides whether one shared INT8 table
can serve both K16 refinement and final reranking. Existing manifest-bound
storage accounting reports `469,127,530 B` for shared document-major INT8 plus
`rep_doc_id` sidecars versus `1,234,461,302 B` for duplicated representative
vectors (`61.9%` less), but that is a footprint result only; native quality,
locality, and latency remain pending as documented in
`2026-09-15-north-star-reset-gates.md`.

## Gate 3: THQ Flow, independent of R4 — REFERENCE EXECUTED; native gate DEFERRED

Evaluate full-corpus direct THQ Flow for packed levels 4/5/6/7/8 with
ordinal-L1, interval-L1, and interval-squared scoring. Levels 5--8 all occupy
144 packed bytes/document in the current ordinal packing. The eight-query
NumPy reference found best teacher overlap `0.9125` for 7-level
interval-squared, with no ordered top-10 parity; this is not enough to promote
standalone THQ Flow. The result is recorded in
`2026-09-16-native-full-corpus-thq-flow-reference.json`. A native multi-level
replay remains conditional on a held-out quality gate and is not inferred from
the reference timings. Retain the 4-bit thermometer representation as a
separate bandwidth/decode control.

## Gate 4: routing work reduction — PLANNED

Measure asymmetric refinement budgets `8192/8192/2048`, `8192/8192/4096`, and
`8192/8192/8192`, including residual teacher coverage and end-to-end quality.
In parallel, benchmark blocked/SoA/AVX2 K16 kernels against the current gather
implementation; do not infer native speed from Python transposes. A candidate
optimization must preserve deterministic address ranking and full-cascade
quality.

## Gate 5: persistent R4 and held-out confirmation — PLANNED

Materialize real `(seed,address) -> posting doc_ids` controls and compare flat
mmap, page-packed, and MDBX layouts using whole-posting overshoot, duplicate
entries, logical/physical pages, and update/rebuild behavior. Finally rerun the
selected codec/router on an untouched query/domain split with frozen thresholds
and qrels. No production architecture is selected until Gates 1--5 report
total persistent footprint and end-to-end native evidence.

## Decision rule

Prefer the smallest total persistent representation that meets the declared
qrels guardrails (mean loss, p05/minimum loss, and worst-query loss) and wins
the native end-to-end latency/page gate. THQ4 remains a prefilter candidate;
direct shared INT8 remains a first-class control and may be the simpler winner.
