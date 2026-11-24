# Identity policy and decision register

**Status: normative specification for Identity Architecture; standalone AMXR v1
architecture APPROVED, integrated and CLOSED within standalone AMXR v1.** Existing
implementation observations below are bound to
`main@acd5ad5545386b3feeaa79f8a587855b60801b4d`. The new ExternalObjectRef /
AMXR value-and-codec implementation was separately reviewed; this
policy was first published in documentation PR #682. Companion implementation
[#684](https://github.com/LimiNode/agent-memory-cpp/pull/684) supplies the opt-in
values and codec. #682/#684/#685 are integrated in
`main@386e26fe9176d268722894cd9f48df33f3b7bf72`; historical qualification records
retain their original source/HEAD identities and are not rewritten.

This is the central owner of identity-category separation, external namespace
issuance and the standalone AMXR codec. Topic owners retain allocation,
revision, lifecycle, authorization, persistence and their existing wire formats.
An identity policy does not authorize a migration, production activation or
rewriting a legacy constructor. Conversation/Event Model, TypedMetadata,
ConversationStore and canonical publication remain separate workstreams.

## 1. Categories and authority

An identifier's type/namespace is part of its meaning. Equal raw integers,
strings or digest bytes in different domains do not imply the same object.

| Category | Namespace and owner | Allocation, validation and stability | Comparison, encoding and import |
|---|---|---|---|
| Internal logical occurrence | Store/workspace allocation domain; e.g. KnowledgeUnitId | Store allocates non-zero u64 monotonically; committed assignments are not rebound after retirement. Constructor values do not allocate. | Numeric equality/order only within that allocation domain; store codec owns bytes. Import may explicitly remap local occurrence while retaining portable source identity. |
| Portable logical/source | Issuer plus its declared object/revision scheme; artifact/canonical owner | Host/adapter issues stable identity; no rebind to a different logical object. Opaque text is valid where the scheme requires it. | Exact scheme-defined comparison/bytes, not numeric parsing. Export preserves identity and selected revision; namespace reconciliation is explicit. |
| Provider-scoped external | Provider + service instance + actual identity domain + object kind + parent context when needed | Issuer supplies typed key; host/adapter supplies the declared namespace recipe. Missing identity remains unresolved. | Full type-strict tuple equality; AMXR below. Local remapping never rewrites the external tuple. |
| Content-addressed | Algorithm and identity recipe; artifact/unit owner | Derived from exact declared content preimage; no occurrence allocator. New content/recipe can mean new identity. | Binary digest equality is algorithm/recipe-qualified. Existing ArtifactId and ContentHash formats remain unchanged; a digest does not authenticate source or replace collision checks. |
| Backend-local physical | Environment/context plus table/key kind; storage owner | Backend maps logical identity to compact keys under its transaction/no-reuse contract. | Existing numeric or composite comparator/codec; not portable source identity. Import remaps only through a validated mapping in a fresh/compatible namespace. |
| Composite | The ordered product of its typed, scoped components; containing owner | Recipe fixes component roles, names, arity, presence and versions. | Framed encoding, never ambiguous separator concatenation; no reordering unless the owner defines a map rather than a tuple. |
| Revision/version/generation | The object, recipe, schema or index whose version it measures | Not automatically an entity ID, wall-clock timestamp or globally comparable counter. | Bind the owning object and exact version. Equal generation numbers in different resources/indexes are unrelated. Physical encoding generation cannot change logical identity. |

Identity values own their memory unless a specific view API says otherwise.
Logical immutability is a store/admission invariant: mutable C++ DTO fields do
not authorize rebinding a published identity. Validate again at import,
serialization and admission boundaries. Equality alone is not validation.

Authority is deliberately distributed, without duplicated wire grammars:

| Subject | Owner retained by this policy |
|---|---|
| Units, scope, content keys and recipe versions | [knowledge units](knowledge-units-roadmap.md), [memory stacks](memory-stacks-roadmap.md) |
| Sources, immutable source revisions, artifacts, representations and anchors | [artifact provenance](artifact-provenance-roadmap.md) |
| Documents, blocks and selected canonical revisions | [canonical content](canonical-content-storage-roadmap.md) |
| Physical document/block mapping | [storage identity mapping](storage-identity-mapping.md), [DBI manifest](dbi-manifest.yaml) |
| Token namespace/dictionary epochs and posting generations | [lexical search](lexical-search-roadmap.md) |
| Candidate identity, exact hydration and final deduplication | [retrieval execution](retrieval-execution-roadmap.md) |
| Future conversation identities and events | [conversation/event ADR](adr/2026-11-conversation-event-identity.md), subordinate to this guide for reusable external references |

Specific legacy encodings win over a generic recommendation in this guide.
An actual contradiction requires a named owner correction and conformance
proof, not silent normalization, numeric conversion or schema modification.
The narrowly scoped [AMXR v1 ADR](adr/2026-11-external-identity-amxr-v1.md)
records the standalone-format rationale and architecture-review boundary;
it does not approve the separate conversation/event proposal.

## 2. External namespace issuance and missing identity

ExternalObjectRef is a backend-neutral reference to an external object, not a
chat message, local allocation, revision selector or authorization token. Its
owned fields are provider, service_instance, identity_domain, object_kind,
optional parent_key and required object_key.

The host and provider adapter MUST agree on a durable namespace descriptor
before admitting references. At minimum that descriptor fixes:

- the provider label and actual issuer/service-instance identity, including
  self-hosted instances rather than only a product name;
- the provider's real uniqueness domain: account, workspace, tenant or shared
  service domain as applicable;
- object_kind and its immutable key recipe, including parent role/type,
  scalar types, component names/order, and any provider-defined aliases;
- descriptor/adapter provenance and the authority responsible for issuing,
  retaining and exporting those namespace assignments.

Provider labels are stable registered labels within the cooperating host's
namespace registry, not names minted afresh on each import. The registry may
be host configuration or an existing durable source catalog; this phase does
not implement another registry store. Reopen, backup and cross-store exchange
must retain the same descriptor/issuer binding. An assigned namespace cannot
later be reused for a different issuer or key recipe.

Portability is conditional on that shared, verified issuer contract. The value
type cannot prove Internet-global uniqueness, discover an issuer, authenticate
a namespace or resolve aliases. Two independent hosts choosing the same label
are not a uniqueness proof. When their issuer bindings cannot be reconciled,
retain the discrepancy as an explicit import conflict/unresolved observation;
do not merge source objects because their tuple bytes happen to match.

Changing a key recipe requires a distinct object_kind or identity_domain and
an explicit versioned mapping. A new adapter build number alone cannot give
the same tuple a different meaning. No generic URI canonicalization, case
folding, Unicode normalization, whitespace trim or decimal parsing occurs.
If an issuer defines an equivalence, its adapter must implement the declared
versioned recipe before constructing the tuple and retain the source value.
The lowercase namespace-label grammar below is a validation rule, not an
instruction to lowercase supplied input. Any change to that grammar needs a
new reviewed wire version; changing issuer or key equivalence needs a new
namespace/recipe assignment as described above. The codec guesses neither.

A parent belongs to the **same provider, service instance and identity domain**
as the object. The immutable object-kind recipe defines whether it denotes a
chat, topic, directory, etc.; the numeric/text value alone does not. Cross-domain
relationships require another full reference, not this parent_key. Multiple
scoped components can be flattened into an ordered named composite under a
declared recipe; AMXR v1 does not support arbitrary recursive keys.

An observer account normally belongs to observation/access provenance. Shared
messages observed by two accounts retain one external identity and two
observations. If the provider's actual keys are account-local, include the
account domain in identity_domain under that provider contract. Identity never
grants access; scope, lifecycle and selected-revision admission are independent.

Absent or empty external IDs do not become guessed/random global references,
text hashes or fabricated provider keys. Retain a local occurrence/unresolved
observation and import evidence under its owner. A later resolved source
reference is an explicit reconciliation, not silent mutation of a published
key. Default ExternalObjectRef is invalid; optional absence and an invalid
present DTO are different states. No conversion from MessageId, ThreadId,
AuthorId, FactId or another legacy string wrapper infers provider or key type.

## 3. External value semantics and limits

Scalar key types are signed i64, unsigned u64 and opaque UTF-8 string. A
composite key is an **ordered tuple of 2..16 named scalar components**; names
must be unique. Composite nesting depth is exactly one: components cannot
contain composites. A one-element tuple is not admitted as a second spelling
of a scalar. Equal values with different component names are allowed.

| Field | Exact version-1 validation |
|---|---|
| provider | `[a-z][a-z0-9._-]*`, 1..128 bytes |
| object_kind | `[a-z][a-z0-9._-]*`, 1..64 bytes |
| service_instance, identity_domain | Non-empty strict UTF-8, at most 1024 bytes each |
| Scalar text key | Non-empty strict UTF-8, at most 4096 bytes |
| Component name | `[a-z][a-z0-9_]*`, 1..64 bytes; unique within its tuple |
| Complete AMXR reference | At most 65,536 bytes, including all framing |

UTF-8 must be shortest-form encoding of Unicode scalar values: reject stray
continuations, overlong sequences, surrogates and code points above U+10FFFF.
Valid opaque text preserves exact bytes, embedded separators and U+0000.
Logs/interchange must escape control characters without changing identity.
No empty scalar text is admitted, including a present empty parent. A parent
may be absent; signed/unsigned zero are legitimate keys, not missing sentinels.

For valid values, equality compares every namespace field, parent presence,
scalar tag/value, component name/order and tuple arity. Thus signed `123`,
unsigned `123`, string `123` and string `00123` are distinct. Reordered
components are different identities even if their names/values form the same
map. Structural `operator==` on invalid DTOs does not admit either as an
identity. Constructors do not allocate, authenticate or validate an issuer.

Canonical order is unsigned-byte lexicographic order of the complete AMXR
encoding, **not numeric order, locale order or another existing ID's order**.
Ordering, serialization and digest reject invalid values. The stable
fingerprint is full SHA-256 of those complete bytes, including magic/version;
it is not ArtifactId, ContentHash, std::hash, proof of authority or a substitute
for full reference equality. Collision policy cannot silently merge unequal
full references.

For admitted values, `a == b` if and only if `encode(a) == encode(b)` and if
and only if neither `a < b` nor `b < a`. Equal values have equal fingerprints;
equal fingerprints alone never establish equality. Finite collision regression
vectors qualify namespace/type/framing separation, not cryptographic collision
freedom. No std::hash specialization is introduced; a caller's runtime hash
must respect full equality and must not become a durable identity recipe.

## 4. Canonical ExternalObjectRef codec (AMXR v1)

AMXR is a new **standalone** value encoding. It is not wired into an existing
DBI/key, persisted C1/flat record, content-hash recipe or publication manifest.
Adding any such persistence is a separate schema/version/migration decision.
AMXR v1 defines **binary interchange only**. It introduces no public textual
prefix, URI, decimal form, hex/base64 wrapper or diagnostic interchange grammar.
The hex literals in golden tests merely display exact binary bytes. Logs or
diagnostic renderings are not serialized identities and must not be parsed as
AMXR interchange. A future public text form needs its own reviewed canonical
grammar, version discriminator and independent golden vectors.

All lengths/counts are unsigned big-endian and count bytes/elements, not code
points. Fixed-width integer payloads are exactly eight bytes. The complete
grammar, in field order, is:

```text
reference = ASCII "AMXR" + u16be(1)
          + text32(provider) + text32(service_instance)
          + text32(identity_domain) + text32(object_kind)
          + u8(parent_presence) + parent_key? + key(object_key)
parent_presence = 0 (absent) | 1 (exactly one key follows)
text32(text) = u32be(byte_length) + exact UTF-8 bytes
key = u8(tag) + u32be(payload_byte_length) + payload
tag 1: payload = i64be, two's complement
tag 2: payload = u64be
tag 3: payload = exact opaque UTF-8 bytes
tag 4: payload = u16be(component_count) + component*
component = u16be(name_byte_length) + ASCII name bytes + scalar_key
scalar_key = a complete key frame with tag 1, 2 or 3 only
```

There are no hidden separators, terminators, padding, optional trailing fields
or additional component tags. Key payload lengths include the whole composite
count/name/scalar-frame sequence for tag 4. The tuple order and absent-parent
byte are identity-bearing. Every nested frame and the complete input must be
consumed exactly. Unknown version/tag/presence, duplicate component name,
invalid count/width/name/text, truncation, overflow or trailing bytes reject.
Decode of i64 must not rely on implementation-defined out-of-range unsigned
conversion. No lossy integer-to-double or implementation-defined formatting is
part of this codec.

Encoding validates and overflow-safely preflights the exact whole-reference
size before output allocation, reserves that size and verifies the final size.
Decoding checks total input bound, remaining framed lengths, scalar/name bounds
and count feasibility before allocating strings/vectors. A legal per-component
size does not waive the aggregate limit. Canonical valid input satisfies
`encode(decode(bytes)) == bytes`; invalid/noncanonical input must not be repaired.

JSON adapters are not included in this phase. Any future optional adapter must
preserve scalar tags and all i64/u64 values exactly, including `2^53 + 1`, using
typed lossless parsing rather than a floating-point intermediary. JSON support
cannot become a mandatory dependency of the core value types.

## 5. Identity architecture decision register

`IMPLEMENTED` below describes the named existing slice, not universal production
activation. `COMPATIBILITY_RETAINED` is an explicit choice to preserve current
API/bytes. `PLANNED_NOT_BLOCKING` names an owner and a reason it does not block
AMXR. `REVIEW_REQUIRED` is not CLOSED or permission to merge.

| Decision / type family | Disposition and owner | Adopted rule / boundary |
|---|---|---|
| KnowledgeUnitId | IMPLEMENTED; unit store | Local non-zero u64 occurrence, store allocation/no-reuse; distinct from content key and physical document/block domains |
| TokenId / SectionId | IMPLEMENTED; lexical | Token is dictionary/index-local; SectionId is resource-local with zero unset. Keep epoch/resource context; neither is a portable source ID |
| DocumentStorageId / BlockStorageId | IMPLEMENTED opt-in; storage mapping | Independent non-zero sequences, committed no-reuse/retirement, exact forward/reverse bindings and transaction provenance; lookup does not allocate |
| ResourceId / ChunkId | COMPATIBILITY_RETAINED; resource ingestion / retrieval | Keep strong opaque strings, current equality/order and caller-supplied bytes; namespace plus resource/generation provenance provides meaning, not a new universal allocator |
| DocumentId / ContentBlockId | COMPATIBILITY_RETAINED; canonical content | Stable logical document and document-qualified block across revisions; no rebind to another object. Exact revision is separate; physical mapping does not rewrite public references |
| SourceRevisionId | COMPATIBILITY_RETAINED; artifact provenance | Opaque portable immutable source snapshot identity; preserve issuer/source context. Do not replace with local generation, URI or unqualified commit token |
| ArtifactId / BlobDigest / ResourceBodyDigest | IMPLEMENTED representation retained; artifacts/resource | Preserve `sha256:` + 64 lowercase hex ArtifactId and algorithm-tagged binary digests of declared decoded bytes; retention/role belongs to bindings |
| ContentHash / KnowledgeUnitKey | IMPLEMENTED narrow M0; units | Preserve truncated hash/content-key semantics and recipe v0/v1/v2 separation. Hash is not occurrence; changing recipe needs explicit lineage/migration |
| storage::CanonicalFlatDigest | IMPLEMENTED opt-in; flat codec | Preserve algorithm/byte format and version validation; not source/occurrence identity |
| ArtifactBindingId | COMPATIBILITY_RETAINED; artifact catalog | Keep existing immutable binding IDs/codecs and revision-artifact-role semantics; no new universal parsing/allocation rule |
| ScopeId | COMPATIBILITY_RETAINED; units / scope admission | Current wrapper/recipe v2 use supplied bytes; empty is unspecified, not implicit global. Future roadmap canonical_bytes normalization is not current wrapper behavior |
| RepresentationId / SegmentSetId / SegmentId / EvidenceAnchorId | COMPATIBILITY_RETAINED; artifact/representation owner | Preserve opaque IDs and existing locator bytes; use containing source/revision/recipe/representation where required. No local-counter substitution |
| LexicalSegmentPhysicalKey | IMPLEMENTED; lexical physical contract | Preserve versioned composite widths/comparator/epoch; rebuildable key is not source provenance |
| ExternalIdentityScalar / component / key / ExternalObjectRef | IMPLEMENTED in #684; domain / this guide; standalone integration CLOSED | Dependency-free values and exact standalone AMXR v1 codec; no existing DBI integration, issuer registry or default activation |
| MessageId / ThreadId / AuthorId | COMPATIBILITY_RETAINED; legacy chat / future conversation owner | Keep old strings, constructors and validation. No automatic external-ref conversion; internal/external namespaces in the old shapes remain incomplete |
| FactId / MemoryObjectId | COMPATIBILITY_RETAINED; legacy facts/memory | Keep existing value APIs; do not invent or activate a competing canonical allocator/store |
| Portable namespace registry persistence | PLANNED_NOT_BLOCKING; host / source catalog | The issuance contract is mandatory now; an additional library registry/store is not needed to encode caller-supplied verified tuples |
| Binary ArtifactId lookup optimization | PLANNED_NOT_BLOCKING; artifact owner | Existing digest/text representation is correct and retained; optimizing lookup needs separate compatibility evidence, not AMXR changes |
| Conversation occurrence allocator and actor/thread identities | PLANNED_NOT_BLOCKING; conversation ADR | No event/store added now; AMXR is reusable without inventing an EntryId allocator |
| Generic Fact/MemoryObject and representation allocator profiles | PLANNED_NOT_BLOCKING; units / artifact owner | Existing concrete validators/codecs remain authoritative; their broader stores are not consumers required by AMXR |

### Named legacy/raw-string exceptions

The [historical catalogue](audits/2026-11-public-type-catalogue.json) remains
byte-for-byte bound to its original source SHA. Its 74 public raw-string
identity-name candidates are disposed by the following **exhaustive families**,
not by silently converting all fields. Each exact field/header is in that
catalogue; its `decision_ref` family spelling remains historical.

| Family / candidate count | Disposition, named owner and non-blocking rationale |
|---|---|
| Descriptor, revision and receipt text / 27 | COMPATIBILITY_RETAINED. Artifact receipts/bindings: artifact owner; prompt profile/renderer: prompt composition; resource schema/extractor/segment recipe: resource/structural-segment owners; embedding/encoder/model/index/projection descriptors: embedding/index/lexical owners; workspace: federated retrieval; codec: body codec; publication ID: linked-derived publication. Preserve issuer/recipe/version semantics and exact existing codecs; these descriptors are not newly allocated external objects. |
| Non-entity lookup keys / 2 | COMPATIBILITY_RETAINED. MetadataFilter.key and PromptTemplateBinding.key remain exact lookup/binding labels under their existing metadata/prompt owners, not namespace-qualified entity IDs. |
| Opaque binary-or-text keys / 5 | COMPATIBILITY_RETAINED. DerivedRecordRef.key belongs to resource manifests; canonical/unit/manifest/lexical publication-guard keys belong to their existing publication contracts. Keep exact bytes, including NUL where allowed; do not apply UTF-8 or numeric parsing. |
| Table/column/row locator IDs / 7 | COMPATIBILITY_RETAINED. Artifact representation/locator owner retains representation-local composite context and exact locator codec. PLANNED_NOT_BLOCKING: any new globally portable table-key scheme requires that owner's reviewed adapter/recipe; AMXR does not reinterpret these fields. |
| Transcript speaker / external video IDs / 4 | COMPATIBILITY_RETAINED. Artifact/locator owner retains existing fields and source/representation context. PLANNED_NOT_BLOCKING: an explicit provider-domain adapter may later emit ExternalObjectRef, but legacy text alone cannot infer issuer/account/revision. |
| Evaluation labels, revisions and digest text / 24 | COMPATIBILITY_RETAINED. Evaluation/artifact/embedding-fixture owners preserve portable dataset/query/item/model/prompt labels and exact declared digest encodings. These are fixture/interchange descriptors, not local database sequence addresses. |
| Flat logical document/block/parent text / 5 | COMPATIBILITY_RETAINED. Canonical/flat/storage-mapping owners preserve logical strings plus document/revision context and map transactionally to physical numeric keys. Parent absence/empty and no-reuse follow the existing flat codec, not AMXR. |

Within the descriptor family, SegmentSetIdentity.source_revision_id specifically
remains an exact portable source-revision binding under the artifact/structural
segment owners; it is not a processor version or local generation. A
FramedZstdDescriptor.codec_id instead describes physical representation and
cannot change logical content identity. The family label does not erase these
different semantic roles.

The counts sum to 74; private wrapper storage and numeric fields are separate.
No correctness-critical AMXR rule is deferred through these exceptions. A
future consumer that needs a stronger rule must name its concrete owner,
namespace recipe, loss/error semantics and conformance/migration boundary.
It cannot treat `PLANNED_NOT_BLOCKING` as permission for guessed identity.

## 6. Compatibility, evidence and closure gates

Existing installed paths, constructors, Message/Thread/Author/Fact/Metadata
values, C1/flat/MDBX records, comparison contracts, recipe versions and defaults
are unchanged. In particular AMXR validation does not retroactively impose
UTF-8/NFC/no-NUL rules on legacy opaque/binary keys. Existing numeric mapping
tests must still prove independent allocation domains, no committed ID reuse,
retired-record preservation, foreign transaction rejection, lookup without
allocation, rollback, reopen and exact forward/reverse mapping. Sequence gaps
or an uncommitted allocation after rollback follow the store's existing
transaction contract; this guide does not introduce another durable allocator.
A tentative ID from a rolled-back transaction is not a durable assignment and
must not be published as committed provenance.

Required AMXR qualification is hand-specified independent golden bytes and
negative decoding controls for every tag/frame/limit, namespace separation,
signed/unsigned/leading-zero strings, Telegram-style negative parent plus local
message, forum composites, observer-account rules, INT64_MIN/MAX, UINT64_MAX,
`9007199254740993`, absent versus present-empty parent, duplicate/malformed
components, UTF-8 errors, truncation/overflow/trailing bytes and exact re-encode.
Simulated local/physical remapping must preserve complete external identity.
Include legacy source compatibility, C++17 GNU/MSVC, JSON/MDBX on/off affected
headers/tests and an installed consumer. No skipped workflow is a passing run.

The implementation PR records exact source HEADs, actual test results and a
new source-bound catalogue/addendum for newly introduced declarations. It must
not relabel or overwrite the original `acd5ad55` audit/catalogue as current.
This policy PR alone supplies no runtime-test or namespace-registry evidence.

The public [usage example](../examples/external_identity.cpp) is also the
installed C++17 consumer executed by packaging CI. The separately extracted
[type delta](audits/2026-11-identity-closure-type-delta.json) compares the complete
header AST at `a27e0c0558314d3760b41217ca37b29ab71c3bff`: 149 headers, 565
declarations, exactly four added types and unchanged hashes for the 561 old
declarations. Its name-based field count is not semantic identity inference.

The standalone closure gate required agreement between public API, this owner,
implementation, goldens, compatibility and independent review, followed by
explicit merge authorization, approved integration and exact-final-main
qualification. That gate is CLOSED at the integrated baseline named above;
the source-bound historical records are unchanged. No release/tag, existing
database migration, numeric/default activation,
ConversationStore, TypedMetadata or publication coordinator is authorized here.

### 6.1 Explicit exception-safe value copies

The additive `copy_external_identity_scalar`, `copy_external_identity_component`,
`copy_external_object_key` and `copy_external_object_ref` functions construct
active alternatives directly and recursively copy composite components. They
preserve exact types/bytes and leave the input unchanged if allocation fails.
They neither validate/admit an identity nor change AMXR v1. Existing aliases,
aggregate initialization, fields, layout, implicit special members and wire
goldens remain unchanged. This is not a replacement variant implementation.

Use these named operations when an external identity is copied in a path with
an allocation-failure contract. Some tested libstdc++ headers fail while
unwinding a throwing *variant copy constructor*; ordinary implicit caller-side
copies still use the system STL and are not repaired by these functions.
The Conversation wrappers similarly copy source bindings, unresolved targets,
metadata links and envelopes through this one AMXR implementation. Move the
result into optional/container/variant destinations; do not copy it again.
Existing by-value conversion APIs remain source/binary compatible: pass a
moved value (or an explicitly copied envelope) rather than relying on a
caller-side implicit copy under injected OOM.

The [copy-hardening audit](audits/2026-11-24-external-identity-copy.md) records the
failure mechanism, alternative considered, per-path coverage and qualification
limits. It does not identify an upstream GCC bug or fixed STL version.
