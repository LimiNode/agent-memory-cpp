# Domain model, identity and public API audit

- Status: **Draft / review pending**. Observations describe source; proposals
  are not activated APIs, schemas or milestone policy.
- Date: 2026-11-21.
- Source: `main@acd5ad5545386b3feeaa79f8a587855b60801b4d`.
- Scope: every installed source header and focused semantic identity review.
- Delivery: documentation only; no C++, persisted bytes, defaults, databases,
  migration behavior or research branches change.

## Decision summary

Current status is separately bound to `main@77a278bef338` in the
[Foundation Closure Gate](2026-11-foundation-closure.md), with a fresh AST
catalogue. This audit and its original catalogue remain historical, not relabelled.

Keep logical identity, external source identity, content identity and physical
storage keys separate. A string wrapper is not itself a defect. Strong numeric
IDs, binary digests, versioned composites and `std::variant` already exist;
their semantics need a register, not a bulk conversion.

Keep the DDD-like `domain/` boundary, feature-local type ownership, self-contained
installed headers and compiled implementations in the C++17 static library.
Do not introduce global `data/` or `enums.hpp` merely to make unrelated types
look uniform. The two independent proposals are:

- [Conversation/event identity ADR](../adr/2026-11-conversation-event-identity.md).
- [Typed metadata compatibility ADR](../adr/2026-11-typed-metadata-compatibility.md).

Neither authorizes all-M2 implementation, a content-hash recipe change, header
splitting or production activation of experimental numeric stores.

## Authority and evidence

| Concern | Existing normative owner | This review's effect |
|---|---|---|
| Milestone | [milestones](../milestones.md) | Conversation/session remains later M2; canonical text is the M1b slice |
| Dependencies and components | [architecture](../architecture.md), [memory stacks](../memory-stacks-roadmap.md) | Reconcile maturity, not replace architecture |
| Canonical events and identity layers | [canonical content](../canonical-content-storage-roadmap.md) | Propose an event identity boundary |
| Unit identity and typed wire grammar | [knowledge units](../knowledge-units-roadmap.md) | Preserve roadmap recipe v1 versus implemented M0 v2 |
| Artifact/source identity | [artifact provenance](../artifact-provenance-roadmap.md) | Preserve portable revisions and decoded-byte digest identity |
| Physical identity | [storage ADR](../adr/2026-11-mdbx-storage-architecture.md), [DBI manifest](../dbi-manifest.yaml) | Preserve sequences, comparators and no-reuse |
| Names/includes/documentation | [coding style](../coding-style.md) | Compatible organization plan only |
| Product integration | [product path](../product-path.md), [backend integration](../storage-backend-integration-roadmap.md) | Separate work, not reimplemented here |

The repository graph was consulted first but its root index lacked integrated
numeric/flat declarations. Completeness therefore uses actual source. Graph
reachability and textual mentions are discovery evidence, not runtime traces.

## Machine-checkable catalogue and boundary

[The JSON catalogue](2026-11-public-type-catalogue.json) records installed headers
and their Git-source SHA-256; named records, enums and aliases with header, byte
offset, line, access, nesting, role and declaration hash; identity-named members,
including raw strings; and per-ID decisions with source-bound mention files.
Forward declarations and complete definitions are separate. Private PIMPL types
are labelled private nested declarations, not public DTOs.

`semantic_owner` is the source module. Each header/type also has one
`primary_contract_owner_candidate`: an explicit review-pending owner proposal,
not proof that every current member is already specified by that guide. Shared
architecture/style, authorization and storage-transaction rules still apply;
an owner pointer does not create a second canonical model or override them.

The header set follows the install rules: `src/*.hpp` and installed headers under
`src/agent_memory/`, excluding `storage/CanonicalContentStoreInternal.hpp`.
Aggregates without declarations still have entries. All thirteen requested
module areas are covered.

Extraction used **Clang 22.1.4 syntax-only C++17 ASTs**, MDBX/JSON/Zstd declaration
gates enabled, and MinGW GCC 15.2 standard headers. No object/library/executable,
CMake build or application test was produced. An `agent_memory` namespace pass
is complemented by a `std::hash` specialization pass; namespace-only extraction
would miss project hash specializations. Both compiler passes must succeed.
This is not a regex-only inventory.

