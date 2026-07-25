# translation-adapters-roadmap.md

Roadmap for optional translation and cross-lingual retrieval support in
`agent-memory-cpp`. Translation is an adapter concern: the C++17 core defines
contracts and provenance metadata, while concrete engines such as Argos
Translate, cloud APIs or local model runtimes live outside the core library.

## 1. Purpose

Many agent knowledge bases start as mixed-language raw documents: `.md`,
`.txt`, extracted `.pdf`, transcripts, playbooks, logs and curated cards.
Retrieval should work when the user asks in one language and the useful source
text is in another language.

The design goal is:

- keep original text authoritative for citation and export;
- allow optional canonical-language search projections;
- make translation model/package drift auditable;
- support offline translators without adding Python or ML runtime dependencies
  to the static library.

## 2. Non-Goals

- Do not make translation mandatory for BasicRag or AgentLTM.
- Do not add Argos Translate, Python, OpenNMT, SentencePiece or Stanza as core
  dependencies.
- Do not put translation package management into `mdbx-containers`.
- Do not treat translated text as a replacement for original source text.
- Do not guarantee quality for pivot translation. It is useful for recall, but
  remains lossy evidence.

## 3. Core Contract

The core exposes an optional adapter interface and stores derived metadata. The
adapter may be implemented as an in-process C++ plugin, a subprocess, a Python
sidecar or an external service.

```cpp
struct LanguageCode {
    std::string bcp47; // e.g. "en", "ru", "uk", "pt-BR"
};

enum class TranslationMode : uint8_t {
    Disabled,
    OfflineAdapter,
    ExternalService,
};

struct TranslationRequest {
    std::string text;
    std::optional<LanguageCode> source_language;
    LanguageCode target_language;
    TranslationMode mode = TranslationMode::Disabled;
    std::optional<ResourceId> resource_id;
    std::uint64_t source_revision = 0;
};

struct TranslationPivotStep {
    LanguageCode from;
    LanguageCode to;
    std::string model_id;
    std::array<std::uint8_t, 32> model_digest;
};

struct TranslationResult {
    std::string translated_text;
    LanguageCode detected_source_language;
    LanguageCode target_language;
    std::string provider_id;
    std::string model_id;
    std::array<std::uint8_t, 32> model_digest;
    std::vector<TranslationPivotStep> pivot_path;
    double quality_hint = 0.0;
};

class ITranslationAdapter {
public:
    virtual ~ITranslationAdapter() = default;
    virtual TranslationResult translate(const TranslationRequest& request) = 0;
};
```

The adapter contract is intentionally synchronous in the minimal sketch. The
runtime may schedule translation through `TaskQueue`/`JobDispatcher` when the
implementation reaches background ingestion jobs.

## 4. Stored Metadata

Translated projections are regular `SearchProjection` records with
`ProjectionKind::TranslatedCanonical`. Translation provenance lives in
`unit_components` as a typed component:

```cpp
struct TranslationMetaComponent {
    LanguageCode original_language;
    LanguageCode canonical_language;
    std::string provider_id;
    std::string model_id;
    std::array<std::uint8_t, 32> model_digest;
    std::vector<TranslationPivotStep> pivot_path;
    double quality_hint = 0.0;
    std::uint64_t translated_at_ms = 0;
    std::uint64_t source_revision = 0;
};
```

Rules:

- `SearchProjection::Original` is always built from original/extracted text.
- `TranslatedCanonical` is built only when `TranslationProjection` is enabled.
- `TranslationMetaComponent.source_revision` must match the source unit
  revision used to generate the projection.
- Changing canonical language, provider id, model id or model digest makes only
  `TranslatedCanonical` stale.
- Source citations point to `SourceRef` on the original resource, not to the
  translated projection.

## 5. Package Manifest Pattern

Offline translation engines need reproducible model/package identity. Borrow the
package-index idea from Argos Translate without copying its package format into
the core:

