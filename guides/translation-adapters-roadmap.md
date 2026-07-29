# translation-adapters-roadmap.md

Roadmap for optional translation and cross-lingual retrieval support in
`agent-memory-cpp`. Translation is an adapter concern: the C++17 core defines
contracts and provenance metadata, while concrete engines such as Argos
Translate, cloud APIs or local model runtimes live outside the core library.
The dependency-free SPI and value types are part of the core/domain contract;
concrete engines, package managers and provider-specific routing live in
`adapters/` and `examples/`.

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
struct CanonicalLanguageCode {
    std::string bcp47; // canonical BCP-47, e.g. "en", "ru", "uk", "zh-Hant"
};

struct UnknownLanguage {};
struct MixedLanguage {};

using DetectedLanguage = std::variant<
    CanonicalLanguageCode,
    UnknownLanguage,
    MixedLanguage>;

enum class TranslationBackendMode : uint8_t { Disabled, OfflineAdapter, ExternalService };
enum class TranslationProjectionMode : uint8_t { Disabled, Persisted };

struct TranslationPolicy {
    TranslationProjectionMode projection_mode = TranslationProjectionMode::Disabled;
    TranslationBackendMode backend_mode = TranslationBackendMode::Disabled;
    CanonicalLanguageCode canonical_language;
    bool allow_pivot = true;
};

struct UnitSourceToken {
    KnowledgeUnitId unit_id;
    std::uint64_t unit_revision = 0;
};

struct ResourceSourceToken {
    ResourceId resource_id;
    std::uint64_t resource_generation = 0;
    std::array<std::uint8_t, 32> body_digest;
};

using TranslationSource = std::variant<UnitSourceToken, ResourceSourceToken>;

enum class FingerprintSubject : uint8_t {
    PackageArchive,
    ModelBundle,
    RemoteModelRevision,
    OpaqueUnpinned,
};

struct ArtifactFingerprint {
    FingerprintSubject subject = FingerprintSubject::PackageArchive;
    BlobDigest digest;
};

struct TranslationRequest {
    std::string text;
    std::optional<DetectedLanguage> source_language;
    CanonicalLanguageCode target_language;
    TranslationBackendMode backend_mode = TranslationBackendMode::Disabled;
    std::optional<TranslationSource> source;
};

struct TranslationStepProvenance {
    std::string provider_id;
    std::string package_id;
    std::string package_version;
    CanonicalLanguageCode from;
    CanonicalLanguageCode to;
    std::string provider_native_from;
    std::string provider_native_to;
    std::string model_id;
    ArtifactFingerprint fingerprint;
};

