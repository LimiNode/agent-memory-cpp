# First Release Profile: Normalized-First Knowledge Base Preview

This document defines the intended first installable knowledge-base preview.
It is a release contract and a capability gap list, not evidence that every
listed feature is already implemented. The implementation inventory remains
the source of truth for release readiness.

The profile is text-first and normalized-first. It is suitable for articles,
Telegram text and normalized video transcripts supplied by external
connectors. HTML, video and other acquisition artifacts may be retained or
discarded according to the selected retention policy; they are not silently
replaced by a synthetic search projection.

## Required guarantees

1. A canonical normalized UTF-8 representation has a stable logical source
   identity, observed revision, digest and provenance for the normalizer.
2. Structural segmentation preserves headings and heading paths, paragraph and
   section boundaries, lists, code blocks, tables, image references and timed
   transcript ranges when those structures are present.
3. Each `SegmentSet` and chunking recipe is versioned. Rechunking creates a new
   representation without invalidating older evidence anchors.
4. Derived summaries, facts, entities, notes and question/context projections
   retain exact input revisions, recipe/model/config fingerprints, publication
   generation and evidence. External LLM, OCR, ASR and extraction services are
   allowed; inference is not a core-library dependency.
5. Retrieval may search Original and admitted derived views, but results are
   deduplicated by canonical identity and preserve scope, freshness, budget and
   completeness semantics.
6. Text hydration returns `Hydrated` only after resolving the exact retained
   normalized revision, validating its digest and validating the requested
   UTF-8 range and quote. Missing, corrupt, stale and unsupported material must
   fail closed.

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
supports the durable raw UTF-8 importer, revision-aware Original publication
and bounded lexical retrieval substrate. Snapshot-consistent MDBX admission,
structural product chunking, derived multi-view retrieval, byte-verified
hydration and install/export are separate gates. A roadmap contract or an API
sketch must not be reported as an implemented release capability.

The first release therefore remains a Technical Preview until the following
vertical evidence exists:

- normalized Markdown article: structural chunks, derived summary/concepts,
  retrieval and exact normalized-text citation;
- Telegram text with image references: source revision update, stale rejection,
  reopen and evidence-preserving locators;
- timed transcript: derived retrieval, exact text hydration and an external
  video time link without claiming retained video bytes.

ANN, graph traversal, embedded LLM inference, OCR/ASR connectors, late
chunking, complex rerankers and a universal agent coordinator are not required
for this profile.