The inventory covers project-owned named declarations in `agent_memory` and
project `std::hash` specializations in that installed-header/enabled-feature
profile. It excludes third-party namespaces, such as the `mdbxc::Connection`
forward declaration, as well as other macro combinations, implicit compiler
types, function-local classes and template instantiations. Identity-name matches are
**candidates**, not proof every key is an entity ID. Some string keys contain
binary bytes. Descriptive API-role labels are not new interfaces. The `counts`
object gives exact results; forward declarations, nested/private types and aliases
must not be counted as independent public namespace DTOs.

Recorded profile: 148 headers; 561 named declarations (520 namespace, 41 nested,
39 private/protected); 315 identity-name candidate members, including 74 public
raw-string candidates. These are declaration/candidate counts, not 561 public
entity types or 315 semantic IDs.

### Reproduce and independently verify

The checked-in [documentation extractor](extract-public-type-catalogue.py)
requires Python 3.10+ and Clang for generation. It writes only this catalogue
and ignored `.artifacts/domain-identity-audit/` syntax-only artifacts. The
recorded invocation from repository root is:

```powershell
python guides/audits/extract-public-type-catalogue.py `
  --clang 'C:/Program Files/LLVM/bin/clang++.exe' `
  --mingw-root 'C:/MinGW/x86_64-15.2.0-release-posix-seh-msvcrt-rt_v13-rev0/mingw64'
python guides/audits/extract-public-type-catalogue.py --verify
```

`--verify` needs Git and Python only; it checks the source SHA/header manifest,
all header/declaration hashes and byte/line anchors, uniqueness, counts, module
coverage, private nesting and the explicit `std::hash<TokenId>` specialization.
Generation checks worktree source against the bound Git revision before running
the compiler. On a host with Clang and usable C++17 headers, omit `--mingw-root`
to use host headers; compiler/profile diagnostics may differ from this Windows
receipt. The script is documentation tooling, not installed library code.

For an independent extraction at the source SHA, make one translation unit
including every catalogued header. Run Clang with the declared feature profile
and standard-library include paths:

```text
clang++ -std=c++17 -fsyntax-only -Isrc \
  -DAGENT_MEMORY_HAS_MDBX=1 -DAGENT_MEMORY_ENABLE_JSON=1 \
  -DAGENT_MEMORY_ENABLE_ZSTD=1 \
  -Xclang -ast-dump=json -Xclang -ast-dump-filter=agent_memory all_public_headers.cpp
```

Repeat with `-ast-dump-filter=std::hash`, qualifying project specializations in
`std`. The Windows audit used `--target=x86_64-w64-windows-gnu` and GCC 15.2
`include/c++`, target/backward and MinGW system include directories. AST output
contains successive top-level JSON objects: decode all, retain locations inside
the header manifest, omit implicit declarations and track class access/nesting.
Do not count class tokens, which also occur in enum declarations and templates.

This independent Python check verifies source binding, installation coverage and
every declaration/member anchor. Run from repository root. It does not replace
AST extraction or semantic review:

```python
import hashlib, json, pathlib, subprocess
c = json.loads(pathlib.Path('guides/audits/2026-11-public-type-catalogue.json').read_text(encoding='utf-8'))
base = c['source_commit']
assert base == 'acd5ad5545386b3feeaa79f8a587855b60801b4d'
files = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', base, 'src'], text=True).splitlines()
headers = [f for f in files if f.endswith(('.hpp', '.h', '.ipp'))
           and not f.endswith('/CanonicalContentStoreInternal.hpp')]
assert headers == [h['path'] for h in c['headers']]
raw = {}
for h in c['headers']:
    b = subprocess.check_output(['git', 'show', base + ':' + h['path']])
    assert hashlib.sha256(b).hexdigest() == h['sha256']
    raw[h['path']] = b
for d in c['types'] + c['identity_fields']:
    b, n = raw[d['header']], d['offset']
    assert b[:n].count(b'\n') + 1 == d['line']
    assert b[n:].decode('utf-8').startswith(d['name'])
    if 'declaration_sha256' in d:
        assert hashlib.sha256(b[d['range_begin']:d['range_end']]).hexdigest() == d['declaration_sha256']
assert len({(d['header'], d['offset'], d['kind']) for d in c['types']}) == len(c['types'])
print(c['counts'])
```

