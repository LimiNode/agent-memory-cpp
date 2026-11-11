# Raw Markdown product-ingestion slice

`MdbxRawMarkdownProductIngestion` is the bounded host-facing composition for
one original UTF-8 Markdown source. It is an experimental product path, not a
coordinator or a production retrieval switch.

```text
ObservedResource (.md/.markdown, original UTF-8)
  -> MdbxResourceImporter (ResourceRevision, raw Chunk + Original manifest)
  -> FileRawBlobStore + MdbxArtifactCatalog
  -> MdbxResourceArtifactBindingStore (typed revision join)
  -> bounded lexical candidate
  -> MdbxAuthoritativeArticleHydrator (verified bytes or no bytes)
```

The importer, catalog and typed join remain separate authorities. MDBX and the
filesystem CAS are separate durability domains; the composition does not claim
physical atomicity between them. `Published` is returned only after all three
steps complete and agree on the resource digest. A retry of the same body is
idempotent. A failure after any earlier step may leave staging or an orphan CAS
object, but the product-facing read is still fail-closed and reports no
hydrated bytes until the catalog, join, active manifest, projection and CAS
digest all match. Reopen/retry uses the existing importer and catalog recovery
paths.

The product-facing state machine is deliberately monotonic:

```text
Observed -> Imported -> Cataloged -> Joined -> Published
              |           |           |
              +-----------+-----------+--> Incomplete/Conflict
```

`Imported`, `Cataloged` and `Joined` are lower-level durable observations, not
successful product results. There is no deletion or retirement operation in
this slice; source revisions remain readable as historical records, while
current/stale admission continues to be enforced by the existing revision
frontier. Retention collection and lifecycle retirement require a separate
contract.

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
