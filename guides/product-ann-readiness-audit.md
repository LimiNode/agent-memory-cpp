# Product and ANN readiness audit

Status: reviewable architecture audit, 2026-10-28  
Baseline: `origin/main` at `be3c80c1c635b92b21cee6a37963526d32ea88fd`  
Scope: documentation and release planning only. This note does not activate
`seg-v2`, add an ANN backend, change the coordinator, or change a DBI.

This audit reconciles the normative milestone table with the implementation
that is actually built by the default library and the optional MDBX profile.
It is deliberately evidence-led: a contract or a green unit test is not
counted as a product capability unless the end-to-end path and its reopen or
failure guarantees are present.

## Executive decision

The repository has a useful M0 lexical/document substrate and several durable
research-quality index implementations. It is not yet a technical preview of
the self-growing knowledge-base product described in `product-path.md`.
The smallest credible preview remains the narrow KB-0 path:

```text
UTF-8 resource
  -> canonical document/revision
  -> raw Chunk / Original projection publication
  -> lexical candidate retrieval
  -> revision-aware source hydration
  -> close/reopen and recovery
```

The first five steps are present only in bounded profiles and are not exposed
as one stable host-facing API. The missing work is integration and conformance,
not another retrieval algorithm. `seg-v2` and the other approximate-index
experiments remain derived, opt-in research implementations.

## 1. Verified capability map

The status vocabulary is:

- **Implemented and tested** — a concrete path is built and exercised by a
  focused test; the claim is limited to the tested profile.
- **Implemented but not integrated** — a backend or contract is present, but it
  is not part of the product path or lacks a shared lifecycle boundary.
- **Contract only** — public types/tests describe the behavior without a
  complete durable implementation.
- **Docs/tests only** — a proposal or isolated oracle exists without a product
  adapter.
- **Missing** — no usable implementation was found.

| Capability | Evidence in this checkout | Status | Product gap |
|---|---|---|---|
| UTF-8 canonical document storage | `IDocumentStorage`, `CanonicalContentStore`, MDBX canonical store, `mdbx_document_storage_test.cpp` and canonical-content tests | Implemented and tested (C1/raw subset) | Durable edit/materialize conformance and framed/segment codecs are not a single host API |
| Resource identity, manifest and update | `IResourceManifestStorage`, `MdbxResourceManifestStorage`, `MdbxResourceImporter`, MDBX importer tests | Implemented and tested (bounded profiles) | Full source-ref catalog, locator hydration and general reindex lifecycle remain open |
| M0 Chunk envelope | `IKnowledgeUnitStore`, `MdbxKnowledgeUnitStore`, `mdbx_knowledge_unit_store_test.cpp` | Implemented and tested (Chunk subset) | Supersedence orchestration, non-Chunk payload stores and lineage publication are absent |
| Revision-aware `Original` projection | `IProjectionStore`, `OriginalProjection`, `MdbxProjectionStore`, projection-aware retriever tests | Implemented and tested (envelope-seed profile) | Full body/locator hydration, reclamation and projection lifecycle are not integrated |
| Lexical retrieval | `ILexicalIndex`, exact/flat/persistent/segmented indexes, importer and retriever tests | Implemented and tested (lexical profiles) | Product-wide projection lifecycle, trace and bounded candidate reader are not unified |
| Source hydration/citation | `SourceRefSummary` in envelopes and manifest bindings | Implemented but not integrated | A stable public result must return revision + typed locator and hydrate or report unavailable |
| Scope isolation | `ScopeId` in M0 records and MDBX routing tests | Implemented and tested (covered profiles) | No common enforcement point across every future index adapter |
| Retrieval trace | `IRetrievalTrace` is described in milestones, but no corresponding public result path was found | Missing | Add a minimal trace without making diagnostics a hot-path requirement |
| Dense embedding registration | `IEmbedder`, `EmbeddingModelInfo`, embedding contract tests | Contract only | No product adapter ties an embedder to a durable profile |
| Exact vector baseline | `ExactVectorIndex` and tests | Implemented but not integrated | It is keyed by `ChunkId`; no M0 publication/freshness adapter exists |
| Binary candidate indexes | `IBinarySignatureIndex`, flat and multi-probe implementations/tests | Implemented but not integrated | Candidate identity, generation and canonical hydration are external to the index |
| ANN/HNSW | Roadmap and optional benchmark flags only | Missing | Do not add a public ANN API before the conformance boundary below is accepted |
| Hybrid/RRF | `HybridRetrievalEngine` and contract tests | Contract/partial implementation | Route-local pools, hard-filter diagnostics and freshness-aware fusion are incomplete |
| TSV/CSV structured retrieval | Roadmap only | Missing | Parser, canonical table rows/cells, locators and targeted invalidation are absent |
| Runtime queue/maintenance | Roadmap contracts | Missing | No bounded queue, compaction handoff or recovery worker in the product path |