```cpp
struct TranslationPackageManifest {
    std::string provider_id;
    std::string package_id;
    LanguageCode from;
    LanguageCode to;
    std::string model_id;
    std::array<std::uint8_t, 32> model_digest;
    std::string local_path;
    std::string license;
    std::string source_index_uri;
    std::uint64_t installed_at_ms = 0;
};
```

The manifest is adapter-owned in M1. A future shared registry may be added only
if multiple adapters need common package discovery. Storage is ordinary metadata
or an adapter-local table; no new `mdbx-containers` primitive is required.

## 6. Ingestion and Query Routing

Ingestion-time flow:

1. Import raw resource and create `SearchProjection::Original`.
2. Detect language during extraction/tokenization when possible.
3. If profile enables translation and the source language differs from the
   canonical language, call `ITranslationAdapter`.
4. Write `SearchProjection::TranslatedCanonical` and
   `TranslationMetaComponent` in the same coordinated write.
5. Reindex only the translated projection.

Query-time flow:

1. Detect query language when the planner has such an adapter.
2. Search `Original` and regular curated projections first.
3. Add `TranslatedCanonical` when the corpus/query language mix benefits from
   cross-lingual recall.
4. Fuse results through the existing RRF/hybrid retrieval path.
5. Build final context from original source excerpts unless the caller requests
   translated snippets.

Pivot routing is allowed only when `TranslationPolicy::allow_pivot = true`.
Every pivot step is persisted in `TranslationMetaComponent`.

## 7. Storage Boundary

This feature does not change the MDBX physical manifest beyond existing
extension points:

- translated text uses `unit_projections`;
- metadata uses `unit_components` via `TypeDiscriminatedTable`;
- raw bodies stay in `ResourceBodyStore`;
- token/posting indexes stay projection-aware and scope-aware.

`mdbx-containers` remains responsible for generic typed tables, reverse
indexes, coordinated writes and optional blob/large-value primitives. It does
not know about language codes, translation engines, pivot paths or retrieval
policy.

## 8. Argos Translate Reference

Argos Translate is a useful pattern donor because it is offline-first and
package-based:

- Python library, CLI and GUI surfaces;
- OpenNMT-based translation pipeline;
- `.argosmodel` package files;
- separate package index repository with metadata/download links;
- automatic pivoting through intermediate languages when a direct model is not
  installed.

Borrowed ideas:

- explicit local package identity;
- package digest in derived metadata;
- pivot path as first-class provenance;
- offline adapter as a privacy-preserving option.

Rejected as direct dependency:

- Python runtime in C++ core;
- Argos package format as a normative `agent-memory-cpp` storage format;
- translation quality assumptions as retrieval correctness guarantees.

## 9. Milestones

M0:

- No translation dependency.
- Preserve `ChunkPayload.detected_language` where importers can provide it.

M1:

- Add `TranslationProjection` capability and `TranslationPolicy`.
- Add `ProjectionKind::TranslatedCanonical`.
- Add `TranslationMetaComponent`.
- Provide an example offline adapter or sidecar, with tests using a deterministic
  fake translator.

M2:

- Query-time language routing.
- Cross-lingual retrieval eval set.
- Optional package registry helper for offline adapters.
- Translation drift report in `agent-memory-cli inspect/check`.

## 10. Evaluation

Minimum eval gates for translation support:

- retrieval Recall@K on mixed-language QA/document fixtures;
- citation correctness against original `SourceRef`;
- stale-projection behavior after source revision and package digest changes;
- latency and storage overhead with and without translated projections;
- pivot-vs-direct quality delta tracked separately from core retrieval metrics.

## 11. References

- Argos Translate repository:
  `https://github.com/argosopentech/argos-translate`
- Argos Translate documentation:
  `https://argos-translate.readthedocs.io/en/latest/`
- Argos package index:
  `https://github.com/argosopentech/argospm-index`
- Canonical projection model:
  [`lexical-search-roadmap.md`](lexical-search-roadmap.md)
- Memory profile capability model:
  [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md)