The verifier uses Git bytes, not platform newline conversion. A new source
revision needs a new inventory/review; relabelling this SHA does not refresh it.

## Per-identity decision register

These recommendations do not change representation in this PR. In addition to
the six requested decision labels, `KEEP_BINARY` distinguishes existing digest
bytes from numeric allocation sequences.

String wrappers own their text; `value()` borrows a const reference with the
wrapper's lifetime. Construction alone neither validates uniqueness, allocates,
normalizes provider IDs nor guarantees no-reuse. Numeric constructors also accept
caller values; the store owns allocation and retirement. Value assignment does
not imply permission to mutate an already-persisted immutable key.

| Type | Decision / semantic owner | Scope, origin and sentinel | Persistence, encoding/order, import and no-reuse |
|---|---|---|---|
| `KnowledgeUnitId` | `KEEP_NUMERIC`; unit store | Local occurrence, opaque allocated u64; zero unassigned | Monotonic no-reuse ledger; numeric order; import may remap occurrence, not provenance |
| `TokenId` | `KEEP_NUMERIC`; token dictionary | Dictionary/profile-local token; zero empty | Retain dictionary/history; numeric order is not linguistic; imports reconcile dictionaries |
| `SectionId` | `KEEP_NUMERIC`; lexical resource structure | Plain u64 alias, resource-local; zero unset | Retain resource/revision; alias itself promises no universal allocator/no-reuse |
| `DocumentStorageId` | `KEEP_NUMERIC`; optional mapping | Backend document sequence; zero unassigned | Independent sequence, monotonic/retired reservations, AMSI v1; explicit import remap |
| `BlockStorageId` | `KEEP_NUMERIC`; optional mapping | Backend block sequence from document + block; zero unassigned | Independent of document sequence; no cross-document alias, history retained; not portable identity |
| `ResourceId` | `KEEP_STRING`; ingestion/resource | Logical source identity, supplied/derived; empty unspecified | Exact bytes/order today, stable across updates; preserve portable source provenance when remapping |
| `DocumentId` | `KEEP_STRING`; document/canonical | Stable logical identity; header calls globally unique; empty unspecified | Backend no-reuse is separate enforcement; exact bytes/order; physical mapping cannot rewrite references |
| `ChunkId` | `KEEP_STRING`; derived ingestion | Logical chunk, empty unspecified | Preserve resource/generation and derivation; no global allocator inferred; citations survive remapping |
| `ContentBlockId` | `KEEP_STRING`; canonical content | Stable block in document/history; empty unspecified | Document context required, historical no-reuse; string order is not tree order |
| `SourceRevisionId` | `KEEP_STRING`; artifact provenance | Portable immutable snapshot, empty invalid when required | Preserve opaque bytes across stores; not local generation/URI/commit token alone |
| `ArtifactId` | `BINARY_DIGEST_CANDIDATE`; artifacts | Decoded bytes, currently sha256 prefix + 64 lowercase hex | Public textual contract; investigate tagged binary lookup behind compatibility; validate against BlobDigest; not an occurrence ID |
| `ArtifactBindingId` | `COMPOSITE_ID`; artifact relationship | Immutable relationship, empty unspecified | Wrapper remains opaque text, not a proven parseable tuple; review role/catalog derivation before new encoding |
| `ScopeId` | `KEEP_STRING`; tenant/application namespace | Explicit namespace, empty unspecified; explicit global differs | M0 constructor/recipe v2 keep supplied bytes. Roadmap NFC/alias normalization is not wrapper behavior; never apply silently |
| `RepresentationId` | `KEEP_STRING`; extraction | Versioned representation, empty unspecified | Processor/version/source binding; portable identity needs explicit mapping/recipe, not local substitution |
| `SegmentSetId` | `KEEP_STRING`; segmentation | Versioned segmentation, empty unspecified | Source/recipe bound; rebuild is a new derivation, not new source revision |
| `SegmentId` | `KEEP_STRING`; representation segments | Logical segment, empty unspecified | Retain containing representation/source; validate collisions before interning |
| `EvidenceAnchorId` | `KEEP_STRING`; evidence | Portable anchor, empty unspecified | Exact revision/locator/retention preserved; no storage-local replacement |
| `MessageId` | `SCOPED_EXTERNAL_ID`; legacy chat | Opaque text; internal/external distinction absent | Keep old API/bytes; new namespace-qualified external reference versus local occurrence; no automatic conversion |
| `ThreadId` | `SCOPED_EXTERNAL_ID`; legacy chat | Opaque text; conversation/thread distinction absent | Separate conversation and branch/topic semantics; chat number is not global thread identity |
| `AuthorId` | `SCOPED_EXTERNAL_ID`; legacy chat | Opaque participant string | Qualify source actor and aliases; observer account and LLM role are not automatically actor identity |
| `FactId` | `REVIEW_REQUIRED`; legacy facts | Opaque text; canonical allocation owner not proven | Retain compatibility while FactPayload/unit integration is designed |
| `MemoryObjectId` | `REVIEW_REQUIRED`; legacy memory | Opaque hierarchy handle; canonical allocator not proven | Retain compatibility; do not build competing canonical mutable store |
| `KnowledgeUnitKey` | `COMPOSITE_ID`; unit content identity | Kind + scope + recipe-qualified hash, not occurrence | Immutable key; preserve recipe versions; dedupe separate from occurrence |
| `LexicalSegmentPhysicalKey` | `COMPOSITE_ID`; lexical encoding | Versioned physical descriptor | Preserve widths/comparator/schema; index rebuild is not source migration |
| `BlobDigest`, `ResourceBodyDigest` | `KEEP_BINARY`; decoded artifact/resource bytes | Algorithm-tagged 32 bytes | Byte-exact declared codecs; text at interchange; enum algorithm numbers across types are not interchangeable |
| `ContentHash` | `KEEP_BINARY`; unit identity recipe | Truncated 16-byte SHA-256, not collision-free occurrence ID | Preserve recipe v0/v1/v2; collision policy belongs to identity/store |
| `storage::CanonicalFlatDigest` | `KEEP_BINARY`; flat codec | Tagged 32 bytes, zero algorithm unspecified on encode DTO | Decoder/version owns validity; not portable source identity by implication |