### Smallest usable vertical slice

The current implementation can demonstrate ingest, lexical search and reopen
for selected MDBX profiles. It cannot yet promise the complete KB-0 contract
to an arbitrary consumer because retrieval results still use the legacy
`RetrievedChunk` (`src/agent_memory/retrieval/Retrieval.hpp`) and the primary
index contracts use `ChunkId` (`VectorIndex.hpp`, `BinarySignatureIndex.hpp`),
while the M0 durable identity is `KnowledgeUnitId`.

The next integration slice should therefore be a read-only adapter and fixture,
not a new storage abstraction:

1. import one revision-bound UTF-8 resource;
2. resolve manifest binding `knowledge_unit:<id>` to a `KnowledgeUnitId`;
3. read the active `Original` projection and lexical hit;
4. reject a stale generation before materializing the result;
5. hydrate the canonical source summary/locator;
6. close, reopen, repeat, and exercise interrupted publication.

## 2. Architecture risks

No new P0 was found. The following are the release-relevant risks.

### P1 — identity and freshness are not one contract

`KnowledgeUnitId` is the durable M0 identity, but `IVectorIndex`,
`IBinarySignatureIndex`, `ILexicalIndex`, and `RetrievedChunk` are all keyed or
returned through `ChunkId`/`DocumentChunk`. An adapter can map these values, but
the mapping and its revision/generation checks are not currently a public,
durable contract. Without that boundary an approximate result may be returned
after a source update or from the wrong scope.

### P1 — the product result cannot guarantee citation hydration

`IRetriever::retrieve()` returns `RetrievalResult`, whose elements contain a
legacy `DocumentChunk` and free-form `Metadata`. The product objective requires
revision-bound evidence and an explicit hydrated/partial/unavailable state.
`ProjectionAwareLexicalRetriever` performs useful freshness checks internally,
but the guarantee is not represented in the result type and is not shared by
dense or binary routes.

### P1 — `MemoryStack` is normative documentation, not an executable boundary

`milestones.md`, `memory-stacks-roadmap.md`, and `knowledge-base-roadmap.md`
describe profile validation and a `MemoryStack::open()` flow. No public
`MemoryStack` implementation is present in the current `src/` tree. The docs
must not describe M1 profile validation as shipped until a narrow profile
factory and its conformance tests exist.

### P2 — durable and derived lifecycles are coupled only by convention

MDBX adapters serialize and deserialize complete values (for example
`MdbxDocumentStorage.cpp`, `MdbxKnowledgeUnitStore.cpp`), while derived index
records carry no common publication envelope. This is correct for the current
bounded profiles, but it leaves no shared stale-result, delete, rebuild or
reopen contract for future vector/ANN adapters.

### P2 — technical-preview packaging is incomplete

`CMakeLists.txt` defines the static target and public alias, but has no install,
package-config or exported-target rules. A clean clone can build in-tree, yet a
consumer cannot follow the advertised install/add-subdirectory path without
copying source layout assumptions. This is a release-readiness gap, not a
reason to add a package manager or generated framework now.

