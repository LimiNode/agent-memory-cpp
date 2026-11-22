# ADR proposal: typed metadata without legacy mutation

- Status: **Proposed / review pending**, not an implemented public API or codec.
- Date: 2026-11-21.
- Source inspected: `acd5ad5545386b3feeaa79f8a587855b60801b4d`.
- Milestone: M1b+ opt-in value/codec prototype; consumer-specific activation later.
- Scope: separate typed values, canonical wire ownership, limits and compatibility.

## Context and authority

Current `domain/Metadata.hpp` is `map<string, string>` with exact text equality
in MetadataFilter. Document, C1, flat and retrieval codecs use that surface.
Changing it in place to a variant would change public APIs, persistence and
filter semantics. Legacy `Fact.value` is also text, not a generic typed value.

[Knowledge units, canonical wire grammar v1 and typed metadata value encoding](../knowledge-units-roadmap.md)
already owns null, bool, i64, u64, f64, string, bytes, list and map encoding,
normalization and identity-recipe policy. This proposal adopts that direction
for a separate type; it does not introduce a competing hash grammar. The
implemented M0 Chunk recipe v2 hashes supplied scope/resource/text bytes and
does not implement the broader normalized v1 roadmap contract.

## Proposed decision

Introduce a separate `TypedMetadata` map and an owned recursive `TypedValue`
only after architectural review and a concrete consumer is named. The value
model is a C++17 variant of null, bool, signed i64, unsigned u64, binary64,
UTF-8 string, bytes, list and map. Representation helpers needed to make the
recursive variant legal in C++17 remain implementation details. They must not
leak backend headers, borrowed lifetimes or unbounded parser state into the API.

Retain Metadata and MetadataFilter unchanged. Legacy strings remain strings:
`42`, `true`, an empty string and a decimal-looking ID are never inferred as
numbers, booleans or null. Absence is lack of a field/key; null is a present
typed value. Empty string, empty bytes, empty list, empty map, false and zero
are distinct. Conversion back to legacy text is explicit and potentially
lossy, requiring a caller-selected format or an error.

### Values, equality and filters

Default equality and filtering are type-strict: i64 `42`, u64 `42`, f64 `42.0`
and string `42` differ. Do not compare 64-bit integers by first converting to
double. A future explicit cross-numeric comparison policy needs exact signed /
unsigned and integer / floating boundary rules and its own tests. Lists are
ordered; maps are key-addressed and canonically ordered for encoding.

The owner prohibits NaN. This proposal additionally recommends rejecting
infinities in the first bounded metadata profile; this is a stricter proposed
profile, not a claim that the current owner already forbids infinity. Review
must settle signed-zero semantics before implementation: the proposed canonical
typed representation preserves binary64 sign bits, so `-0.0` and `+0.0` have
different encoded values; any mathematical comparator that treats them equal
is a separately named operation. Do not silently normalize floating values in
an existing identity recipe.

Metadata fields do not confer permissions. A field named scope, tenant, trusted
or role does not override the independently authenticated namespace, lifecycle
or admission checks. Indexing and range predicates are consumers, not intrinsic
capabilities of the value container.

### Canonical codec owner and unresolved framing

Preserve the roadmap v1 tags:

| Tag | Type | Owner-defined payload |
|---|---|---|
| `00` | null | Empty |
| `01`, `02` | false, true | Empty |
| `03` | i64 | Fixed-width big-endian two's complement |
| `04` | u64 | Fixed-width big-endian unsigned |
| `05` | f64 | IEEE-754 binary64 big-endian; NaN prohibited |
| `06` | string | Normalized UTF-8 bytes |
| `07` | bytes | Raw bytes |
| `08` | list | u32 big-endian count followed by recursive values |
| `09` | map | u32 big-endian count followed by normalized key/value pairs |

The owner specifies outer field framing as u16 tag + u8 presence + u32 byte
length, distinct absent/present-empty/present-non-empty, strict field order and
duplicate rejection. Map keys normalize before sorting by encoded key;
duplicate normalized keys fail instead of selecting a winner. Unicode data is
15.1.0 in recipe v1. Keys use NFC, normalized line endings, outer-whitespace
trim, ASCII lower-case and internal-whitespace collapse; metadata string
values use NFC, normalized line endings and outer trim, preserving case.

The existing table does **not fully spell out independent nested string/bytes
and map-key lengths** inside a recursive list/map. An outer field's byte length
alone cannot delimit every recursive variable-length value. Before a codec
prototype, refine that grammar in its existing owner, specifying exact nested
lengths, canonical rejection rules and version/domain relationship. Do not
silently choose one framing here and call it compatible v1. The standalone
TypedMetadata codec also needs an explicit format/version descriptor separate
from the enclosing content-hash recipe version.

