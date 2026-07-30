# Lexical Search Roadmap

> **Scope.** This document details **Layer 2 (Retrieval Primitives)** from
> [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md). BM25F operates on
> the `SearchProjection` DBI (`unit_projections`) rather than on
> `envelope.search_text`. All posting keys below are
> `(scope_id, projection_kind, field_id, …)`-shaped to match the projection
> model defined in ADR-001 / ADR-005 of the stacks roadmap.
>
> Related roadmaps:
> - [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) — envelopes, components,
>   projections, profile/stack model, physical manifest ownership.
> - [`knowledge-base-roadmap.md`](knowledge-base-roadmap.md) — knowledge units,
>   `KnowledgeUnitId`, retrieval flow contracts.
> - [`knowledge-units-roadmap.md`](knowledge-units-roadmap.md) — per-kind payload
>   components (QAPayload, FactPayload, ChunkPayload, …).
> - `guides/research-reading-map.md` — research reading map backing the
>   SPLADE and ColBERT references in the §References section below.
>
> C++17 compliance: кодовые сниппеты используют `const std::vector<T>&` вместо
> `std::span` и явные конструкторы (`MyType x{...}`) либо сеттеры вместо
> designated initializers (`Type{ .field = value }`).

## Purpose

This guide captures the planned keyword, BM25, and BM25F lexical retrieval
direction. Lexical search is a first-version retrieval capability, not a
late optimization, because it handles exact technical terms better than dense
embeddings: APIs, macros, file paths, class names, error messages, command
fragments, and rare identifiers.

The baseline should be small, embeddable, and dependency-free. Larger search
engines and Unicode libraries can be studied or added later as optional
adapters.

## Core Rules

- Keep source and chunk text as canonical UTF-8.
- Do not store source text as UTF-32 by default, even when compression is
  enabled.
- Use UTF-32/code point buffers only temporarily inside tokenizer backends that
  need them.
- Keep lexical contracts dependency-free (backends may opt into ICU, simdutf,
  etc., but the public API stays std-only).
- Use `KnowledgeUnitId` as the primary retrieval key; `ChunkId` is a typed
  alias for chunk-shaped hits.
- All posting keys are scope-aware and projection-aware (see the
  `Lexical Projections And BM25F` section).
- BM25F indexes `SearchProjection` records, not `envelope.primary_text`. The
  flat-body baseline is a special case where `projection_kind = Original` and
  the only active `field_id` is `body`.
- Store `ResourceId` and explicitly named `resource_generation` with postings
  for targeted source reindexing, and the complete `ProjectionVersionRef` for
  projection freshness.
- Use `ResourceManifest` records for lexical postings so one resource can be
  reindexed without rebuilding the whole corpus.
- Keep BM25 as the first ranked lexical scorer; BM25F is the fielded
  generalisation and ships after the projection model is in place.
- Treat phrase, proximity, fuzzy, SPLADE, ColBERT-like, and graph retrieval as
  later layers unless a PR introduces focused contracts for them.
- Prefer RRF as the first hybrid fusion method because it does not require
  cross-backend score normalization.

## Keyword Search Versus BM25

Keyword search is the broad capability: tokenize a query, find matching tokens,
apply Boolean operators or filters, and return candidates. BM25 is the first
ranked scoring model for those keyword candidates.

Keep these concepts separate in the API:

```text
keyword matching:
    exact term lookup, Boolean AND/OR/NOT, phrase, proximity, filters

BM25 scoring:
    ranked score over matching chunks using term rarity, term frequency, and
    chunk length normalization
```

This separation matters because Boolean search, phrase search, and metadata
filters may be useful even when BM25 scoring is disabled or replaced by a later
BM25F/fuzzy/hybrid pipeline.

### Keyword-Overlap Tiny-KB Baseline

`KeywordOverlapIndex` is the deliberately small deterministic baseline for
unit-testable knowledge bases and tiny curated corpora. Its score is:

```text
score(q, d) = |unique_terms(q) ∩ unique_terms(d)|
```

It is not BM25: it does not use IDF, term-frequency saturation, or document
length normalization. Duplicate query terms and repeated document terms do not
increase the score beyond one match per unique term. Ties are resolved by
`chunk_id` so fixtures remain stable.

Use it for:

- tiny KBs, roughly 5-100 curated articles, where deterministic behaviour and
  zero infrastructure matter more than semantic recall;
- fast unit-test or oracle fixtures that should not depend on embeddings,
  vector stores, external services, or BM25 corpus statistics;
- a lower-bound lexical baseline in experiments.

Do not use it as the default production lexical scorer. Larger corpora,
multilingual corpora, long documents, user phrasing that diverges from KB
terminology, and hybrid retrieval experiments should use BM25/BM25F and/or
dense retrieval with measured `Recall@K`/`nDCG@K`.

## Lexical Data Model

The logical indexing pipeline is:

```text
SearchProjection (scope_id, unit_id, projection_kind, field_id)
    -> tokenizer
    -> normalized tokens
    -> token ids
    -> per-field postings and stats
    -> BM25F scoring (or BM25 for the flat-body baseline)
```

The retrieval key changes from the older plan. `ChunkId` survives only as a
typed alias for chunk-shaped hits; the canonical key is `KnowledgeUnitId`. Each
hit also carries the `projection_kind` it was scored under so downstream
fusion can disambiguate multi-projection sources.

### Value Types

The following `LexicalPosting` and `ChunkLexicalStats` shapes are the M1+
projection-aware persisted contract. The current C++ `agent_memory::LexicalPosting`
in `src/agent_memory/lexical/Lexical.hpp` is an M0 chunk-only baseline carrying
`ResourceRevision`; it does not yet claim `SearchProjection`, translation, or
summary-projection freshness. The M1+ contract supersedes that baseline only
when the projection layer is implemented.

