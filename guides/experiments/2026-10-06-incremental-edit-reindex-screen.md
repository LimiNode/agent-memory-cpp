# Gate B: incremental edit/reindex screen (2026-10-06)

## Scope

This is a research-only correctness gate for the canonical-content roadmap. It
does not implement `ICanonicalContentStore`, an editor API, MDBX body storage,
compression, SQLite, or production indexing. The fixture is explicitly
synthetic and the projections are deterministic surrogates; no quality or
latency claim is made.

The question is whether a local edit can identify a bounded dependency frontier,
reuse unaffected derived records, and converge to the same logical state as a
full rebuild without exposing stale data as current.

## Frozen setup

- Fixture: `tools/agent-memory-bench/fixtures/gate-b-synthetic-fixture.json`.
- Generator/version: `gate-b-synthetic-v1`, seed `20261006`.
- Three documents, 45 ordered stable blocks, three sections per document,
  headings, paragraphs, a code block, and document metadata.
- Chunker profiles: `block_local_v1` and `windowed_v1`.
- Context profiles: `no_context`, `section_local`, `document_global`, and
  `metadata_derived`.
- Recompute policies: `eager` and `deferred`.
- Projection surrogates: lexical, dense, and codec/vector records, all bound to
  explicit content/context digests.
- Fixed mutation matrix: 12 cases covering modify, insert, delete, intra/inter
  section move, heading edit, metadata-only edit, boundary edit, multi-edit,
  first/last block edits, and normalized no-op.

Commands:

```text
python tools/agent-memory-bench/run-incremental-edit-screen.py --self-test
python tools/agent-memory-bench/audit-incremental-edit-screen.py --self-test
python tools/agent-memory-bench/run-incremental-edit-screen.py \
  --fixture tools/agent-memory-bench/fixtures/gate-b-synthetic-fixture.json \
  --output tmp/gate-b-incremental-edit.result.json \
  --compact-output tmp/gate-b-incremental-edit.summary.json
python tools/agent-memory-bench/audit-incremental-edit-screen.py \
  --receipt tmp/gate-b-incremental-edit.result.json
```

The full receipt is generated under ignored `tmp/`; the compact summary is the
machine-readable committed result for this research line:
`guides/experiments/2026-10-06-incremental-edit-reindex-screen.result.json`.
The measured receipt SHA-256 is
`2d51f9b5fb2cc8a149f4b976912c0db0dcc4e6068917d3ca30bf629bde1bf474`; it was
generated with runner source commit
`32285f4bd79c1928e572b2f21959e61073e18769`. Evidence paths in the receipt are
logical/relative names only.

## Result

The replay produced 192 rows (`12 × 2 chunkers × 4 contexts × 2 policies`) and
all 192 rows reached exact logical parity with the independent full-rebuild
oracle. The auditor independently reconstructs the fixture, applies every
mutation, rebuilds all derived state, recomputes reuse/invalidation/frontiers,
and checks the receipt rather than trusting totals.

### Bounded profile

`block_local_v1` proves bounded reuse for local edits. With no additional
context, only the directly affected block segment is invalidated; section-local
context expands the frontier to the affected section; document-global context
expands it to the whole document. Metadata-derived context invalidates all
segments only for a metadata edit, while content edits remain segment-local.

Across the 48 eager rows for this profile/context matrix, the modeled canonical
bytes rewritten were:

| Context profile | Reused segment records | Invalidated/new records | Modeled bytes rewritten |
|---|---:|---:|---:|
| no_context | 170 | 10 | 791 |
| section_local | 125 | 55 | 4,688 |
| document_global | 30 | 150 | 12,267 |
| metadata_derived | 155 | 25 | 2,018 |

These are logical fixture byte counts, not storage write or latency results.

### Honest fallback profile

`windowed_v1` uses overlapping windows whose bounded resynchronization is not
proven by this harness. Every content/structure mutation therefore falls back
explicitly to whole-document rechunk/reindex with reason
`window_boundary_resynchronization_unproven`. Metadata-only and no-op cases do
not claim a content fallback; metadata-derived context still invalidates its
dependent records.

The fallback is a PASS for the research gate. Silent reuse of an uncertain
window is not accepted.

### Deferred recomputation

Deferred rows retain stale records with their old context/projection digests, but
the strict-current surface excludes every retained stale segment. The eventual
recompute frontier is recorded separately and converges to the same full-oracle
state. There were 78 rows with retained stale records; all 78 excluded them from
strict-current results.

## Interpretation

The Gate B hypothesis is supported for the declared synthetic dependency model:
stable block identity plus explicit dependency profiles is sufficient to express
bounded invalidation and safe reuse for block-local derivations. Context
dependencies enlarge invalidation independently of scheduling policy. A windowed
chunker must not be advertised as locally stable until resynchronization is
proven; the fail-closed whole-document fallback is the correct result here.

## Limitations and next checks

- The fixture and lexical/dense/codec projections are deterministic surrogates,
  not production text, embeddings, ANN, or codec implementations.
- Modeled rewritten bytes are logical content bytes only; no MDBX, frame, cache,
  filesystem, transaction, or crash-recovery behavior was measured.
- The runner and auditor are separate implementations, but both encode the same
  frozen fixture contract; a future hardening pass can move shared constants into
  a generated manifest without sharing derivation code.
- Production work still needs canonical body/edit conflict semantics,
  generation publication, random reads, targeted index writes, and a separately
  reviewed windowed resynchronization algorithm.

The Gate B screen is therefore accepted as research evidence only. It does not
authorize production canonical-content or incremental-indexer implementation.