The catalogue gives every row's header/line and all source-bound textual mention
files under `src`, `tests`, `examples` and `tools`. That is a compatibility
inspection set, not proof all mentions execute or share an allocator.

### String fields without strong typedefs

Every identity-named member, including optional strings and numeric revisions,
is listed in JSON. Each candidate has a `decision_ref` into these families:

| Catalogue family | Meaning and decision |
|---|---|
| `NON_ID_LOOKUP_KEY_KEEP_STRING` | Metadata filter / prompt binding labels, not entity IDs; exact legacy equality retained |
| `EVAL_PORTABLE_LABEL_OR_EXACT_DIGEST_TEXT_KEEP_STRING` | Dataset/query/item IDs, model/recipe revisions, hashes and prompt IDs: portable fixture/wire labels; distinguish algorithm label from digest payload |
| `REPRESENTATION_LOCAL_COMPOSITE_ID` | Table/column/row keys require representation/source context; label or ordinal is not necessarily stable identity |
| `SCOPED_EXTERNAL_ID_REVIEW_REQUIRED` | External video and transcript speaker IDs require provider/instance/domain or representation-local scope; URL/alias is not identity by default |
| `DIGEST_TEXT_REVIEW_ALGORITHM_AND_WIRE_BEFORE_BINARY` | Preserve exact algorithm/wire; suffix does not prove SHA-256 or decoded-byte identity |
| `OPAQUE_BINARY_OR_TEXT_KEY_REVIEW_REQUIRED` | Derived refs/publication guard keys may contain NUL/binary bytes; no normalization or decimal parsing without owner |
| `LOGICAL_TEXT_ID_PHYSICAL_NUMERIC_MAPPING` | Flat document/block/parent strings retain logical identities; map transactionally, preserving history and no-reuse |
| `OPAQUE_DESCRIPTOR_REVISION_OR_RECEIPT_TEXT_KEEP_STRING_REVIEW_NAMESPACE` | Processor/model/encoder/schema/recipe/profile/renderer/publication/workspace/receipt identities retain portable descriptors; establish issuer, namespace and immutability |
| `TYPED_OR_NUMERIC_MEMBER_REVIEW_IN_OWNER` | Follow containing owner; revision is not an entity, timestamp or allocation sequence by implication |

