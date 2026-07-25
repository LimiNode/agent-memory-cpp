# Argos Translate Cross-Lingual Adapter Reference Review

## 2026-07-25

### Context

Reviewed `argosopentech/argos-translate` as a pattern donor for optional
cross-lingual ingestion and retrieval in `agent-memory-cpp`. The question was
whether to borrow anything for roadmap/TZ after adding first-class raw document
support.

### Source

- Repository: `https://github.com/argosopentech/argos-translate`
- Documentation: `https://argos-translate.readthedocs.io/en/latest/`
- Package index: `https://github.com/argosopentech/argospm-index`
- Inspected repository revisions:
  - `argos-translate`: `c72ec9040a580bc5f9ad4272f2c8a685d9bc66dd`
  - `argospm-index`: `ff90de60728f7c1338ff6b75974e4c89b2442d22`
- Inspected surfaces:
  - `argos-translate` README / public documentation
  - `argospm-index` package metadata
- Public inspection date: 2026-07-25

### Question

Can Argos Translate improve our roadmap without adding Python translation
runtime dependencies to the C++17 static library?

### Observed Upstream Behavior

- Argos Translate is an open-source offline translation project implemented as
  a Python library with CLI and GUI surfaces.
- The public README describes OpenNMT-based translation.
- Translation models are installed as package archives with the `.argosmodel`
  extension.
- A separate package-index repository provides package metadata and download
  links.
- The project can pivot through intermediate languages when a direct language
  pair is not installed.

### Local Design Inference

Do not adopt Argos Translate as a core dependency. It is useful as an optional
adapter or sidecar for local/offline deployments, but `agent-memory-cpp` should
remain a C++17 static library with no mandatory Python/ML runtime.

Borrow these patterns:

- explicit local translation package identity;
- model/package fingerprint in derived metadata;
- pivot path recorded as provenance;
- separation between package registry and runtime translation call;
- offline adapter mode for private local knowledge bases.

### Local Hardening Requirements

Argos package metadata is useful for routing, but package/model fingerprint is an
`agent-memory-cpp` reproducibility requirement. For Argos adapters, compute
SHA-256 over exact downloaded `.argosmodel` archive bytes before unpacking. For
external services, store immutable provider revision when available; otherwise
mark the derivation `OpaqueUnpinned` and do not report it as reproducible.

### Roadmap Impact

Added `guides/translation-adapters-roadmap.md` and cross-references from:

- `memory-stacks-roadmap.md`: new `TranslationProjection` capability,
  `TranslationPolicy`, ADR-017 and optional `TranslatedCanonical` projection;
- `translation-adapters-roadmap.md`: projection-level
  `TranslationProjectionMeta` and generic raw-document language/projection
  notes;
- `lexical-search-roadmap.md`: `ProjectionKind::TranslatedCanonical`, field
  mapping, build rules and retrieval-plan note;
- `mdbx-stack-boundaries.md`: translation execution remains outside
  `mdbx-containers`;
- `related-projects.md`: Argos Translate classified as a sister/pattern donor,
  not a competitor or core dependency.

### Follow-Up Checks

- Use a deterministic fake translator in tests before wiring any real adapter.
- Add mixed-language retrieval fixtures once projection-aware BM25F is
  implemented.
- Ensure final context builders cite original `SourceRef` unless a caller
  explicitly requests translated snippets.
- Track pivot-vs-direct quality deltas separately from storage/retrieval
  correctness metrics.