struct TranslationResult {
    std::string translated_text;
    DetectedLanguage detected_source_language;
    CanonicalLanguageCode target_language;
    std::vector<TranslationStepProvenance> steps;
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

`CanonicalLanguageCode` equality uses canonicalized BCP-47 bytes.
Canonicalization normalizes language to lower-case, script to TitleCase, region
to upper-case and resolves known aliases before profile signature or cache-key
calculation. `UnknownLanguage` and `MixedLanguage` are explicit
`DetectedLanguage` variants, not free-form strings and not valid target
languages.
Provider-native codes are stored separately in `TranslationStepProvenance`; an
Argos adapter owns the mapping table between provider codes such as `zt` and
core BCP-47 such as `zh-Hant`.
`TranslationRequest::source_language == nullopt` means the caller did not supply
detection evidence. `UnknownLanguage` means detection was attempted but produced
no stable language. `MixedLanguage` means the source spans multiple languages
and the adapter may reject, segment internally or use a provider-native mixed
mode, but must report the chosen steps explicitly.

## 4. Stored Metadata

Translated projections are regular `SearchProjection` records with
`ProjectionKind::TranslatedCanonical`. Translation provenance is revisioned
with the projection value, not stored as a unit-level component:

```cpp
struct ProjectionDerivationId {
    std::uint64_t source_revision = 0;
    std::uint64_t projection_generation = 0;
    std::array<std::uint8_t, 32> policy_fingerprint;
};

struct TranslationProjectionMeta {
    ProjectionDerivationId derivation_id;
    DetectedLanguage original_language;
    CanonicalLanguageCode canonical_language;
    std::vector<TranslationStepProvenance> steps;
    double quality_hint = 0.0;
    std::uint64_t translated_at_ms = 0;
};
```

Rules:

- `SearchProjection::Original` is always built from original/extracted text.
- `TranslatedCanonical` is built only when `TranslationProjection` is enabled.
- `TranslationProjectionMeta.derivation_id.source_revision` must match the
  source unit revision used to generate the projection.
- Changing canonical language, provider, model, package fingerprint or policy
  fingerprint creates a new `projection_generation` and makes only
  `TranslatedCanonical` stale.
- `SearchProjection`, `TranslationProjectionMeta` and lexical/vector index
  deltas commit together for the same generation.
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
    std::string package_version;
    CanonicalLanguageCode from;
    CanonicalLanguageCode to;
    std::string model_id;
    ArtifactFingerprint fingerprint;
    std::string local_path;
    std::string license;
    std::string source_index_uri;
    std::uint64_t installed_at_ms = 0;
};
```

The manifest is adapter-owned in M1. A future shared registry may be added only
if multiple adapters need common package discovery. Storage is ordinary metadata
or an adapter-local table; no new `mdbx-containers` primitive is required.

`ArtifactFingerprint` reuses the artifact profile's full algorithm-tagged
`BlobDigest`; it is not a third hash value type. For Argos, `fingerprint` is SHA-256 over the exact downloaded `.argosmodel`
archive bytes before unpacking (`subject = PackageArchive`). For external
services, reproducible provenance requires an immutable provider revision
(`subject = RemoteModelRevision`); otherwise the step is explicitly
`OpaqueUnpinned` and cannot be reported as reproducible.

## 6. Ingestion and Query Routing

Ingestion-time flow:

1. Import raw resource and create `SearchProjection::Original`.
2. Detect language during extraction/tokenization when possible.
3. If profile enables translation and the source language differs from the
   canonical language, call `ITranslationAdapter`.
4. Write `SearchProjection::TranslatedCanonical` with embedded
   `TranslationProjectionMeta` in the same coordinated write.
5. Reindex only the translated projection generation.

Query-time flow:

1. Detect query language when the planner has such an adapter.
2. Build a `ProjectionQueryVariant` for the original query against `Original`
   and regular curated projections.
3. If query language differs from canonical language and persisted translation
   is enabled, translate the query and build a second variant for
   `TranslatedCanonical`.
4. Run each lexical variant only against its matching projection kind and use
   projection-specific token/collection statistics.
5. Fuse original-query and translated-query streams through RRF.
6. Build final context from original source excerpts unless the caller requests
   translated snippets.

M1b may translate a query only as the direct matching query side for a
persisted `TranslatedCanonical` projection. General query-time-only translation,
corpus-language routing, pivot selection and cross-language stream fusion are
M2 planner contracts; they must not be implied merely by storing translations.

```cpp
struct QueryTranslationTrace {
    DetectedLanguage source_language;
    CanonicalLanguageCode target_language;
    std::vector<TranslationStepProvenance> steps;
    std::uint64_t translated_at_ms = 0;
};
```

`QueryTranslationTrace` belongs to the retrieval trace/query variant. It is not
persisted as `TranslationProjectionMeta`, because it describes the caller query,
not a stored projection generation.

`ProjectionQueryVariant` is defined in
[`lexical-search-roadmap.md`](lexical-search-roadmap.md) and is also carried by
`RetrievalPlan`.

Vector retrieval may use the original query when the embedding model declares
multilingual support. Otherwise the planner builds a translated vector query
variant with the same provenance trace as the lexical variant.

Pivot routing is allowed only when `TranslationPolicy::allow_pivot = true`.
Every pivot step is persisted in `TranslationProjectionMeta`.

## 7. Storage Boundary

This feature does not change the MDBX physical manifest beyond existing
extension points:

- translated text and provenance use `unit_projections`;
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
- package fingerprint in derived metadata;
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

M1b:

- Add `TranslationProjection` capability and `TranslationPolicy`.
- Add `ProjectionKind::TranslatedCanonical`.
- Add `TranslationProjectionMeta` inside the versioned projection value.
- Provide an example offline adapter or sidecar, with tests using a deterministic
  fake translator.

M2:

- Query-time language routing, query-time-only translation and explicit pivot
  selection.
- Cross-lingual retrieval eval set.
- Optional package registry helper for offline adapters.
- Translation drift report in `agent-memory-cli inspect/check`.

## 10. Evaluation

Minimum eval gates for translation support:

- retrieval Recall@K on mixed-language QA/document fixtures;
- citation correctness against original `SourceRef`;
- stale-projection behavior after source revision and package fingerprint changes;
- latency and storage overhead with and without translated projections;
- pivot-vs-direct quality delta tracked separately from core retrieval metrics.
- language canonicalization fixtures for aliases, script subtags, provider-native
  codes and unsupported/unknown languages.

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