Fields named only digest, fingerprint, generation or URI are not all captured
by the requested ID/key/hash/revision name rule. Important digest/descriptor
types are also reviewed semantically above. Name matching is not a proof that
other fields cannot affect identity.

## Module ownership and maturity

Maturity is capability-specific. `SCAFFOLD` means a small implemented value slice
without its future store/retrieval path; `LEGACY_COMPAT` means retained public
compatibility. Both can apply. No header is removed/deprecated here.

| Area | Current source boundary | Maturity and owner |
|---|---|---|
| `core/` | LibraryInfo | `IMPLEMENTED` small version API; architecture/style; keep include path, do not fill artificially |
| `domain/` | IDs, resources, envelope, canonical/artifact/representation/prompt values | `IMPLEMENTED` value/validation slices; richer payloads `PLANNED`; respective topic owners, common invariants only |
| `chat/` | Message, Thread, AuthorRole | `SCAFFOLD` + `LEGACY_COMPAT`; event ADR/canonical roadmap; not ConversationStore |
| `facts/` | Fact, FactSourceRef, FactBundle | `SCAFFOLD` + `LEGACY_COMPAT`; unit FactPayload direction, not independent canonical lineage |
| `memory/` | MemoryObject hierarchy handle | `SCAFFOLD` + `LEGACY_COMPAT`; envelope/components/stacks, no runtime |
| `embedding/` | Values, model info, embedder SPI/local enums | `IMPLEMENTED` contracts; concrete providers optional/planned; embedding guide |
| `index/` | Exact/binary/compact and lifecycle | `IMPLEMENTED` bounded baselines/research; general serving claims not implied; optimization/lifecycle owners |
| `lexical/` | Tokens, dictionary, postings, snapshots, segments | `IMPLEMENTED` slices; dictionary owns TokenId namespace; lexical owner |
| `retrieval/` | Chunk/unit retrievers, admission/hydration | `IMPLEMENTED` bounded paths; universal facade `PLANNED`; retrieval/KB owners |
| `storage/` | Stores, codecs, optional numeric mapping | `IMPLEMENTED` slices/opt-in flat; per-kind stores `PLANNED`; storage/canonical owners |
| `ingestion/` | Indexer/importer/publication/Markdown | `IMPLEMENTED` specific text/revision paths; resource/product owners |
| `eval/` | Dataset/metrics/artifact descriptors | `IMPLEMENTED` fixtures; quality claims require receipts; evaluation owner |
| `infrastructure/` | Concrete MDBX contexts/stores/readers | `IMPLEMENTED` opt-in slices; physical manifest/adapter owners; not second domain model |

The architecture's retired/replaced wording is a target, not proof old headers
or consumers disappeared. `DocumentChunk` still participates in document/index
APIs. Retain old source shapes until a reviewed adapter/deprecation path exists.
Canonical facts/events belong to the component model; legacy Fact/Message are
compatibility/import/formatting shapes, not competing authoritative stores.

## Responsibility-based file and Doxygen plan

Preserve PascalCase for a principal class and snake_case/clear aggregate names
for helpers. Local `embedding/enums.hpp` is valid, not a mandate to consolidate
all enums. Topic-organized OptionX data is a useful ownership analogy, not a
tree to copy. No other repository was modified.

