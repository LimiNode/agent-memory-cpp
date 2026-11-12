# Pre-release ANN/R4 compatibility audit

Status: `COMPATIBILITY NOT PROVEN / IMPLEMENTATION PENDING`

Date: 2026-11-12

Audit branch: `research/pre-release-ann-r4-audit`

Audit base: `main@795588b0e3f170deb279431693772042372dbf18` (post-merge #608)

This note answers one release question:

> Can a database created by the technical preview acquire an R4-compatible
> derived ANN index in a later library version without changing canonical
> resource identities, `KnowledgeUnit` revisions, or source provenance?

The answer is a qualified **not yet proven**. The repository has the canonical
records needed to make such an upgrade possible, but it does not yet expose one
production contract that binds an ANN candidate to those records and to an
atomically published index generation. This audit therefore does not add an ANN
backend, change a DBI, or claim that R4 is the default route.

## Evidence and scope

The audit reconciled the following repository-owned documents and current
interfaces at the audit base:

- [`index-lifecycle-roadmap.md`](../index-lifecycle-roadmap.md)
- [`product-ann-readiness-audit.md`](../product-ann-readiness-audit.md)
- [`derived-representations-and-codecs.md`](../derived-representations-and-codecs.md)
- [`resource-reindexing.md`](../resource-reindexing.md)
- [`r4-routing-evidence-ledger.md`](r4-routing-evidence-ledger.md)
- [`native finalist wave`](2026-10-03-native-flat-and-r4-finalist-wave.md)
- [`fresh packed screen`](2026-10-03-fresh-packed-quality-and-layouts.md)
- `src/agent_memory/index/IVectorIndex.hpp` and `VectorIndex.hpp`
- `src/agent_memory/index/IBinarySignatureIndex.hpp` and its record types
- `KnowledgeUnitEnvelope`, `ResourceRevision`, and `ResourceManifest`

The post-merge checks for the audit base were observed, not inferred:

| Check | Run | Result |
| --- | --- | --- |
| CI | [43133359590](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43133359590) | success; documentation and CI gate passed; research lanes skipped by path classification |
| Packaging | [43133359672](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43133359672) | success on Ubuntu, Windows, macOS and installed MDBX consumer |

The skipped research lanes are not counted as ANN evidence.

## Current contract and compatibility gap

The canonical side already has useful identity and revision fields:

- `ResourceId`, `ResourceRevision::generation`, and optional immutable
  `ResourceBodyDigest` identify source revisions;
- `KnowledgeUnitId` is a monotonic local identity and the envelope carries a
  separate mutable `revision`, lifecycle state, scope, and source summaries;
- `Original` projections and resource manifests provide a rebuildable derived
  view boundary;
- the lifecycle roadmap requires old-generation reads, fail-closed stale
  filtering, deterministic rebuild, reopen, and interrupted-build handling.

The vector and binary index contracts are intentionally narrower:

- `VectorRecord` and `VectorSearchResult` are keyed by `ChunkId` and carry
  embedding/metadata, but no `KnowledgeUnitId`, source revision, projection
  generation, or index generation;
- `IBinarySignatureIndex` has the same `ChunkId` identity boundary and validates
  binary signature identity/width, but not canonical source freshness;
- `ExactVectorIndex` and `FlatBinarySignatureIndex` are in-memory exact or
  candidate baselines. They have no durable reopen, publication, rollback, or
  active-generation protocol;
- no production `IndexSpaceDescriptor` implementation was found that binds
  model/artifact fingerprint, dimension, metric or code identity, scope and
  generation to a persisted index;
- `ResourceIndexer` is explicitly a prototype composition of independently
  owned document, manifest, embedder, and vector interfaces. Its rollback is
  in-process compensation, not a cross-store crash transaction.

Consequently, a numeric or positional mapping from `ChunkId` to
`KnowledgeUnitId` would be an unsafe compatibility claim. The mapping must be
persisted and checked as evidence, not inferred by an adapter.

The normative owners above are unchanged. The current coverage inventory calls
codec-neutral derived representations a contract/research split, not a shipped
compact-vector adapter. This report is a dated audit, not another live status
registry. At final GitHub refresh, #607 was still Draft/open at
`24cd25e29a69eba441f319774ba36d9ca3224a1b`; no #607 source or branch was
changed. The release verdict must be refreshed after its integration. Its raw
source path can supply canonical inputs, but cannot alone prove ANN activation.

## Release classification

| Question | Classification | Reason |
| --- | --- | --- |
| Has the preview format proved that a later derived ANN generation needs no canonical migration? | `RELEASE_BLOCKER` for this task's pre-release gate | The canonical records are versioned, but the required upgrade/reopen counterexample fixture does not exist. |
| Must the real R4 algorithm or default-backend selection ship now? | `SAFE_TO_DEFER` | An exact derived backend can prove the lifecycle first; R4 is neither required nor approved as a default. |
| Can a future index topology be rebuilt from retained embeddings? | `NEEDS_EVIDENCE` | The repository does not establish embedding retention, model-space compatibility, or a persisted index-space descriptor. |
| Can embeddings be recomputed from retained source? | `NEEDS_EVIDENCE` | This is a model/revision operation, not topology rebuild; source and encoder provenance must be retained and matched. |
| Can an interrupted rebuild recover safely? | `NEEDS_EVIDENCE` | The lifecycle roadmap requires it, but no shared ANN implementation executes the check. |
| Can an active generation be rolled back after reopen? | `NEEDS_EVIDENCE` | No durable ANN publication pointer and rollback receipt are present. |
| Can the 2026 R4 quality numbers be used as a production selection? | `SAFE_TO_DEFER` | They remain research evidence with separate route, scorer, and persistence scopes. |

The practical decision is **HOLD for the requested compatibility gate**, not a
release approval. R4 routing may be deferred, but calling the format ready for
future derived-index upgrades requires the conformance gate below. Merely
labelling ANN non-default does not close that pre-release requirement.

## Rebuild inputs are separate operations

| Operation | Required retained input | Model requirement | If unavailable |
| --- | --- | --- | --- |
| Topology rebuild | exact embeddings or an accepted explicitly described vector representation, with revision bindings | same embedding space; topology/builder may change | `RebuildUnavailable` if the target topology requires absent inputs |
| Re-encode vectors | original FP32 or a lossless vector representation | no embedder call; validate dimension, metric and normalization | lossy codes do not restore exact vectors; reject exact migration |
| Recompute embeddings | exact input text or byte-equivalent recipe replay, source revisions and provenance | same immutable model/preprocessing for same-space rebuild; a changed model creates a new space | explicit unavailable if text/model cannot be reproduced |

The current `Embedding` value is not evidence that vectors are durably retained.
Keeping raw canonical text also does not prove that the original model or
preprocessing recipe remains available. Codec, topology, embedding
materialization and canonical unit revisions must remain independent.

## Reproducible upgrade matrix (proposed, not executed)

Freeze a fixture with two resources in distinct scopes, equal-score vectors,
two revisions of one source, and an admitted/denied hard-filter pair. Bind it
to an old library SHA, new library SHA, build/profile options, model and input
digests. Use separate processes for old-version creation and new-version open.
Capture canonical logical records and source bytes, not the entire mutable
MDBX file: derived pages and environment metadata legitimately change.

| Case | Required observable result |
| --- | --- |
| Descriptor mismatch | wrong model, scope, projection, metric, dimension, codec artifact or generation fails closed at open/query |
| No initial ANN | old-version canonical fixture opens after upgrade; no ANN is implicitly required or created |
| First generation | exact dummy backend builds derived rows; activation admits only bound current units |
| Same-index rebuild | new generation yields the same deterministic order without canonical writes |
| Alternative topology/codec | independent descriptor/generation; old reader stays on its pinned complete generation |
| Interrupted build | kill child builder before activation; reopen sees the prior active generation or no ANN |
| Failed activation | corrupt manifest or omit one binding; activation fails and active pointer is unchanged |
| Recovery/reopen | fresh process validates descriptor, complete bindings and selected generation |
| Source update | old candidate cannot hydrate the new revision; rebuilt candidate binds the updated unit/projection |
| Delete/tombstone | deleted/superseded unit is not admitted, including after restart |
| Scope/hard filter | denied nearer records cannot displace the allowed result by post-top-K filtering |
| Rollback | explicit rollback after reopen selects a retained complete compatible generation, never resurrecting stale hits |
| Missing input/model | typed unavailable; no reinterpretation of lossy codes or silent cross-model mixing |

For each build/activation/reopen/rollback phase, compare all canonical records
to the snapshot immediately before that phase. Source update and deletion have
their own explicitly expected canonical delta; index work must add no further
delta. Require non-zero test exit on any identity, revision, source digest,
binding or admission mismatch.

The snapshot includes resource IDs/generations/body digests, unit IDs/revisions,
source summaries and source bytes. Also validate projection and index bindings
separately as derived state. Reuse the fixture with the exact baseline first,
then binary and future ANN adapters; do not require every backend in the first
bounded implementation.

## Smallest honest lifecycle gate

This is a production-facing seam to implement in a later, focused PR. It is
deliberately smaller than a general ANN framework.

```text
CandidateRef {
    KnowledgeUnitId unit_id;
    ScopeId scope_id;
    ProjectionKind projection;
    uint64_t unit_revision;
    uint64_t projection_generation;
    uint64_t index_generation;
    IndexSpaceDescriptor space;
    backend-local locator;
}
```

`IndexSpaceDescriptor` must identify at least the embedding/model or binary
codec fingerprint, dimension or bit width, metric, projection descriptor,
scope, configuration digest, source frontier, schema/builder version, and
generation. A generation manifest may store the descriptor
once, but every candidate must resolve to exactly one descriptor and active
generation.

The minimal lifecycle is:

1. read the active canonical resource/unit/projection frontier;
2. build a new derived generation beside the active one from retained,
   revision-checked inputs;
3. write a complete generation manifest and candidate-to-unit bindings;
4. validate descriptor, scope, source revisions, counts, and digests;
5. atomically publish one active-generation pointer;
6. keep the prior generation readable until publication succeeds;
7. reopen and resolve the same active pointer and bindings;
8. on interruption or failed validation, leave the old generation active;
9. support an explicit rollback to the prior complete generation.

A dummy/exact scoring backend is acceptable; a test-owned substitute for the
persisted lifecycle is not. The existing exact backend has no serialize/open
seam. Adding a sidecar and switching an in-memory pointer solely inside a test
would implement the alleged contract in the test itself, not test the library.
The missing durable descriptor, generation binding and activation ownership are
public/persistence choices, so no executable compatibility spike is claimed in
this documentation PR. The next narrow implementation must clarify those owner
contracts and add one durable adapter plus the matrix; it must not add a broad
index manager or change canonical schemas speculatively.

## R4 evidence reconciliation

R4 remains a promising candidate generator, not a release-ready backend:

| Evidence | Interpretation | Boundary |
| --- | --- | --- |
| [Historical native R4, 76 queries](2026-08-31-neuroute-r4-native-end-to-end.md) | baseline/strict/AVX2 p95 `63.008 / 17.452 / 10.423 ms`; nDCG@10 `0.650652` for each | three seeds, 1 warmup + 3 measured passes; historical frozen pipeline, AVX2 sensitivity treatment; one Windows host, not the later 152-query serving shell |
| Three-seed K1→K16 frontier, `A=16384` | approximately `0.9974` mean teacher recall | quality frontier over the 152-query controlled workload; not full latency or product recall |
| Native matched R4 candidate stream | approximately `0.7927 / 0.9357 / 1.151 ms` p50/p95/p99 for one INT8 mode | candidate-stream serving scope; excludes route generation and cold start |
| Fresh 305-query prototype replay | route recall `0.8126`, nDCG@10 `0.6456`, MRR `0.6865` | `PROTOTYPE_EXECUTED_CANONICAL_PENDING`; exact Modern R4 route/model/posting/THQ manifests are missing |
| 152-query direct-flat / Prototype-IVF INT8 controls | p50/p95/p99 `407.166 / 417.831 / 428.745 ms` flat versus `0.7485 / 0.868 / 1.214 ms` routed | same cited native finalist wave and 1+5 timing contract; prepared routes, not generator-inclusive routing speed |
| 305-query fresh flat oracle / Prototype-IVF / R4-shaped surrogate | exact FP32 nDCG@10 `0.672395`; TQ1+PQ8 `0.668428 / 0.656407` for the two routed controls | route loss dominates; controls are not a canonical Modern R4 comparison or production winner claim |

The historical 152-query stream and the fresh prototype receipt must not be
collapsed into one canonical R4 result. A future canonical replay needs the
route/model manifest, frozen postings/candidate stream, THQ representation,
source hashes, and complete-cascade timing scope.

Numbers above are transcribed from the linked source-bound notes/receipts;
raw experiments were not rerun in this audit. The `39.3 ms` historical native
total in the [older synthesis](2026-09-06-r4-flat-product-synthesis.md) excludes
external generator cost and belongs to another pipeline. It cannot be compared
as an end-to-end improvement with the `0.9357 ms` prepared-stream p95. The
[fresh receipt](2026-10-03-modern-r4-fresh-replay.result.json) explicitly lists
missing canonical inputs. R4 merits a later opt-in adapter evaluation, retaining
flat and Prototype-IVF controls; none of these rows authorizes default selection.

## Follow-up order

1. Keep the current preview ANN-neutral and preserve canonical source and unit
   revisions as the only authority.
2. Add the narrow generation descriptor and durable candidate binding seam.
3. Add the conformance fixture and negative cases above against one exact
   derived backend before implementing R4 routing.
4. Add reopen/interruption/rollback evidence and a retained-embedding policy.
5. Re-run a canonical fresh R4 replay with complete manifests.
6. Only then decide whether an R4 adapter can be enabled as an optional
   technical-preview profile or defaulted in a later release.

This audit changes no public API and does not promote any research result to a
production capability.

## Verification boundary

Evidence rung: repository contract/implementation audit only. No new benchmark,
old-to-new library execution, durable ANN conformance, or latency result was
produced. The upgrade matrix is proposed, not passed. Local verification checks
the changed Markdown links, whitespace, and consistency of the inventory
snapshot; a documentation CI pass cannot close the compatibility blocker.
