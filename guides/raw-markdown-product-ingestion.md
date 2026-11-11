# Raw Markdown product-ingestion slice

`MdbxRawMarkdownProductIngestion` is the bounded host-facing composition for
one original UTF-8 Markdown source. It is an experimental product path, not a
coordinator or a production retrieval switch.

```text
ObservedResource (.md/.markdown, original UTF-8)
  -> MdbxResourceImporter (ResourceRevision, raw Chunk + Original manifest)
  -> FileRawBlobStore + MdbxArtifactCatalog
  -> MdbxResourceArtifactBindingStore (typed revision join)
  -> coherent current-manifest/join/catalog observation (Published or Conflict)
  -> bounded lexical candidate
  -> MdbxAuthoritativeArticleHydrator (verified bytes or no bytes)
```

The importer, catalog and typed join remain separate authorities. MDBX and the
filesystem CAS are separate durability domains; the composition does not claim
physical atomicity between them. `Published` is returned only after all three
steps complete and a final coherent MDBX read observes an active manifest with
the exact resource ID, generation, full digest, pipeline and Original profile,
the expected raw/Original manifest bindings, exact typed join, and finalized
materialized catalog receipt with the same artifact identity. This final read
snapshot is the publication result's linearization point. A revision superseded
before that snapshot returns `Conflict`; `Published` does not promise freshness
after the snapshot or verify CAS bytes again. Hydration has its own later proof.

A retry of the same body **and binding timestamp** is idempotent. The host must
reuse `bound_at_ms` across retries (the default `0` is stable); a changed timestamp
is a conflicting catalog claim, not a fresh binding for the same revision.
This preserves existing ArtifactCatalog semantics, including pending intents.
A failure after any earlier step may leave staging or an orphan CAS
object, but the product-facing read is still fail-closed and reports no
hydrated bytes until the catalog, join, active manifest, projection and CAS
digest all match. Reopen/retry uses the existing importer and catalog recovery
paths.

Persisted source publication, current product publication, retrievability and
hydration are separate outcomes. Import admission defaults to 64 MiB per body;
query indexing defaults to 8 MiB across admitted projections and 4096 units.
Thus `Published` does not guarantee retrievability under every corpus/query
budget. A budget overflow throws `ProjectionAwareLexicalRetrievalBudgetExceeded`
without returning a partial ranking as complete. Hydration independently checks
range and complete-object verification budgets and returns no bytes on failure.

The product-facing operation has these completion boundaries:

```text
Observed -> Imported -> Cataloged -> Joined -> Final snapshot -> Published
              |           |           |             |
              +-----------+-----------+-------------+--> Incomplete/Conflict
```

`Imported`, `Cataloged` and `Joined` are lower-level durable observations, not
successful product results. There is no deletion or retirement operation in
this slice; source revisions remain readable as historical records, while
current/stale admission continues to be enforced by the existing revision
frontier. Retention collection and lifecycle retirement require a separate
contract.

Importer intents recover only after their existing fencing lease expires; the
adapter forwards `importer_ownership_lease_ms` and `importer_clock_ms` without
changing writer ownership. Catalog reopen can complete metadata/receipts when
CAS bytes already exist, but neither recovery path invents the missing product
join. The host must replay the original request to finish the product operation.
Before manifest publication, first-import staging units are hidden from lexical
candidate production. After manifest publication but before the join, the exact
candidate can be visible, but hydration returns `Missing/BindingMissing`. After
join publication, current metadata and verified bytes can be read even if the
import caller was interrupted before receiving its result; replay reconciles it.

The fault regressions declare candidate visibility explicitly for first imports:

| Interruption | Import result | Candidate/hydration before replay | Completion |
| --- | --- | --- | --- |
| After importer intent, after raw units, before manifest | `Incomplete` | No candidate | Expired-lease reopen and host replay |
| After manifest | `Incomplete` | Present; `Missing/BindingMissing`, no bytes | Reopen and host replay |
| Before CAS, after CAS, after catalog commit | `Incomplete` | Present; `Missing/BindingMissing`, no bytes | Catalog recovery where possible, then host replay |
| After catalog completion, before typed join | `Incomplete` | Present; `Missing/BindingMissing`, no bytes | Host replay |
| A@2 completes before A@1 final snapshot | A@1 `Conflict` | Retained A@1 is `Stale`, no bytes; A@2 hydrates | No rollback of A@2 |
| CAS missing or corrupted after publication | Earlier `Published` unchanged | Present; `Missing/CasMissing` or `Corrupt/CasCorrupt`, no bytes | Source availability repair is outside this slice |

The generated `SourceRevisionId` is a bounded 128-bit fingerprint over the
explicit resource identity, generation and full SHA-256 body digest. The full
digest remains in `ResourceRevisionRef` and the artifact record; the compact
opaque label avoids exceeding MDBX key limits. It is not a replacement for
`ResourceRevisionRef`, which remains the canonical join key. Binding IDs are
derived from that source-revision identity and never use a fixed `source@1`
value.

The slice admits only `.md`/`.markdown` URIs and `OriginalText`. Tables,
images, OCR, ASR, multimodal codecs, derived Summary/Concept publication,
coordinator integration, BM25/ANN/graph serving and production retrieval
activation remain unsupported. Low-level readers may observe incomplete
staging rows; only the composed operation is a product result.

Conformance is covered by the focused MDBX test and the installed C++17
consumer: first import, identical replay, revision update, lexical candidate
to exact CAS hydration, reopen, and invalid-input/fail-closed boundaries.