```cpp
using TokenId = std::uint64_t;

enum class ProjectionKind : std::uint16_t {
    Original        = 1,
    DenseContextual = 2,
    QAQuestion      = 3,
    QAAnswer        = 4,
    Summary         = 5,
    CodeSymbols     = 6,
    TranslatedCanonical = 7,
    QACombined      = 8,  // M2+ dense-only Question + Answer experiment
};

enum class FieldId : std::uint16_t {
    title     = 1,
    heading   = 2,
    body      = 3,
    code      = 4,
    tag       = 5,
    symbol    = 6,
    meta      = 7,
    qa_q      = 8,
    qa_a      = 9,
    summary   = 10,
};

// CanonicalLanguageCode, DetectedLanguage and QueryTranslationTrace are common
// dependency-free domain contracts owned by translation-adapters-roadmap.md.
// Lexical retrieval consumes them and must not redeclare or re-encode them.

struct ProjectionQueryVariant final {
    ProjectionKind projection_kind = ProjectionKind::Original;
    DetectedLanguage query_language = UnknownLanguage{};
    std::string text;
    std::optional<ProjectionDerivationId> target_projection_derivation;
    std::optional<QueryTranslationTrace> query_translation;
};

`query_language` records detection, not an asserted target language. A caller
without a detector uses `UnknownLanguage`; a mixed natural-language/code query
uses `MixedLanguage`. Neither variant is serialized as a fake BCP-47 value or
used as a target for persisted translation without an explicit routing policy.

struct LexicalPosting final {
    TokenId token_id = 0;
    KnowledgeUnitId unit_id;
    ProjectionKind projection_kind = ProjectionKind::Original;
    FieldId field_id = FieldId::body;
    ResourceId resource_id;
    std::uint64_t resource_generation = 0;
    ProjectionVersionRef projection_version;
    std::uint32_t term_frequency = 0;
    std::vector<std::uint32_t> positions;
};

struct ChunkLexicalStats final {
    KnowledgeUnitId unit_id;
    ResourceId resource_id;
    std::uint64_t resource_generation = 0;
    ProjectionVersionRef projection_version;
    std::uint32_t token_count = 0;
    std::uint32_t unique_token_count = 0;
    std::array<std::uint32_t, 11> per_field_token_count{}; // index by FieldId
};

struct LexicalTokenStats final {
    TokenId token_id = 0;
    ProjectionKind projection_kind = ProjectionKind::Original;
    std::uint64_t document_frequency = 0;
    std::uint64_t collection_frequency = 0;
};

// A durable non-reused identifier for one lexical-index incarnation.
struct LexicalStatisticsEpoch final {
    std::array<std::uint8_t, 16> bytes{};
};

// Identifies one atomically readable active-projection statistics view.
struct LexicalStatisticsSnapshotRef final {
    ScopeId scope_id;
    ProjectionKind projection_kind = ProjectionKind::Original;
    LexicalStatisticsEpoch statistics_epoch;
    std::uint64_t statistics_generation = 0;
};

struct LexicalCollectionStats final {
    ProjectionKind projection_kind = ProjectionKind::Original;
    LexicalStatisticsSnapshotRef snapshot;
    std::uint64_t unit_count = 0;
    std::uint64_t total_token_count = 0;
};
```

BM25F only needs per-field term frequency and per-field token count. Positions
are included in the roadmap because phrase search, proximity search, snippets,
and highlighting need token order.

The first implementation may expose two storage modes:

```text
BM25F-only:
    postings store (unit_id, projection_kind, field_id, tf)

positional:
    postings store (unit_id, projection_kind, field_id, tf, positions)
```

The public value types should not prevent positions, but a backend should be
allowed to omit positions until phrase/proximity search is enabled.

## Token Dictionary

Token strings should be normalized before they receive ids:

```text
lexical_token_by_text:
    (scope_id, normalized_token) -> token_id

lexical_token_by_id:
    (scope_id, token_id) -> normalized_token
```

The reverse dictionary is not required for scoring, but it is useful for
debugging, index dumps, explain output, and relevance diagnostics.

### Token Id Lifecycle

`TokenId` values are index-local stable identifiers. The first implementation
should allocate them monotonically and avoid reusing ids during normal updates.
This keeps posting references, diagnostics, and future segment/tombstone logic
simple.

Deletion of the last posting for a token does not have to immediately remove the
token dictionary entry. A later compaction pass can reclaim unused tokens after
checking token stats and posting segments.

Stats updates must keep these counters coherent (per `projection_kind`):

```text
document_frequency:
    number of units containing the token in that projection_kind

collection_frequency:
    total token occurrences across indexed units in that projection_kind

unit_count:
    number of units with lexical stats for that projection_kind

total_token_count:
    sum of token_count across indexed units in that projection_kind
```

`ProjectionVersionRef` is mandatory for per-unit postings and stats, while a
`LexicalStatisticsSnapshotRef` identifies one aggregate view across many live
units. They are deliberately different: a collection cannot carry one
`ProjectionVersionRef` because each member may have its own active projection
version.

When a resource or one of its projections is reindexed, the indexing
transaction removes the previous **active** contribution from
`LexicalTokenStats` and `LexicalCollectionStats`, applies the replacement
contribution, advances `statistics_generation` within the current
`statistics_epoch`, and publishes the replacement lexical manifest together.
Physical stale postings may remain until delayed
compaction, but they must not remain in aggregate stats. Consequently `df`,
`collection_frequency`, `unit_count`, `total_token_count`, and `avgdl` always
describe the same active-projection set as the manifest visible in the read
frontier.

`LexicalTokenStats` rows are current members of the collection snapshot; they
are not independently stamped or copied on every unrelated unit update. A
query reads token rows, `LexicalCollectionStats`, and the active manifest from
one storage read transaction/frontier. A token-stat cache is scoped to that
frontier or the full `LexicalStatisticsSnapshotRef`; it must not combine token rows from one
aggregate view with collection counters from another.

A **token-level cache** keyed by `(scope_id, token_id, projection_kind,
statistics_epoch, statistics_generation)` keeps warm-up reads cheap within a stable lexical
session without mixing aggregate views.

`statistics_epoch` is generated durably and never reused for a different
lexical-index incarnation. A full rebuild, posting-block layout migration, or
restore that replaces an existing derived lexical index must allocate a new
epoch before publishing its manifest, even when its generation counter begins
at zero. A backup that restores the exact same immutable derived index without
coexisting stale blocks may preserve its epoch; any restore into a workspace
that can retain older blocks must instead allocate a new epoch or delete those
blocks before publication. Cache keys and block headers carry the full
snapshot ref. An epoch mismatch disables/recomputes pruning bounds rather than
allowing a coincidentally equal generation to reuse stale statistics.

## Tokenization

The first tokenizer should be std-only and code-aware:

```text
ExactVectorIndex      -> exactvectorindex, exact, vector, index
AGENT_MEMORY_HAS_MDBX -> agent_memory_has_mdbx, agent, memory, has, mdbx
src/agent_memory/...  -> src, agent_memory, agent, memory, ...
error 11001           -> error, 11001
```

Near-term tokenizer contracts should model:

- normalized token text;
- token position;
- source byte range;
- token kind where useful, for example word, number, path, identifier, symbol.

Optional tokenizer backends can be introduced later:

- `simdutf` for fast UTF-8 validation and UTF-8/UTF-32 transcoding;
- `utfcpp` for lightweight scalar UTF-8 validation, conversion, and code point
  iteration when SIMD support is unnecessary;
- ICU for normalization, Unicode case folding, word boundary segmentation,
  and optional Cyrillic morphology (see the Cyrillic Morphology section
  below).