### P2 — research and product claims share too much surface area

Research binaries and opt-in CTest jobs are carefully gated, but the public
headers expose several index contracts next to product storage types. The
documentation must continue to label `seg-v2`, binary candidate indexes and
learned encoders as derived/research profiles until they pass the same identity,
freshness and reopen suite as the exact lexical path.

## 3. Evidence-backed optimization targets

These are the five smallest targets with direct source evidence. They are
optimization hypotheses, not claimed speedups; each needs a before/after
benchmark with the reproducible contract in `milestones.md`.

| Priority | Evidence | Likely cost | Measurement to add |
|---|---|---|---|
| 1 | `ProjectionAwareLexicalRetriever.cpp` scans `scan_by_scope()` and then calls `IKnowledgeUnitStore::find()` while materializing hits | O(units) candidate validation and repeated envelope copies | candidate count, unit lookups, bytes decoded, p50/p95 by scope size |
| 2 | `MdbxDurableLexicalDocumentMemory.cpp` calls `rebuild_scope()` after an intervening scope generation | Full-scope rebuild on cross-instance publication | affected units, rebuild bytes, write/read amplification, recovery latency |
| 3 | MDBX stores serialize complete `Document`, `DocumentChunk`, and `KnowledgeUnitEnvelope` values on read/write | allocation and encode/decode cost on hot hydration paths | encoded bytes, allocations, decode time, map pages touched |
| 4 | `IVectorIndex::find/search` and `IBinarySignatureIndex::find/search` return records/results by value, including `Metadata` | result materialization and copies dominate small candidate sets | candidate bytes, metadata copy time, RSS and top-k construction time |
| 5 | There is no shared batched reader for canonical source/projection hydration; retrievers perform per-hit lookups | N round trips and duplicated freshness checks | lookup count, transaction count, bytes per hit, p95 under top-k 10/50/100 |

The existing segmented-index measurements are useful for their isolated
workload, but they do not measure these product-path costs. No optimization
should be accepted from a microbenchmark that omits source revision, scope,
reopen and citation hydration.

## 4. Minimal ANN adapter and conformance proposal

Do not implement HNSW/IVF/MIH in this audit. First introduce one internal
adapter boundary that can wrap exact, binary or external derived indexes.

### Stable candidate reference

The adapter should resolve every candidate to a durable record equivalent to:

```text
CandidateRef {
    KnowledgeUnitId unit_id;
    ScopeId scope_id;
    ProjectionKind projection;
    uint64_t unit_revision;
    uint64_t projection_generation;
    backend-local locator;
}
```

The backend-local locator is never exposed as a final retrieval identity. For
the current M0 import profile it is obtained from the manifest binding
`knowledge_unit:<numeric-id>` and the active `Original` projection. A future
ChunkId adapter may store a durable `(ChunkId, KnowledgeUnitId, revision)`
binding, but it must not infer one from numeric values or array positions.

### Required adapter behavior

- validate model/metric/bit-width or projection descriptor at open and query;
- apply scope and hard access filters before candidate creation and again before
  hydration;
- compare active unit and projection generations before accepting a hit;
- reject stale/deleted candidates rather than silently returning them;
- support delete, rebuild and reopen with the canonical store as source of
  truth;
- expose backend work diagnostics separately from final relevance scores;
- fall back to exact lexical/vector retrieval when the derived index is absent,
  stale or unavailable.

### Conformance suite

One parameterized suite should run against `ExactVectorIndex`,
`FlatBinarySignatureIndex`, each future ANN adapter, and an external derived
index shim:

1. identity/model descriptor and incompatible-query rejection;
2. scope and hard-filter isolation;
3. active-generation acceptance;
4. stale-generation rejection after update;
5. delete/tombstone behavior;
6. rebuild from canonical storage;
7. close/reopen parity;
8. missing-index fallback and deterministic tie ordering;
9. candidate diagnostics versus final score accounting;
10. source citation hydration with `Partial`/`Unavailable` status.

