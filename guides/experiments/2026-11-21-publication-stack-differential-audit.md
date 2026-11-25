# Historical publication stack: exact-head differential audit

Date: 2026-11-21. Status: **READ_ONLY_SOURCE_AUDIT; no coordinator port or
activation qualified**. This corrects the dependency inventory of
[product integration Phase 1](../storage-product-integration-audit.md) and its
[proposed ADR](../storage-product-integration-adr.md), not the old branches.

## Question, source and method

Question: which parts of the unmerged coordinator stack can inform numeric/flat
publication without rebuilding existing seams or importing a weaker guarantee?
Expected outcome: a selective reuse plan, not a whole-stack merge.

The comparison baseline is
`main@acd5ad5545386b3feeaa79f8a587855b60801b4d` (`src` tree
`87a70c0f850266e549458be40f05760febb1050f`). GitHub API and `git ls-remote`
observations at 2026-11-21T20:12Z agree on that main and these heads;
all seven PRs remain **OPEN Draft**, unmerged:

| PR / original slice | Exact observed HEAD |
| --- | --- |
| [#549](https://github.com/LimiNode/agent-memory-cpp/pull/549): write ownership | `be4c11f5459640d25bf382797b2b852d83faba72` |
| [#550](https://github.com/LimiNode/agent-memory-cpp/pull/550): durable coordinator metadata | `45c870c45e5ce20526e62e3ffd277c576cabe76c` |
| [#551](https://github.com/LimiNode/agent-memory-cpp/pull/551): authoritative participants | `38c7473e1f2b2c5e4a76172e2b052b4cc33f27e9` |
| [#552](https://github.com/LimiNode/agent-memory-cpp/pull/552): projection/lexical participants | `e64630ab7ca0523bf1bf26e3fd5d6bbd41f3cdcf` |
| [#553](https://github.com/LimiNode/agent-memory-cpp/pull/553): pinned read scope | `18548d1fa65b25c7906e50214712199b9cac0aeb` |
| [#554](https://github.com/LimiNode/agent-memory-cpp/pull/554): corrective evidence | `e3766ab1896b9642c92ed33553b6dff8749864bf` |
| [#555](https://github.com/LimiNode/agent-memory-cpp/pull/555): writer activation gate | `00adeb640557a143ac2279d0e4f9ccd4eeead060` |

#549 depends on #544 at `b7fff7002efd412d3e2a2630f160a86ac6303fbc`;
#550 through #554 form the successive stack shown above. #555 is separate.
The historical merge base of #554 and audited main is
`1e2806d21bc36fa6baeca536aaccec0bf535502c`, also #555's reported base SHA.
Incremental base/head file inventories locate each contribution; source claims
below use the final corrective #554 head, not the less-correct earlier slices.

The existing publication-audit checkout at
`3be7657569facf95cda39b4001badd6b5cdaead1` and its preserved
[October integration audit](2026-10-27-publication-stack-integration-audit.md)
and [November reuse audit](2026-11-06-publication-stack-reuse-supersession.md)
were inspected first. They remain historical evidence at their own named main
SHAs; this note does not rewrite them as current qualification. Graph discovery
found no matching coordinator/numeric nodes, so exact-object `git show`,
`git ls-tree` and source comparisons were used. No trial merge, restack, build,
historical-test replay, old-branch edit or PR-state change was performed.

## Reuse matrix against audited main

Classifications apply to the named behavior, not an automatic disposition of an
entire PR. `REUSABLE_WITH_CHANGES` requires a reviewed port and new tests;
`UNQUALIFIED` does not erase the narrower historical implementation evidence.

| Behavior / source | Classification | Current-main delta and reuse instruction |
| --- | --- | --- |
| Shared context, legacy KU/projection/manifest transaction reads; B1/B3 fenced derived output and current revalidation [M1], [M2], [M6] | `EXISTS_IN_MAIN` | Reuse current adapters and admission. They do not atomically publish flat canonical inputs, typed KU, lexical rows and a new product intent. |
| #549 owner-thread write scope, scope-only commit/abort; #553 pinned transaction and #554 closed concrete family admission [H1], [H2], [H3] | `REUSABLE_WITH_CHANGES` | Preserve ownership, foreign-store rejection, complete-family admission and abort-reset counterexamples. Adapt to current transaction provenance, lock order, lifetime and numeric stores; do not invent a generic transaction framework. |
| #550/#554 frontier CAS, fencing identity, exact replay, persisted receipt/binding agreement [H2] | `REUSABLE_WITH_CHANGES` | Reuse checked-state/replay patterns. Historical metadata is not the proposed canonical-plus-pending-intent / later-publication protocol or authenticated writer admission. Review the new record owner and bounded codec before adding any DBI. |
| #551/#553 concrete C1 canonical and legacy KU participants; old coordinator wire/table/profile [H4], [H5] | `INCOMPATIBLE` | Not drop-in numeric/flat participants or a migration mapping. Do not copy old store implementations, serializers or DBI manifest over current main. Preserve current C1 protections, flat history/mapping and typed KU allocation domain. |
| #552/#554 prepared/persisted projection and manifest checks, exact resource-generation selection [H4], [H6] | `REUSABLE_WITH_CHANGES` | Retain read-back, full projection/manifest digest and V6 normalization counterexamples. Extend the authoritative canonical/body/typed binding; matching revision numbers alone are insufficient. |
| Earlier reuse audit's prerequisite to first land snapshot, canonical and ArtifactCatalog/File CAS foundations | `SUPERSEDED` | Those foundations now exist, as the current product audit maps. Reuse them and B1/B3 [M1], [M2], [M6]; do not restart their implementation. The missing flat/typed multi-family writer and durable product intent are not superseded or already implemented. |
| #554 `Complete` / `TargetExact`, lexical content, scope-wide exactness, crash and cost qualification [H3], [H7], [H8] | `UNQUALIFIED` | Historical target checks are real, but not full current product acceptance, arbitrary-query completeness, bounded target-local cost or complete crash recovery. Keep the weaker names and limits. |
| #555 legacy-writer admission/quiescence procedure [H9] | `REUSABLE_WITH_CHANGES` | Require an explicit controlled-writer/fencing deployment gate and mixed-writer negative controls. A coordinator marker, token or green test does not disable public legacy writers. |

## Source-confirmed boundaries and port gates

### Transaction provenance and writer ownership

The historical write scope rejects nested transactions, records its owner
thread, passes one transaction to participants and owns commit/abort [H1].
The corrective coordinator admits exactly five final concrete write families;
the read scope requires all six final read families. Constructors compare the
underlying store connection with the declared context, including a populated
foreign-store negative fixture [H2], [H3], [H7]. This is useful implementation,
not merely assigned consistency enums.

It is not sufficient to transplant the old caller-transaction methods. For
example, historical C1 `create_document_in_transaction` and
`commit_in_transaction` take the instance mutex after the caller owns a writer,
while ordinary owned writes take that mutex before waiting for a writer [H5].
That creates a static lock-order inversion risk if both paths share an adapter;
no deadlock was reproduced in this audit. Those methods also do not themselves
perform the explicit transaction validation now used by mapping. Current
mapping validates the active environment/mode and deliberately avoids taking
the owned-write mutex in a caller-held writer [M3]. A port must test foreign,
inactive, read-only and wrong-thread usage, rollback, no ownership transfer and
bounded contention against the current dependency pins, not infer safety from
old fixture success.

### Scope, exactness and stale admission

The historical writer checks plan scope and the persisted unit's scope, and
re-reads authoritative rows before issuing `Complete` [H2], [H4]. The pinned
reader compares receipt/frontier/binding, canonical identity/revision, target
unit/source generation, projection version/digest, manifest bindings and
lexical membership in one transaction [H3]. Mutated target rows can downgrade
the result. Unrelated lexical units and historical projections intentionally
do not prevent `TargetExact` [H7].

The reader is not a complete product authorization gate: its unit-target
predicate does not directly compare `unit.scope_id` or active lifecycle, and
its observation can retain row payloads when completion is not `TargetExact`
[H3]. These are source-level limits, not newly executed exploits. Preserve
current main's explicit scope/active-unit admission [M1], add flat/typed and
canonical-body authority, and require no body/preview on failed public current
admission. Host authorization/revocation is separate from a matching stored
scope. Do not copy a weaker historical predicate over the current gate.

### Lexical token-content binding and cost

The decisive old negative control erases the target lexical contribution and
recreates it with `{"wrong", "tokens"}` under unchanged identities. It still
expects `TargetExact` [H7]. Thus projection payload digest, lexical membership
and revision equality do **not** prove that indexed tokens derive from those
bytes. A future profile needs a reviewed tokenization/normalization and full
content-binding contract; this audit does not introduce a digest recipe.

Both the lexical write validator and pinned reader call flat-v2
`read_in_transaction`, which deserializes/rebuilds the complete index [H6], [H8].
Expected materialization cost is O(N) in the complete flat-v2 index, not O(1)
in the receipt target. This is source-derived cost, not a new timing result.
Current segmented lexical storage is not automatically a compatible
coordinator participant; generation, descriptor and binding proofs need their
own port and qualification. No universal throughput or production-tail claim
follows from target-bounded semantics.

### Legacy bypass, recovery and independent durability domains

The old coordinator fences only calls that participate. Public canonical,
legacy KU, projection, lexical and manifest write APIs can still mutate state
without advancing its receipt/frontier; #554 itself uses such mutations as
negative controls [H7], [H9]. A read-side downgrade is detection, not prevention;
the wrong-token control shows that not every semantic bypass is detected.
The exact-head #555 document requires both initial draining/quiescence and
continuous control of writer startup after activation, not a one-time stop/start
[H9]. Current main also exposes independently committed numeric flat and typed
KU writes; their markers do not install mandatory coordinator admission [M4], [M5].
Require a declared writer admission/epoch policy, control of every later writer
start or actual fencing of all admitted writers, and independent-process stale
writer/crash-transition controls. Unmanaged old processes leave activation
unsupported. MDBX writer serialization orders transactions; it is not writer
authorization.

#554 records rollback, retry and close/reopen counterexamples, but explicitly
excludes full participant-failure-point crash coverage [H8]. Its one shared
publication commit is not the ADR's two separate atomic commits with durable
pending intent. File CAS is an independent durability domain, absent from this
old transaction proof. Retain current importer/catalog recovery, verified CAS
bytes and final metadata admission; do not reinterpret them or the old receipt
as recovery for the full proposed tuple.

## Outcome and next check

The omission is corrected: transaction and read participants must be evaluated
for selective reuse before new seams are designed. Full numeric product Phase 3
remains **BLOCKED**, and coordinator implementation remains a later, separately
authorized workstream after Identity Architecture. First review the minimal
port delta and the current-main counterexamples above; then qualify any
authorized seam change at its own exact source/dependency heads. No old stack
merge, default activation, database migration or release is authorized.

This documentation audit runs no new storage/sanitizer tests and inherits no
old CI pass as current qualification. The actual four-test TSan attempt remains
**NOT_QUALIFIED**, with the dependency-level race diagnosis unresolved, as
recorded in the [TSan note](2026-11-21-storage-tsan-applicability.md). No
suppression, dependency patch or unsupported false-positive conclusion is added.

[H1]: https://github.com/LimiNode/agent-memory-cpp/blob/e3766ab1896b9642c92ed33553b6dff8749864bf/src/agent_memory/infrastructure/mdbx/MdbxPublicationWriteScope.cpp#L19
[H2]: https://github.com/LimiNode/agent-memory-cpp/blob/e3766ab1896b9642c92ed33553b6dff8749864bf/src/agent_memory/infrastructure/mdbx/MdbxPublicationCoordinator.cpp#L544
[H3]: https://github.com/LimiNode/agent-memory-cpp/blob/e3766ab1896b9642c92ed33553b6dff8749864bf/src/agent_memory/infrastructure/mdbx/MdbxPublicationReadScope.cpp#L112
[H4]: https://github.com/LimiNode/agent-memory-cpp/blob/e3766ab1896b9642c92ed33553b6dff8749864bf/src/agent_memory/infrastructure/mdbx/MdbxAuthoritativePublicationParticipants.cpp#L15
[H5]: https://github.com/LimiNode/agent-memory-cpp/blob/e3766ab1896b9642c92ed33553b6dff8749864bf/src/agent_memory/infrastructure/mdbx/MdbxCanonicalContentStore.cpp#L724
[H6]: https://github.com/LimiNode/agent-memory-cpp/blob/e3766ab1896b9642c92ed33553b6dff8749864bf/src/agent_memory/infrastructure/mdbx/MdbxProjectionLexicalPublicationParticipants.cpp#L65
[H7]: https://github.com/LimiNode/agent-memory-cpp/blob/e3766ab1896b9642c92ed33553b6dff8749864bf/tests/infrastructure/mdbx/mdbx_publication_read_scope_test.cpp#L576
[H8]: https://github.com/LimiNode/agent-memory-cpp/blob/e3766ab1896b9642c92ed33553b6dff8749864bf/guides/experiments/2026-10-25-publication-coordinator-final-acceptance.md
[H9]: https://github.com/LimiNode/agent-memory-cpp/blob/00adeb640557a143ac2279d0e4f9ccd4eeead060/guides/experiments/2026-10-25-publication-writer-activation-gate.md#L78
[M1]: https://github.com/LimiNode/agent-memory-cpp/blob/acd5ad5545386b3feeaa79f8a587855b60801b4d/src/agent_memory/infrastructure/mdbx/MdbxSnapshotRevisionBoundRetriever.cpp#L50
[M2]: https://github.com/LimiNode/agent-memory-cpp/blob/acd5ad5545386b3feeaa79f8a587855b60801b4d/src/agent_memory/infrastructure/mdbx/MdbxDerivedPublicationStore.hpp#L96
[M3]: https://github.com/LimiNode/agent-memory-cpp/blob/acd5ad5545386b3feeaa79f8a587855b60801b4d/src/agent_memory/infrastructure/mdbx/MdbxStorageIdentityMapping.cpp#L300
[M4]: https://github.com/LimiNode/agent-memory-cpp/blob/acd5ad5545386b3feeaa79f8a587855b60801b4d/src/agent_memory/infrastructure/mdbx/MdbxCanonicalFlatStorage.cpp#L470
[M5]: https://github.com/LimiNode/agent-memory-cpp/blob/acd5ad5545386b3feeaa79f8a587855b60801b4d/src/agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitTypedStore.cpp#L358
[M6]: https://github.com/LimiNode/agent-memory-cpp/blob/acd5ad5545386b3feeaa79f8a587855b60801b4d/src/agent_memory/infrastructure/mdbx/MdbxCurrentDerivedPublicationReader.hpp#L33