Unknown tags/versions, malformed UTF-8, invalid widths/counts/presence,
truncation, trailing bytes, overflow and duplicate normalized keys fail closed.
No codec upgrade or normalization change may silently rehash existing stored
content. Selection of identity metadata remains a per-kind recipe decision;
not every mutable metadata field belongs in content identity.

### Proposed bounds

The following are initial prototype limits for review, not measured deployment
defaults or changes to legacy codecs:

| Bound | Proposed first profile |
|---|---:|
| Total encoded metadata | 1 MiB |
| Container nesting | 16, root container at depth 1 |
| Total values, including container nodes | 65,536 |
| Encoded normalized key | 1,024 bytes |
| Individual string/bytes payload | 256 KiB |

Count map values and container nodes consistently; separately bound map keys
and declared element counts before allocation. Check arithmetic overflow,
remaining input, normalized output and all aggregate limits before reserve /
growth. Both encoder and decoder enforce the same profile. A caller may choose
a stricter declared profile; changing accepted limits cannot reinterpret old
bytes or erase a selected historical value. Normalization resource use also
needs bounded implementation, not just a post-allocation length check.

## Compatibility matrix

| Existing surface | Proposed integration rule |
|---|---|
| Metadata / MetadataFilter | Keep API, bytes, string ordering and exact text equality unchanged |
| C1 / flat / document codecs | No schema/version change in this ADR; preserve current goldens/reopen behavior |
| Fact.value | Remains legacy text; typed FactPayload is a distinct reviewed component |
| PromptBindingValue | Retains its restricted string/bool/i64/double contract; not replaced with recursive metadata |
| Locators / source references | Keep exact codecs and revision semantics; metadata is not an ID parser |
| ContentHash recipes v0/v1/v2 | Preserve existing bytes/versions; no inferred v1 support or automatic dedupe migration |
| JSON import/export | Preserve numeric types and exact i64/u64 values; do not use double as an intermediary |
| Typed index/filter | Separate projection with explicit type/range operators, version and rebuild policy |
| Cross-store import | Preserve typed values, codec/profile/recipe descriptors; incompatible formats fail explicitly |

An explicit legacy-to-typed adapter maps every legacy value to a typed string
without heuristic coercion. If applying v1 normalization would alter that text,
the consumer must keep the original legacy/source representation and declare
the new normalized projection; it is not a byte-identical upgrade. Unknown
formats remain unavailable rather than decoded with guessed tags.

## Implementation and acceptance gates

The minimal next slice after review is an isolated value model, bounded codec
and exact golden fixtures after the owner framing clarification. It must name
one real consumer and stay opt-in; storage schema integration, typed indexes,
legacy migration and production activation are separate changes. Measure
encoded bytes, allocation, encode/decode and bounded lookup cost against the
same legacy/string baseline if making performance claims. No performance
advantage is established by this ADR.

Required future controls, **not tests executed by this documentation PR**:

| Fixture family | Required result |
|---|---|
| Every scalar/container tag | Exact logical input, canonical hex bytes and round-trip |
| Absent/null/empty/false/zero | Distinct representations and explicit filter outcomes |
| i64/u64 endpoints and `2^53 + 1` | Exact C++/JSON round-trip; overflow/rounding rejected |
| Mixed numeric/string comparisons | Strict default behavior; no implicit double coercion |
| NaN/infinity/signed zero | Declared profile honored, bit/normalization behavior explicit |
| NFC/NFD and normalized duplicate keys | Owner-version normalization; duplicates fail |
| Map permutation and list order | Maps canonicalize; lists preserve order |
| Every depth/count/byte boundary | Accept at bound, reject above bound before unbounded allocation |
| Truncation/unknown tag/count overflow/trailing bytes | Deterministic failure, no partial trusted value |
| Legacy codecs and filters | Existing bytes/equality and reopen/upgrade controls unchanged |
| Recipe v0/v1/v2 separation | No silent hash/identity migration; declared recipe goldens |
| C++17 on supported platforms and installed consumer | Stable public includes, optional backend independence |

The typed prototype cannot claim full roadmap v1 support until its required
per-kind canonical byte/hash vectors exist. A value codec alone does not
implement every knowledge kind, store, query engine or migration path.

## Consequences and decisions awaiting review

This provides real types without destabilizing existing storage. It introduces
an explicit adapter/normalization/version boundary. Remaining review decisions
are recursive framing, standalone descriptor, signed zero, infinity policy,
limits and the first consumer. Rejected alternatives are mutating Metadata in
place, guessing types from text, treating prompt bindings as general metadata,
and declaring roadmap normalization already implemented by the M0 wrappers.