This suite is the gate for calling a backend ANN-capable. `BinaryFlat` remains
an exact Hamming-space baseline, not an ANN claim.

## 5. M0 and KB-0 release gaps

The following gaps are intentionally small and independently reviewable:

| Follow-up | Scope | Acceptance test |
|---|---|---|
| Revision-bound retrieval result | Add a product-facing result/view adapter without changing canonical storage | import/update/reopen returns active revision, typed locator and explicit hydration status; stale result is rejected |
| KnowledgeUnit-to-index binding | Persist/validate the `ChunkId` ↔ `KnowledgeUnitId` ↔ revision mapping in the active manifest/profile | every lexical/vector candidate resolves to one unit; wrong scope, missing and stale bindings fail closed |
| Minimal retrieval trace | Add query id, route, candidate count, filtered count and hydration outcome | trace is deterministic, optional, and absent from the default hot path |
| Profile/open conformance | Replace documentation-only `MemoryStack` claims with one executable M0 profile descriptor/factory | open/reopen validates capabilities, schema signature and optional MDBX wiring |
| Technical-preview packaging | Add install/export smoke coverage only after the target surface is stable | clean consumer project succeeds with `add_subdirectory` and installed target; MDBX remains optional |
| Batched source reader | Add a bounded read API for candidate hydration | top-k hydration uses bounded transactions and reports lookup/decoded-byte counters |

These should be separate PRs. Do not combine them with ANN implementation,
batching policy, coordinator integration, migrations, or production activation.

## 6. Technical-preview acceptance matrix

| Gate | Required evidence | Current state |
|---|---|---|
| Clean clone/build | C++17 configure/build with tests and examples | Build recipe exists; run on the release candidate |
| Optional MDBX | flat submodules, MDBX configure/build/CTest | CI job exists and is green for current PRs; retain as a separate gate |
| Consumer integration | `add_subdirectory` and installed-target smoke project | Missing install/export path; follow-up above |
| Core API tests | domain, storage, lexical, retrieval and embedding contracts | Present; no single product-path test yet |
| Source update/reopen | revision update, stale rejection, close/reopen, interrupted publication | Present in bounded importer/projection tests; needs unified result fixture |
| Research isolation | `seg-v2`/binary/ANN experiments opt-in and not default | Satisfied by options and research CTest gating |
| Evidence discipline | raw/receipt hashes, hardware/build flags, cache procedure and limitations | Normative contract exists; every new claim must publish it |
| Release notes | explicit M0 subset, optional MDBX, non-goals and known gaps | Not yet consolidated into a technical-preview note |

## 7. Documentation reconciliation and status registry

The minimal correction is a single authoritative status registry, not another
roadmap hierarchy. `milestones.md` remains the scope resolver; this audit and
`retrieval-roadmap-coverage.md` describe evidence at the current baseline.

The following statements must be kept aligned in future PRs:

- use `IVectorIndex` as the actual vector contract; any `IDenseIndex` wording
  in older roadmap text is a planned alias, not an implemented API;
- describe `MemoryStack::open()` and profile validation as planned until the
  executable profile boundary lands;
- keep M0 marked partial until retrieval trace, full source hydration and the
  remaining normative M0 rows have tests and a concrete owner;
- label `ExactVectorIndex`, binary indexes and `seg-v2` as derived/index
  implementations, never canonical storage;
- link every benchmark claim to its corpus, qrels/config, raw observations,
  receipt and exact commit.

## Explicit non-goals

This PR does not:

- implement ANN/HNSW/IVF/MIH or choose an index default;
- activate `seg-v2`, change production routing, or integrate a coordinator;
- add a generic manager, plugin framework, background queue or replication
  system;
- migrate MDBX schemas or alter canonical DBI ownership;
- claim multimedia, TSV/CSV, graph, temporal or bi-temporal product support;
- replace the exact lexical/vector oracle with an approximate backend;
- treat benchmark smoke tests as product readiness evidence.

