# First Release Profile: Normalized-First, Artifact-Aware Knowledge Base Preview

This document defines the intended first installable knowledge-base preview.
It is a release contract and a capability gap list, not evidence that every
listed feature is already implemented. The implementation inventory remains
the source of truth for release readiness.

The evidence-backed status of the current bounded preview is recorded in
[`technical-preview-preflight.md`](technical-preview-preflight.md). That
preflight is narrower than this normalized-first target and does not promote
the remaining contract-only source profiles to implemented capabilities.

The profile is normalized-first and artifact-aware. It is suitable for
articles, Telegram posts and normalized video transcripts supplied by external
connectors. HTML, video and other acquisition artifacts may be retained or
discarded according to the selected retention policy; they are not silently
replaced by a synthetic search projection.

The external capability comparison and the corresponding release/post-release
decisions are recorded in
[`ragflow-capability-and-release-roadmap.md`](ragflow-capability-and-release-roadmap.md).

## Required guarantees

1. A canonical normalized UTF-8 representation has a stable logical source
   identity, observed revision, digest and provenance for the normalizer.
2. Structural segmentation preserves headings and heading paths, paragraph and
   section boundaries, lists, code blocks, tables, image references and timed
   transcript ranges when those structures are present.
3. Each `SegmentSet` and chunking recipe is versioned. Rechunking creates a new
   `SegmentSet` and mapping for the relevant `Representation`; it does not
   silently create a new `Representation` or `SourceRevision`. Older
   `SegmentSet` values and evidence anchors remain addressable under retention.
4. Saved canonical bodies use file-backed content-addressable storage with
   SHA-256 over decoded bytes. Raw and independently framed Zstd encodings
   round-trip to the same logical digest, and codec generations never change
   logical identity.
5. Structured tables preserve column, row and cell identity with exact cell
   hydration. Retained images preserve bytes in CAS plus caption/alt/source
   bindings; transcript segments preserve time intervals and alignment
   provenance. Typed locators distinguish these cases from plain text ranges.
6. Derived summaries, facts, entities, notes and question/context projections
   retain exact input revisions, recipe/model/config fingerprints, publication
   generation and evidence. External LLM, OCR, ASR and extraction services are
   allowed; inference is not a core-library dependency.
7. Retrieval may search Original and admitted derived views, but results are
   deduplicated by canonical identity and preserve scope, freshness, budget and
   completeness semantics.
8. Text, table, image and transcript hydration returns `Hydrated` only after
   resolving the exact retained normalized revision and validating its locator
   and digest. Missing, corrupt, stale and unsupported material must fail
   closed.
9. Ingest publication is crash-safe and recoverable. Catalog plus retained CAS
   bodies have a backup/restore path, reachability-based collection, reopen
   checks and schema/codec compatibility gates.
10. An external C++17 consumer can reproduce the supported profile from a clean
    build without depending on repository-private test infrastructure.

The following source profiles are in scope:

| Source profile | Required retained primary representation |
|---|---|
| Article | normalized structured document, tables, image bytes when retained, locators |
| Telegram post | text, metadata, source revision and retained image bytes |
| Video transcript | normalized timed transcript, alignment provenance, external video identity and optional retained frames |
| Table-bearing document | canonical table structure with row/cell locators and exact reads |

Original HTML and video files remain optional. A transcript or external time
link must never be presented as proof of retained original-video bytes.

## Retention policy

The profile follows the retention frontier in
[`canonical-content-storage-roadmap.md`](canonical-content-storage-roadmap.md).
`KeepOriginal`, `KeepUntilCanonicalized`, `KeepExternalReference` and
`DiscardAfterVerifiedCanonicalization` are policy choices. Discarding an
acquisition artifact is valid only when all advertised capabilities remain
available from the retained canonical representation or a resolvable external
reference. A URL or an ETag alone does not prove byte hydration.

The minimum retained text profile is a canonical normalized body plus its
structural objects and locators. Original HTML/video bytes are optional under
this profile, while tables, image references and transcript time coordinates
remain part of the normalized representation when supplied by the connector.

## Current implementation boundary

The conservative inventory in
[`retrieval-roadmap-coverage.md`](retrieval-roadmap-coverage.md) currently
supports the durable raw UTF-8 importer, revision-aware Original publication,
bounded lexical retrieval and an experimental Raw Markdown host path.
`MdbxRawMarkdownProductIngestion` composes importer, File CAS/catalog, typed
resource-artifact binding, lexical admission and authoritative article
hydration. PR #601 adds a bounded persistent
lexical candidate parity seam, while PR #602 records the source-bound release
qualification contract; neither enables a default serving backend or turns
contract-only cases into implemented release capabilities. PR #603 also
provides a bounded,
executable article-to-citation conformance slice: it composes structural
segments, snapshot admission, the typed resource-to-artifact join and
byte-verified Raw range materialization. That slice is evidence for the article
path, not a general product ingestion or universal Hydrated API. The new
composition is still bounded to original Markdown and keeps CAS and MDBX as
separate durability domains; incomplete intermediate rows are fail-closed
rather than presented as published product results.
Structural product chunking, derived multi-view retrieval and install/export
remain separate release gates. A roadmap contract or an API sketch must not be
reported as an implemented release capability.

The first release therefore remains a Technical Preview until the following
vertical evidence exists:

- normalized Markdown article: structural chunks, retrieval and exact
  normalized-text citation through the experimental Raw Markdown product path,
  with the canonical body materialized from File CAS and a raw/framed-Zstd
  round trip checked; derived summary/concepts remain a separate gate;
- table-bearing article: structural table representation, stable row/cell
  locators and exact cell hydration rather than a prose-only chunk;
- Telegram text with retained image bytes: source revision update, stale
  rejection, reopen, image-byte hydration and evidence-preserving bindings;
- timed transcript: distinct transcript revision, SegmentSet and derived view,
  exact text hydration and an external video time link without claiming
  retained video bytes.

These fixtures are release evidence only when they exercise the real storage
and retrieval path. DTO round-trips, image references without retained bytes,
or a summary that replaces the canonical representation do not satisfy the
profile.

ANN, graph traversal, embedded LLM inference, OCR/ASR connectors, late
chunking, complex rerankers and a universal agent coordinator are not required
for this profile.