Libraries such as `tiny-utf8` are useful references, but the core tokenizer
contract should remain project-owned. Do not replace public `std::string` text
payloads with a custom UTF string type.

### Query Parsing

The first query parser can be simple: tokenize free text with the same tokenizer
used for indexing. Later parsers can add syntax for:

```text
"quoted phrases"
term1 AND term2
term1 OR term2
term1 NOT term2
exact NEAR/5 index
field:path guides/
metadata:language=cpp
projection:summary summary-of-x
```

Query parsing should stay separate from tokenization so programmatic callers can
construct structured queries without round-tripping through a textual query
language. Structured queries can pin a `projection_kind` to bias the scorer
towards `Summary`, `QAQuestion`, or `CodeSymbols` without changing the parser
API.

## UTF Storage Rule

Canonical persisted text is UTF-8:

```text
SearchProjection.text  -> UTF-8, optionally compressed later
envelope.primary_text  -> UTF-8, optionally compressed later
tokenization          -> temporary UTF-32/code points only when needed
index storage         -> token ids, stats, postings, and positions
```

Zstd may compress UTF-32 zero bytes well, but decompression still produces a
larger working buffer and worse cache behavior. Compression should be benchmarked
on UTF-8 text, normalized text, tokens, and posting lists before any alternative
text storage format is used.

## BM25 Baseline

Use Okapi BM25 as the first ranked lexical scorer for the
`projection_kind = Original, field_id = body` path:

```text
score(unit, query) =
sum over query terms:
    idf(term) * tf_saturation(term, unit) * length_normalization(unit)
```

Default tunables:

```text
k1 = 1.5
b  = 0.75
```

The index should expose these as options instead of hard-coding them into the
public contract.

BM25 scoring inputs come from the lexical index:

```text
tf      = term frequency in the unit body
df      = number of units containing the token (Original projection)
doc_len = token_count for the unit body
avgdl   = total_token_count / unit_count
```

Use the common Okapi IDF variant as the first baseline:

```text
idf = log(1 + (unit_count - df + 0.5) / (df + 0.5))
```

If a query repeats a term, the first implementation may either de-duplicate
query terms or count query term frequency explicitly. Pick one behavior and test
it before exposing tuning options.

See [`usage-memory-models.md`](usage-memory-models.md) for a comparison of memory-only architectures (block-of-facts vs RAG vs hybrid).

## Search Contracts

Planned dependency-free contracts:

```cpp
struct LexicalQuery final {
    std::string text;
    std::vector<ProjectionQueryVariant> query_variants;
    std::vector<ScopeId> scope_ids;
    std::optional<ProjectionKind> pin_projection_kind; // nullopt = stack default
    std::size_t limit = 10;
    std::vector<MetadataFilter> metadata_filters;
};

struct LexicalSearchResult final {
    KnowledgeUnitId unit_id;
    ChunkId chunk_id{}; // empty unless the hit is chunk-shaped
    KnowledgeUnitKind kind = KnowledgeUnitKind::Unknown;
    ProjectionKind projection_kind = ProjectionKind::Original;
    double score = 0.0;
    std::string retriever_name = "lexical";
    Metadata metadata;
};

class ILexicalIndex {
public:
    virtual ~ILexicalIndex();

    [[nodiscard]] virtual std::size_t size() const noexcept = 0;

    virtual void upsert(
        const KnowledgeUnitRevision& revision,
        const std::vector<SearchProjection>& projections
    ) = 0;

    [[nodiscard]] virtual std::vector<LexicalSearchResult> search(
        const LexicalQuery& query
    ) const = 0;

    [[nodiscard]] virtual std::vector<LexicalSearchResult> search(
        const RetrievalPlan& plan
    ) const = 0;

    [[nodiscard]] virtual bool erase_resource(
        const ResourceId& resource_id
    ) = 0;

    virtual void clear() = 0;
};
```

The `search(RetrievalPlan)` overload participates in the unified retrieval
pipeline from `memory-stacks-roadmap.md` section 7.3: it surfaces
`retriever_name = "lexical"` on every hit for RRF, emits a `RetrievalTrace`
segment per call, and applies the `RetrievalPlan.metadata_filter` and
`scope_ids` constraints before scoring. The flat `LexicalQuery` overload stays
for callers that want a standalone lexical lookup without a full plan.

Exact method names can change during implementation. The important part is that
lexical indexing is unit-aware, projection-aware, and plan-aware.

## MDBX Layout