| Source seam | Observed responsibilities | Small compatible follow-up and acceptance |
|---|---|---|
| TypedRepresentation.hpp | Four IDs, representations, tables/transcripts, locator variant/codec; one-line declarations | Format/Doxygen first; optional representation/locator leaves later, preserving umbrella and exact golden locator bytes |
| KnowledgeUnit.hpp | Local IDs/scope, enums, content key, citations, envelope | Split identity/provenance/envelope only for concrete consumers; old path/wire enum/hash goldens unchanged |
| Resource.hpp | Revision/digest, derived refs, manifest versions/state | Keep related groups until coupling justifies split; serializers/manifests preserved |
| CanonicalContent.hpp | Blocks/revisions, edits, changes/conflicts | Optional read-model versus commands leaves; old umbrella, no-op/conflict/history fixtures |
| MdbxSegmentedLexicalIndex.cpp | Private Reader/codecs, stored-state validation, incremental partitions/public mutations | Pure private codec/validation seam only; preserve one transaction and dictionary/posting invariants |
| MdbxResourceImporter.cpp | Pending/claimed records, fencing migration, staged import/search | Pure record-codec seam only if useful; retain claim/commit/fence ownership and replay/crash tests |
| ResourceIndexer.cpp | Preflight, ownership, manifest publication, erase/reclamation | Document stages first; extract independently testable validation, not parallel coordinator |

A later split must compile installed headers as first includes, preserve paths,
build affected tests/examples with feature gates on/off, and exercise an installed
consumer. No root-umbrella cycles or speculative new framework. Line count is
not a correctness or performance criterion.

`///` documents the following declaration; `///<` the preceding member. Both
are valid. Document ID scopes, ownership/lifetime, units, persistence, versions,
corruption and thread safety; do not force trailing syntax or comment every
obvious field. Do not promise noexcept/thread safety or add custom tags without
evidence.

## Numeric reconciliation and compatibility

| Layer | Already present | Preserve | Not proved here |
|---|---|---|---|
| Unit occurrence | Numeric pilot + MdbxKnowledgeUnitTypedStore | Non-zero/no-reuse, typed/legacy parity, markers | Default activation/general payload stores |
| Document/block mapping | MdbxStorageIdentityMapping | Independent sequences, document-qualified blocks, retired records, lookup without allocation, transaction composition | Numeric public domain IDs |
| Flat canonical content | MdbxCanonicalFlatStorage | Immutable versions, history, conflicts, numeric composite keys | Universal speed advantage/product-wide atomicity |
| Physical DBIs | Manifest + codecs | Native INTEGERKEY versus 16-byte big-endian bytewise composites are different | In-place comparator changes/cross-environment atomicity |
| Portable provenance | Text revisions/anchors + binary digests | Exact provenance survives local mapping | Local counters replacing portable identity |
| Legacy metadata | String map/filter | Existing C1/flat/document/retrieval bytes/equality | Typed inference or schema migration |

No persisted key/digest, numeric comparator, enum value, recipe, include path or
default changes. Historical fixtures retain source SHA. Real OLD-to-NEW numeric
migration and activation on existing databases remain separate and unexecuted.

## Ranked review and implementation queue

Every row is separate and reviewable; none grants merge authority.

| Priority / milestone | Dependency and minimal next slice | Acceptance and main risk |
|---|---|---|
| P1 cross-cutting | Review identity/ownership register; owner clarifications, no representation changes | Source/API/roadmap agreement; avoid semantic change by documentation alone |
| P1 M2 | Event ADR, then isolated external-reference prototype | Namespace/type/JSON precision goldens; collision-domain risk |
| P1 M1b+ opt-in | Metadata ADR + consumer + framing clarification, then separate value/codec prototype | Canonical goldens, limits/normalization/absent-null/numeric controls; no legacy mutation |
| P2 cross-cutting | TypedRepresentation readability/Doxygen corrective | Header and exact locator tests; no hidden semantic edits |
| P2 layout | Consumer-justified narrow split with umbrella | Include-order/self-sufficiency/installed consumer; no reverse dependency |
| P2 M2 | Event store only after product storage contracts | Event fixture matrix, scope/history/reopen/no-reuse; avoid duplicate canonical models |
| P2 product | Separate numeric/flat product integration batch | Authoritative edit-to-retrieval/reopen; no parallel publication here |
| P3 private code | Pure codec/validation decomposition where justified | Existing fencing/crash/transaction/golden controls; file size alone insufficient |

Implementation prototypes follow independent architectural review of this Draft.
No external-identity, metadata or event implementation is included. The next
decision concerns namespaces, typed-value semantics and compatibility, not global
replacement of strings.
