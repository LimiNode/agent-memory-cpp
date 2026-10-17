# M0 storage follow-up research

This note records work that is intentionally outside the M0 envelope-store
implementation. It is a research contract, not a production layout choice.

## Canonical ownership and projections

Canonical normalized bytes belong to a backend-independent body/artifact store.
The first implementation may use MDBX, but the contract also permits a
filesystem CAS/pack, a remote object store, or a mixed hot/cold topology. A
`KnowledgeUnit` keeps a bounded retrieval seed and locator/provenance metadata;
it does not own a second authoritative full-text copy.

The M0 insertion index is a serialized vector and therefore promises
deterministic insertion order. Exact replay is idempotent and does not reorder;
erase removes membership, while a committed erase permanently reserves the
numeric id. Explicit sparse ids below the current `@next_id` frontier are
accepted only when their `@used:<id>` marker is absent.

Projection experiments must distinguish at least these modes:

- A — original text only;
- B — annotation only;
- C — original plus contextual annotation;
- D — independent original and annotation routes with explicit fusion.

Every physical projection key must include the logical UnitId, projection role
and variant, model id/version, recipe identity, and generation/revision. A write
must never silently overwrite another model, role, recipe, or generation.

For multimodal resources the ownership chain is:

```text
SourceRevision -> ImageArtifact -> FigureContext/occurrence
              -> OCR / vision description / visual embedding
              -> Segment/Chunk -> EvidenceAnchor
```

OCR, captions, and visual embeddings are derived records. The original image
remains the artifact, and hydration returns the original image or region when
the retention policy makes it available.

Related reading for the future retrieval lane:

- [UniversalRAG (ACL 2026)](https://aclanthology.org/2026.acl-long.177/)
- [MCompassRAG (arXiv 2026)](https://arxiv.org/abs/2606.18508)

## Physical-layout experiments

The Zstd experiment must compare raw/plain against framed Zstd without a
dictionary at 16 KiB, 64 KiB, and 256 KiB frames, recording declared levels,
logical/compressed bytes, frame overhead, encode/decode percentiles, small,
cross-frame and full-range reads, edit rewrites, cold/warm behavior, reopen,
checksum/corruption failures, and MDBX footprint. Dictionary results are valid
only after the no-dictionary baseline and must use a disjoint training
partition. Optional file-CAS/pack results must be reported separately.

Each result records the exact zstd version/configuration, source and dependency
SHAs, and raw JSON receipts. No production codec or frame size is selected from
an unrun experiment.

The companion key/allocator/posting matrix covers 10k and 100k records (1M is
optional), numeric/string/composite keys, serialized vectors, composite
postings, sorted duplicates, low-level DUPFIXED/INTEGERDUP, and segmented
blocks. Its executable validator checks membership, order, uniqueness, delete,
reinsert, pagination, scope isolation, rollback, and reopen. The existing
`mdbx-layout-benchmark` worktree owns that implementation; this note avoids
duplicating it in the M0 PR.

The existing `tools/agent-memory-bench/audit-mdbx-compression-screen.py`
provides a contract-audit pattern for compression receipts; it must be extended
with the range-read, corruption, reopen, and physical-footprint measurements
above before any codec is promoted.