The lexical DBI set is part of the stack-wide layout described in
[`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md) §5.5. The DBI
names and key shapes from that section are normative; this section adds the
payload shapes and explains the design.

```text
inverted_token_to_unit:          // DUPSORT secondary index
    key   = (scope_id, token_id, projection_kind, field_id) -> DUPSORT unit_id

field_to_postings:                 // KV posting stats by full posting identity
    key   = (scope_id, projection_kind, field_id, token_id, unit_id)
    value = PostingStats { tf, positions_count, resource_generation, projection_version, resource_id }

lexical_token_by_text:
    key   = (scope_id, normalized token)
    value = token_id

lexical_token_by_id:
    key   = (scope_id, token_id)
    value = normalized token

lexical_chunk_stats:
    key   = (scope_id, unit_id, projection_kind)
    value = { token_count, unique_token_count,
              per_field_token_count[11], resource_id, resource_generation,
              projection_version }

lexical_token_stats:
    key   = (scope_id, projection_kind, token_id)
    value = { document_frequency, collection_frequency }

lexical_collection_stats:
    key   = (scope_id, projection_kind)
    value = { statistics_epoch, statistics_generation, unit_count,
              total_token_count }
```

The first implementation may collapse `field_to_postings` to a single
`(scope_id, Original, body, token_id, unit_id)` row while keeping the schema
ready for the other projections and fields. That keeps the BM25-only path fast
without locking the schema.

### Posting Segments

Large posting lists should eventually move from one blob per token to segmented
storage:

```text
lexical_posting_segments:
    key   = (scope_id, projection_kind, field_id, token_id, segment_id)
    value = posting segment blob
```

Segments allow resource updates to touch fewer bytes and make tombstone cleanup
more incremental. The simple blob layout is still the right first backend
because it is easier to test against the in-memory BM25F baseline.

### Compressed Posting Blocks And Dynamic Pruning (M2)

The M2 physical segment remains a derived lexical representation, not a second
source of truth for a unit or its current lifecycle. Its versioned layout may
contain:

```text
[header: scoring/statistics generation, record count, safe upper bounds]
[delta-coded unit ids]
[term frequencies and field statistics]
[optional per-document delta-coded positions]
[skip offsets]
[projection-version/resource-generation data]
[optional conservative zone synopsis]
```

Unit ids and positions are candidates for delta plus bounded integer packing
(VByte, SIMD-BP128, Stream VByte, FastPFOR or an equivalent codec); term
frequencies may use bit packing; repeated projection-version/source fields may use a
local dictionary or RLE. No particular third-party wire format is a public
contract. A segment that cannot safely use a narrow delta representation must
use a lossless wide fallback.

Each compressed posting block that exposes WAND/BMW-style score bounds records
the full `LexicalStatisticsSnapshotRef` used to construct those bounds. A query
may use a bound for pruning only when it exactly matches the
`LexicalCollectionStats.snapshot` read at the same frontier. On epoch or
generation mismatch, the backend recomputes a conservative bound or disables
bound-based pruning for that block and preserves exhaustive BM25/BM25F
correctness.

Dynamic pruning is introduced in stages: exhaustive DAAT BM25F baseline first,
then MaxScore/WAND, then Block-Max WAND only after the per-block score upper
bounds are proved conservative for the active BM25F formula, field weights and
collection-statistics generation. A stale bound may reduce pruning efficiency,
but it must never discard a document that exhaustive BM25F could return in
top-K. Segment zone synopses may skip a block only for immutable or
generation-bound predicates; mutable lifecycle/authority/revision decisions
continue through `CandidateSet` and final canonical validation.

Before scoring or position decode, lexical candidates intersect the same
execution-local `CandidateSet` used by dense, graph and temporal routes. This
pushes exact scope/authority/lifecycle/time/source filters ahead of expensive
work without treating the set as a substitute for stale-posting checks.

Acceptance compares each mode with exhaustive BM25F on identical corpus and
filters. It reports nDCG/Recall, decoded posting integers and position bytes per
query, posting blocks touched, documents fully scored, CandidateSet filtering
rate, encoded/decoded I/O, p50/p95/p99 latency, update/delete/compaction cost,
and corrupt/obsolete-statistics negative cases.

## Targeted Reindexing

When a unit is reindexed for a given `projection_kind`:

```text
1. Load the old unit manifest (per projection_kind).
2. Remove or invalidate lexical postings referenced by the old manifest.
3. Load the new SearchProjection set for the unit.
4. Tokenize each (projection_kind, field_id) text.
5. Upsert token dictionary entries (shared across projection_kinds).
6. Write `field_to_postings` KV entries and `inverted_token_to_unit`
   candidate-index entries.
7. Remove the replaced active contribution, then update `lexical_token_stats`
   and `lexical_chunk_stats` per projection_kind.
8. Update `lexical_collection_stats` and advance its
   `statistics_generation` within the active `statistics_epoch` for the
   replacement active-projection set.
9. Write new lexical manifest refs with the active `projection_version` and
   applicable `resource_generation`.
10. Commit through a backend transaction where available.
```

Lexical `DerivedRecordRef` entries can use:

```text
kind = LexicalPosting
key  = (scope_id, projection_kind, field_id, token_id) or an encoded
       posting segment key
```

The manifest does not need stable offsets into compressed posting blobs. Source
reclamation filters by `unit_id`, `resource_id`, and `resource_generation`.

For mutable memory, a backend may leave stale postings in place. Query-time
freshness first compares `posting.projection_version` with the active projection
version, then checks `resource_generation` when the posting was derived from a
resource-managed record. The resource-generation path needs a cheap current
generation lookup or cache; otherwise stale filtering can turn lexical search
into too many random reads.

### Stale Filter via ProjectionVersionRef

LexicalPosting stores the complete `ProjectionVersionRef` captured at indexing.
Retrieval-time stale-filter:
  1. Posting lookup → candidates.
  2. Bulk load current envelopes.
  3. Skip unless `posting.projection_version` equals the active projection version.

См. также: ResourceManifest.generation filtering только для derived records от source resource (M2+).

Projection-version filtering is a per-posting check, not a separate DBI.
`projection_version_index` (optional, M2+) is a separate DBI for batch
validation and debugging.

The reindex path is **projection-aware**: an update that only changes a
`Summary` projection does not invalidate `Original`, `QAQuestion`, or
`QAAnswer` postings. The `projection_kind` participates in the staleness key
so unrelated projections stay live.

## Raw Resource Stores

The project should support raw source corpora as first-class inputs:

```text
.md
.txt
source code
structured records
later: PDF and office document adapters
```

Planned contract shape:

```cpp
class IResourceStore {
public:
    virtual ~IResourceStore();

    [[nodiscard]] virtual std::vector<ResourceDescriptor> list_resources() const = 0;

    [[nodiscard]] virtual std::optional<ResourceSnapshot> read_resource(
        const ResourceId& resource_id
    ) const = 0;
};
```

Initial adapters should focus on file-system resources:

```text
FileSystemResourceStore
    root_path
    include globs: *.md, *.txt, *.cpp, *.hpp
    exclude globs: .git, build, node_modules
    stable ResourceId from connector/provider identity, persisted locator
        registry, explicit application mapping, or operator-confirmed rename
    first-seen files without provider identity receive a ResourceId from the
        application registry, never from their relative path or content hash
    relative path is recorded as a SourceLocator observation only
    content hash for change detection
```

PDF and office document support should be separate parser adapters. They should
produce UTF-8 resource text and metadata rather than forcing lexical indexing to
understand PDF/docx internals. The same indexing pipeline should support two
source modes:

```text
raw-first mode:
    files are the source of truth; indexes are derived

memory-native mode:
    agent-created notes/facts live in storage; indexes are still derived
```

## Lexical Projections And BM25F

BM25F is the first BM25 variant because agent-memory resources naturally
have fields. The data is stored in projection-aware form from day one
so the BM25F scorer is a pure scoring change on top of the existing
`SearchProjection` DBI, not a migration.

### SearchProjection Model

A `SearchProjection` is a per-unit text view optimized for one retrieval
method:

```cpp
struct SearchProjection final {
    ScopeId scope_id;
    KnowledgeUnitId unit_id;
    ProjectionKind kind = ProjectionKind::Original;
    ProjectionVersionRef version;

    // Canonical fielded text. Empty fields are skipped at index time.
    std::string title;
    std::string heading;
    std::string body;
    std::string code;
    std::vector<std::string> tags;
    std::vector<std::string> symbols;
    TypedMetadata metadata_typed;
    std::string qa_question;   // populated when kind = QAQuestion
    std::string qa_answer;     // populated when kind = QAAnswer
    std::string summary;       // populated when kind = Summary
    std::optional<TranslationProjectionMeta> translation_meta; // TranslatedCanonical only
};
```

The projection is persisted in `unit_projections`
(`mdbx-containers-extension-tz.md` §5.5). The flat-body BM25 baseline is the
special case where `kind = Original` and only `body` is non-empty; BM25F
generalises it by reading additional fields from the same projection.

### Field Model Per ProjectionKind

Each `ProjectionKind` consumes a subset of the `FieldId` slots. The mapping
below is normative for BM25F scoring and for the post-fusion secondary
retrieval path.

| ProjectionKind   | Active Field IDs (weights)                                                     |
|------------------|---------------------------------------------------------------------------------|
| Original         | title (w_title), heading (w_heading), body (w_body), code (w_code), tag (w_tag), symbol (w_symbol), meta (w_meta) |
| DenseContextual  | body (w_body), meta (w_meta), summary (w_summary)                               |
| QAQuestion       | qa_q (w_qa_q), tag (w_tag)                                                      |
| QAAnswer         | qa_a (w_qa_a), body (w_body)                                                    |
| Summary          | summary (w_summary), tag (w_tag), title (w_title)                               |
| CodeSymbols      | symbol (w_symbol), code (w_code)                                                |
| TranslatedCanonical | body (w_body), tag (w_tag), meta (w_meta) |

`DenseContextual` is consumed by the dense retriever; BM25F still indexes the
projection so secondary lexical retrieval can fall back to it after fusion.

### Posting Keys With Projection Kind

Postings are projection-aware. The previous draft keyed postings only by
`(token_id, field_id)`; the new model adds `projection_kind` and `scope_id` so
weights and stats do not bleed across projection families or tenants:

```text
inverted_token_to_unit:
    key   = (scope_id, token_id, projection_kind, field_id)
    value = DUPSORT unit_id

field_to_postings:                 // KV posting stats by full posting identity
    key   = (scope_id, projection_kind, field_id, token_id, unit_id)
    value = PostingStats { tf, positions_count, resource_generation, projection_version, resource_id }
```

A `unit_id` may appear multiple times for the same token — once per
projection_kind/field_id combination that contains it. The scorer joins these
postings at query time using the per-field weights defined below. Targeted
update/delete of one posting uses the full `(scope_id, projection_kind,
field_id, token_id, unit_id)` key; `PostingStats` never carries identity.

### Projection-Weighted BM25F Scoring

The BM25F score for a unit `u` under query `q` is:

```text
score(u, q) = sum over query terms t:
    idf(t) *
    sum over fields f that hold t in the active projection_kind:
        w_f * tf_sat(t, u, f)
    /
    1 - b + b * len_norm(u)

where:
    idf(t)              = log(1 + (N - df(t) + 0.5) / (df(t) + 0.5))
    df(t)               = units containing t in the active projection_kind stats
    N                   = unit_count for the active projection_kind
    tf_sat(t, u, f)     = tf(t, u, f) * (k1 + 1) /
                          (tf(t, u, f) + k1 *
                           (1 - b + b * len_f(u) / avg_len_f))
    len_f(u)            = per_field token count from ChunkLexicalStats
    avg_len_f           = average len_f over units with that field
    w_f                 = LexicalFieldWeights entry for FieldId f
```

Per-field weights are explicit index options, applied at score time:

```cpp
struct LexicalFieldWeights final {
    double w_title   = 3.0;
    double w_heading = 2.5;
    double w_body    = 1.0;
    double w_code    = 1.5;
    double w_tag     = 2.0;
    double w_symbol  = 2.0;
    double w_meta    = 0.5;
    double w_qa_q    = 4.0;
    double w_qa_a    = 1.5;
    double w_summary = 1.5;
};
```

The default `LexicalFieldWeights` differ per projection_kind in spirit but the
scorer reads the same struct: when `kind = QAQuestion` the only active fields
are `qa_q` and `tag`, so the rest of the weights have no effect on that
projection. Changing weights never requires re-indexing; the weight is applied
at score time, not stored in each posting.

#### Worked Example

A QAPair unit `u` has two projections:

- `QAQuestion`: `qa_q = "how does cyrillic morphology help retrieval?"`,
  `tags = ["russian", "morphology"]`.
- `Original`: `title = "Cyrillic Morphology"`, `body = "Lemmatization
  collapses word forms …"`.

Query: `"cyrillic morphology"`.

For `QAQuestion`, the active fields are `qa_q` (w=4.0) and `tag` (w=2.0).
`cyrillic` and `morphology` appear in `qa_q`; the tag field contributes no
matching tokens. The BM25F score is dominated by `w_qa_q = 4.0`.

For `Original`, the same tokens appear in `body` with `w_body = 1.0` and
`title` (one occurrence) with `w_title = 3.0`. The Original projection score
is materially smaller than the QAQuestion score for the same unit.

When the retrieval plan requests both projection_kinds, RRF combines the two
ranked lists and the unit floats to the top from both sides — exactly the
behavior expected for a `QAKnowledgeBase` stack.

### Projection Build Rules Per ProjectionKind

Each `ProjectionKind` has a deterministic generation rule applied at write
time. The rule is part of the projection adapter and is documented next to
the kind enum:

```text
Original:
    title   = envelope.title (if any)
    heading = envelope.heading_path joined by newline
    body    = envelope.primary_text (or chunk payload body)
    code    = chunk.payload.code_blocks joined
    tags    = envelope.tags
    symbols = chunk.payload.symbols
    meta    = envelope.metadata_typed

QAQuestion:
    qa_q    = QAPayload.question
    tag     = QAPayload.tags ∪ envelope.tags

QAAnswer:
    qa_a    = QAPayload.answer
    body    = QAPayload.answer (when question-only answer is empty)

Summary:
    summary = CompiledArticlePayload.summary (or projection-specific summary)
    tag     = envelope.tags
    title   = envelope.title

CodeSymbols:
    symbol  = chunk.payload.symbols
    code    = chunk.payload.code_blocks joined

DenseContextual:
    body    = envelope.primary_text
    meta    = envelope.metadata_typed
    summary = nearest Summary projection if present, else empty

TranslatedCanonical:
    body    = translated extracted text or chunk body
    tags    = language-neutral tags only
    meta    = canonical_language + translation derivation fingerprint
```

`TranslatedCanonical` is generated only when the profile enables
`TranslationProjection` and supplies a `TranslationPolicy`. Its source of
truth is the original resource/unit revision plus `TranslationProjectionMeta`
embedded in the projection value. If the source revision, canonical language,
translation policy fingerprint or package/model fingerprint changes, only the
translated projection gets a new `ProjectionVersionRef.derivation_generation`;
`Original` postings remain valid. Translated projection postings store the full
version in `PostingStats.projection_version`, and stale-check compares it with
the active projection version, not only with `KnowledgeUnitEnvelope.revision`. See
[`translation-adapters-roadmap.md`](translation-adapters-roadmap.md).

Open question 17.4 from `memory-stacks-roadmap.md` (`When does a SearchProjection
regenerate?`) is resolved for the lexical path: projections regenerate on
write for the active revision. Old revisions stay until compaction purges them.
Targeted reindexing is responsible for the BM25F posting refresh.

### Targeted Reindexing Per Projection Kind

The reindex pipeline in the Targeted Reindexing section above is parameterised
by `projection_kind`. The additional rules are:

- A `SearchProjection` write for `kind = Summary` triggers reindexing of
  `(Summary)` projections only. `Original`, `QAQuestion`, and `QAAnswer`
  postings are untouched unless their source text changed.
- A `QAPayload` write triggers reindexing of both `QAQuestion` and `QAAnswer`
  in the same transaction (atomic with the unit write).
- A `ChunkPayload` write triggers reindexing of `Original` and `CodeSymbols`
  for that chunk-shaped unit.
- `DenseContextual` is owned by the dense retriever; BM25F only reindexes it
  when the dense retriever hands a new projection back to the lexical pipeline.

This keeps reindex cost proportional to the change rather than to the full
unit.

## Future Search Layers

See [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) for broader retrieval techniques beyond BM25: learned sparse (SPLADE), late-interaction (ColBERT), hybrid PRF interpolation timing, contextual retrieval, adaptive routing, graph-based retrieval, RLM.

### Boolean Search

Support `AND`, `OR`, and `NOT` as an exact filtering layer. This is useful for
technical queries, user constraints, and structured query builders. Boolean
search works on the same `field_to_postings` table as BM25F; the projection_kind
filter is just another predicate in the boolean expression.

### Phrase And Proximity Search

Use token positions to support exact phrases and near-term windows:

```text
"exact vector index"
exact NEAR/5 index
```

This should also support snippets and highlighting. Phrase/proximity search
needs positions, so backends that omit positions until phrase support lands
must clearly advertise the limitation.

### Fuzzy Search

Add fuzzy search after the BM25F baseline. Initial candidates:

```text
Levenshtein distance
Damerau-Levenshtein distance
trigram similarity
BK-tree over token dictionary
```

Technical identifiers need careful thresholds because a one-character change can
mean a different symbol. Fuzzy search is applied per-field per
projection_kind; the result list is merged with the exact-match list using RRF.

### N-Gram Search

N-gram or substring search is useful for identifiers, partial paths, and unknown
languages. It should be a separate index or optional mode because it increases
storage size and noise.

### Graph Retrieval

Graph retrieval is likely needed earlier than learned sparse or late-interaction
methods for agent assistants and expert-system style workflows. It should expand
from retrieved units/resources to related symbols, concepts, people, tasks, and
dependencies. Graph retrieval operates on `graph_edges_by_src` /
`graph_edges_by_dst` from `mdbx-containers-extension-tz.md` §5.5 and joins
its hits into the RRF stream with `retriever_name = "graph"`.

### SPLADE-v2 Learned Sparse Adapter (M2+)

Reference: arXiv:2107.05720 — "SPLADE: Sparse Lexical and Expansion Model for First Stage Ranking".

Принцип: learned sparse retrieval — термин-веса предсказываются MLM-style, совместимы с inverted index.

Архитектура в C++:
  - Внешний inference backend (Python/PyTorch модель).
  - Или ONNX runtime для embedded inference.
  - Output: sparse vector (term_id → weight), query expansion.

```cpp
class ILearnedSparseAdapter {
public:
    virtual SparseVector encode(const std::string& text) const = 0;
};
```

Pipeline:
  1. Query → SPLADE → sparse vector.
  2. SPLADE output → inverted index lookup (overlap с BM25F).
  3. Combined with BM25F: hybrid lexical score.

Status: M2+ optional external backend.

### ColBERT Late Interaction Adapter (M2+)

Reference: arXiv:2004.12832 — "ColBERT: Efficient and Effective Passage Search via Contextualized Late Interaction over BERT".

Принцип: token-level embeddings per chunk, late interaction при query time. ColBERTv2 уменьшает footprint на 6-10x.

Архитектура в C++:
  - Multi-vector per unit (token-level embeddings).
  - Storage: vector_per_token × N tokens per unit.
  - Query time: max-similarity aggregation.

```cpp
class ILateInteractionAdapter {
public:
    virtual std::vector<TokenEmbedding> encode(const ChunkPayload& chunk) const = 0;
    virtual float late_interact(
        const std::vector<TokenEmbedding>& query,
        const std::vector<TokenEmbedding>& doc) const = 0;
};
```

Storage estimate: 1M units × 32 tokens × 128 dim float32 = ~16 GB (raw).
С ColBERTv2 compression: ~1.6-2.7 GB.

Status: M2+ optional, дорого по памяти. Для high-precision use cases.

The M2+ storage contract is a `VectorSetProjection`: canonical unit/projection
identity, embedding-space and modality descriptor, pooling rule,
`max_vectors_per_unit`, encoded vector-set payload, and token/sentence/patch
locators. Coarse unit retrieval precedes bounded MaxSim over the selected vector
sets. Equal dimension is not sufficient compatibility: embedding space,
preprocessing, pooling and vector-set layout must match. Residual compression
and late-interaction ANN remain optional adapters evaluated against a
single-vector exact baseline and the global `RetrievalIoBudget`.

## Hybrid Retrieval

The first hybrid pipeline should be:

```text
metadata filters
    -> BM25F / keyword search (per active projection_kind from RetrievalPlan)
    -> vector search (per active projection_kind from RetrievalPlan)
    -> optional graph expansion
    -> fusion (RRF default)
    -> optional rerank
```

The `RetrievalPlan` from `memory-stacks-roadmap.md` section 7.3 drives which
projection_kinds each retriever runs against. The default mapping for
`AgentLongTermMemory` is:

```text
BM25F active projection_kinds  = { Original, QAQuestion, QAAnswer, Summary }
vector active projection_kinds = { Original, DenseContextual }
graph  active projection_kinds  = { Original }
```

When `TranslationProjection` is enabled, planners may add
`TranslatedCanonical` to BM25F/vector projection sets for cross-lingual recall,
but lexical search must use a translated `ProjectionQueryVariant` for that
projection. The original query is searched against `Original`; the translated
query is searched against `TranslatedCanonical`; RRF fuses the two result
streams. The default result context still carries `SourceRef` to the original
resource; translated snippets are auxiliary unless the caller explicitly asks
for a translated answer surface.
`ProjectionQueryVariant::target_projection_derivation` is set only when the
planner pins the query to a known persisted translated projection generation.
`query_translation` records query-side provenance and must not be copied into
stored projection metadata.

Secondary retrieval (post-fusion) may load `CodeSymbols` or `DenseContextual`
if RRF confidence is below a threshold or if the query parser hints at code
search. The decision belongs to the HybridRetriever, not to the lexical index.

Start with Reciprocal Rank Fusion:

```text
score = sum(1 / (k + rank_i))
```

RRF is robust because BM25F scores, vector similarities, graph weights, and
future reranker scores do not need to be normalized into one scale. Per-stack
weights (`HybridRetrievalConfig.retriever_weights`) modulate the contribution
of each retriever.

Other future fusion modes:

```text
weighted score
max score per source
learned reranker
planner-guided fusion
```

### Metadata And Structured Filters

Metadata filters are part of the first practical hybrid retrieval layer, not a
later luxury. Filters can be applied before candidate generation when a backend
has supporting indexes, or after candidate generation as an exact filter.

Examples:

```text
source_kind = markdown
path starts_with guides/
language = cpp
resource_id = resource:indexer
timestamp in range
tags contains memory
projection_kind = Summary
field_id = body AND token contains "cyrillic"
```

The first lexical contracts reuse exact `MetadataFilter` semantics from the
vector index. The knowledge base roadmap's new variants — `MetadataRangeFilter`,
`MetadataInFilter`, `MetadataTagFilter`, and the `all_of` / `any_of` combinators
— slot into the same BM25F candidate pipeline.

## Planner-Guided Retrieval

RLM-style recursive retrieval is not a first lexical-index task, but it is worth
tracking as a future context planning layer. The C++ version should not execute
arbitrary Python. It can instead run controlled retrieval functions over the
database:

```text
root planner:
    inspect metadata and corpus stats
    call lexical/vector/graph retrieval functions (each pin-able to a
        projection_kind)
    split large result sets
    request sub-summaries or sub-answers from an external model adapter
    aggregate final context or answer
```

This belongs in a later retrieval planning or context assembly layer, not in
`ILexicalIndex`. The lexical index's only responsibility is to honour the
`projection_kind` pin in `RetrievalPlan` when the planner asks for it.

## Cyrillic Morphology

Russian (and other Cyrillic) text needs morphology-aware tokenization for the
lexical scorer to work well. Without it, `"каталог"`, `"каталога"`,
`"каталогу"`, `"каталогом"`, `"каталоге"` index as five unrelated tokens and
queries that mix cases lose recall.

### Backend Selection

The first morphology backend is **optional** and gated by the
`AGENT_MEMORY_ENABLE_MORPHOLOGY` CMake flag:

```cmake
option(AGENT_MEMORY_ENABLE_MORPHOLOGY
       "Enable Cyrillic morphology backend for lexical tokenization"
       OFF)
```

When the flag is off, the default tokenizer is the std-only code-aware
ASCII/Latin tokenizer described above. The default stack on Cyrillic-heavy
content degrades to lower recall; users opt into the morphology backend when
their corpus warrants it.

When the flag is on, the backend plugs in through a thin C ABI so the project
does not pull a heavy dependency into core. Candidates:

- **ICU** — full Unicode normalization, case folding, word boundary
  segmentation, and Russian stemming/lemmatization via the ICU BreakIterator
  and the dictionary-based stemmer. Heavy dependency but already common in
  C++ projects.
- **pymorphy2 C-API** — battle-tested Russian lemmatizer with alias
  resolution (e.g. `"ё"` → `"е"`), packaged behind a small C shim.
- **Lightweight rule-based stemmer** — Snowball Russian stemmer or similar;
  no external dependency, slightly lower quality than pymorphy2.

The first default picks ICU if available and falls back to the lightweight
stemmer otherwise. The morphological backend is selected at compile time and
exposed as a single `IMorphologyProvider` interface so additional backends can
be added later.

### Tokenization Pipeline For Russian

When morphology is enabled, the tokenizer:

1. Normalizes Unicode (NFC, case folding, `"ё"` → `"е"` alias).
2. Segments on ICU word boundaries.
3. Maps each surface form to a lemma via the morphology backend.
4. Records both the surface form (for snippets and highlighting) and the
   lemma (for posting keys and BM25F scoring).

Alias resolution (e.g. `"сервер"` ↔ `"серер"` typos, transliteration pairs) is
applied as a post-lemma step so dedup works the same way as in the СВИНОПАС
system described in `ai-agent-playbook` (`concepts/rag-knowledge/Внешняя
память LLM-агентов — система СВИНОПАС.md`).

The ASCII/Latin tokenizer stays in core and continues to own the code-aware
behaviour. Cyrillic routing is opt-in per stack config; the default
`AgentLongTermMemory` profile enables morphology when the flag is on.

### Maturity Mapping

Per open issue 17.5 in `memory-stacks-roadmap.md`:

- **M0:** morphology is **not required**. The std-only ASCII tokenizer is
  enough for the MVP smoke tests.
- **M1:** morphology is **required** for any stack that lists Russian in its
  supported languages. The `AGENT_MEMORY_ENABLE_MORPHOLOGY` flag becomes the
  toggle for the build.
- **M2:** morphology is **required by default** for all stacks, with the
  flag retained as an escape hatch for minimal builds.

The flag is read once at `MemoryStack::open()` time; runtime morphology
fallback per unit is not supported in the first version.

## Benchmark Expectations

Lexical work should add benchmark gates before optimized storage or external
adapters become defaults. Track at least:

```text
query latency (BM25 flat body)
query latency (BM25F per projection_kind)
index build time
resource reindex time (per projection_kind)
posting bytes per token (per projection_kind)
token dictionary size
chunk stats size
candidate count
top-k ranking stability
index size on disk
stale posting ratio
compaction time
```

Compare:

```text
BM25 (Original.body)
BM25F (Original)
BM25F (Summary)
BM25F (Original + QAQuestion + QAAnswer + Summary)
vector-only
hybrid BM25F + vector with RRF
hybrid + metadata filters
hybrid + graph expansion
later: rerank
```

Use small curated corpora for behavior tests and larger markdown/code corpora
for latency, storage, and update benchmarks. Add a Cyrillic corpus for the
morphology gates:

```text
russian_wikipedia_subset
code_with_cyrillic_comments
mixed_ru_en_corpus
```

## Reference Projects

- Xapian: mature C++ search engine with BM25 weighting. Useful as a reference or
  optional adapter, not a core dependency.
- PISA: performance-oriented C++ inverted index research engine. Useful for
  compression, WAND/BMW, and query processing ideas.
- simdutf / lemire-simdutf: optional Unicode validation and transcoding backend.
- utfcpp: lightweight optional scalar UTF-8 validation and code point iteration
  backend.
- ICU: optional heavy Unicode normalization, case folding, word boundary, and
  Russian morphology backend.
- pymorphy2: Python Russian morphology; a thin C shim is an option for the
  Cyrillic backend.
- Snowball Russian stemmer: lightweight rule-based fallback.
- tiny-utf8: useful UTF handling reference, not a planned public string type.
- symgraph and OpenWiki: useful references for graph/document retrieval shapes.
- RLM and rlm-minimal: references for planner-guided, recursive retrieval.
- СВИНОПАС (in `ai-agent-playbook`): reference design for Cyrillic morphology
  and alias resolution in agent memory.

## References

- arXiv:0911.5046: "Integrating the Probabilistic Models BM25/BM25F into Lucene".
- arXiv:2107.05720: "SPLADE: Sparse Lexical and Expansion Model for First Stage Ranking".
- arXiv:2004.12832: "ColBERT: Efficient and Effective Passage Search via Contextualized Late Interaction over BERT".
- arXiv:2205.00235: "To Interpolate or not to Interpolate: PRF, Dense and Sparse Retrievers".
- See also: guides/research-reading-map.md.

## Recommended Implementation Order

The lexical work is interleaved with the 16 implementation steps in
`memory-stacks-roadmap.md` section 16. The mapping below is the lexical view
of that ordering; each substep is its own PR.

### L1. BM25 baseline + lexical DBI (memory-stacks step 3)

1. Add this roadmap.
2. Add tokenizer value types and `ITokenizer`.
3. Add a std-only code-aware tokenizer.
4. Add lexical query, result, posting, and stats value types
   (`ProjectionKind`, `FieldId`, projection-aware `LexicalPosting`).
5. Add token dictionary and token-id allocation contracts.
6. Add `ILexicalIndex` with `search(LexicalQuery)` and the flat-body
   `projection_kind = Original, field_id = body` path.
7. Add an in-memory BM25 lexical index for tests and the `BasicRag` smoke
   gate.
7a. Add `KeywordOverlapIndex` as a deterministic tiny-KB lower-bound baseline
    and unit-test oracle. Keep it separate from BM25 naming and scoring.
8. Integrate lexical indexing into the resource reindex path using the
   targeted reindexing protocol from this document.
9. Add hybrid retrieval with BM25 + vector through RRF. The lexical retriever
   reports `retriever_name = "lexical"` on every hit.

### L2. SearchProjections and projection-aware BM25F (memory-stacks step 7)

10. Add the `SearchProjection` value type and the `unit_projections` DBI
    (`mdbx-containers-extension-tz.md` §5.5).
11. Add `projection_kind` and `scope_id` to the `inverted_token_to_unit`
    and `field_to_postings` keys (see MDBX Layout above).
12. Add projection build rules per `ProjectionKind` for `Original`, `QAQuestion`,
    `QAAnswer`, `Summary`, `CodeSymbols`, `DenseContextual`, and optional
    `TranslatedCanonical` (see Projection Build Rules Per ProjectionKind).
13. Promote the MDBX layout so `field_to_postings` is keyed by
    `(scope_id, projection_kind, field_id, token_id, unit_id)` and BM25F,
    BM25, and future fielded sparse retrievers share the same posting
    table.
14. Add the BM25F scorer over the fielded view with explicit per-field
    weights and the `LexicalFieldWeights` configuration.
15. Extend `LexicalSearchResult` to carry `KnowledgeUnitId` and
    `KnowledgeUnitKind`, and add a `pin_projection_kind` to `LexicalQuery`
    so callers can bias the scorer.
16. Wire targeted reindexing per `projection_kind` so an update that only
    changes a `Summary` projection does not invalidate `Original`,
    `QAQuestion`, or `QAAnswer` postings.

### L3. RetrievalPlan integration (memory-stacks step 13)

17. Extend `ILexicalIndex::search` to accept a `RetrievalPlan` (in addition
    to the existing `LexicalQuery` overload) and surface
    `retriever_name = "lexical"` on every hit for RRF.
18. Add the new `MetadataFilter` variants
    (`MetadataRangeFilter`, `MetadataInFilter`, `MetadataTagFilter`) and the
    `all_of` / `any_of` combinators from the knowledge base roadmap.
19. Wire the lexical index into the `RetrievalPlan` lifecycle: emission of
    `RetrievalTrace` per call, RRF contribution, and participation in
    `IContextBuilder` budgeted assembly.
20. Add the token-level cache keyed by `(scope_id, token_id,
    projection_kind, statistics_epoch, statistics_generation)` for fast
    warm-up within a stable aggregate statistics view; include a fixture where
    an old block and a full rebuild share generation `0` but have different
    epochs, proving that stale pruning cannot be reused.

### L4. MDBX and secondary indexes (memory-stacks steps 3 cont. and 12.3)

21. Add the MDBX-backed lexical index with the full scope/projection-aware
    key shape from the first schema, initially restricted to fixed
    `ProjectionKind::Original` and `FieldId::body`; then enable additional
    projection kinds and fields.
22. Add segmented postings, tombstones, and compaction. Compaction must
    preserve projection-version and resource-generation filtering so BM25F and
    BM25 score over the same live-posting set.
23. Wire MDBX-backed secondary indexes for the lexical pipeline
    (resource -> token set, metadata_key -> unit_id, projection version -> unit id)
    so reindex and stale filtering stay fast. See
    [`optimization-roadmap.md`](optimization-roadmap.md) "Secondary
    Indexes" and `mdbx-containers-extension-tz.md` §5.5.

unit_id <-> ProjectionVersionRef:
  - LexicalPosting stores the active projection version at indexing time.
  - Do not confuse it with ResourceManifest.generation (resource-level version).

### L5. Morphology, raw stores, and later layers

24. Add the Cyrillic morphology backend behind the
    `AGENT_MEMORY_ENABLE_MORPHOLOGY` CMake flag (see Cyrillic Morphology
    section). Required for M1 Russian-language stacks.
25. Add file-system raw resource store for `.md` and `.txt`.
26. Add phrase/proximity over positions. The phrase path runs per
    projection_kind and joins candidates into the same RRF stream.
27. Add fuzzy and n-gram search.
28. Add graph retrieval integration with `retriever_name = "graph"` and
    RRF merge against BM25F hits.
29. Add optional Unicode backends (simdutf, utfcpp) as accelerators behind
    the existing std-only tokenizer.
30. Add optional external search adapters if benchmarks justify them.

### L6. Eval gate (memory-stacks step 15)

31. Land the golden dataset loader and the `RetrievalMetrics` CI gate
    over the lexical index. The lift target from
    `knowledge-base-roadmap.md` (Recall@10 hybrid >= 1.20x BM25-only) is
    the release gate.
32. Add Cyrillic golden datasets to the eval suite so the morphology
    backend's lift is measured, not assumed.
