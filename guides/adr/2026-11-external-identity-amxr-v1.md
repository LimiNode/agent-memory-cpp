# ADR: standalone AMXR v1 external identity

- Status: **Standalone binary interchange integrated and CLOSED** in
  `main@386e26fe9176d268722894cd9f48df33f3b7bf72` via #682/#684/#685.
  No default activation, provider adapter or store is implied.
- Date: 2026-11-22.
- Owner: domain identity; normative contract: [identity policy](../identity-policy.md).
- Scope: bounded ExternalObjectRef values and binary codec only.

## Context

An integer or opaque string alone cannot identify an external provider object.
Its issuer, service instance, real uniqueness domain, object kind and declared
parent context also matter. Existing MessageId, ThreadId and AuthorId lack that
information and cannot be automatically converted without explicit context.
Their constructors, stored bytes and meaning must remain unchanged.

The [conversation/event ADR](2026-11-conversation-event-identity.md) proposed
external references alongside a larger future model. The reusable identity
slice needs its own review boundary, without approving that event model.

## Approved decision and rationale

The policy's sections 2–4 are the single exact specification, including field
order, namespace issuance, framing, tags, limits, equality, order and digest.
This ADR records why those choices were submitted, not a second grammar:

- Explicit durable host/adapter namespace assignments prevent guessed identity.
  The object-kind recipe fixes parent semantics, scalar types and composite
  roles/order. A recipe change requires a distinct namespace/kind and explicit
  versioned mapping; rebuilding the adapter cannot reinterpret existing bytes.
- Binary magic and an explicit fixed-width version discriminate this standalone
  format from legacy values and future versions. Version 1 fixes scalar tags
  1/2/3 (i64/u64/UTF-8) and composite tag 4. Tags are not heuristic type hints.
- Length-framed fields preserve exact bytes, including NUL and separators,
  without escape aliases. Eight-byte big-endian integer payloads avoid lossy
  decimal/floating-point conversions. Signed payloads use two's complement.
- Ordered named scalar tuples allow bounded scoped keys without an unbounded
  recursive object model. The 2..16 count and depth-one profile are deliberate
  limits, not promises to cover arbitrary provider schemas.
- Validation rejects invalid namespaces and UTF-8; it never normalizes input.
  Strict decoding and preflight size limits reject rather than repair aliases,
  unknown tags/versions, extra bytes or oversized structures.
- Full canonical bytes determine equality/order and the SHA-256 fingerprint.
  Fingerprints do not authenticate issuers or replace full collision checks.
  There is no public textual interchange syntax or std::hash identity recipe.

Rejected alternatives for this slice: unqualified integer/string variants,
delimiter concatenation, automatic numeric-string conversion, implicit Unicode
or case normalization, recursive generic keys and changing legacy storage IDs.

## Review and qualification gates

[Independent literal vectors](../../tests/domain/external_object_ref_test.cpp)
pin each tag and signed boundary, tuple framing and SHA preimage. The isolated
16/17-component vectors separate the count bound from the aggregate byte bound.
Pairwise controls distinguish namespaces, kind, parent presence/type, opaque
case/whitespace/Unicode bytes, scalar tags and composite ordering. Malformed
controls exercise strict decoding, not only encoder/decoder agreement.

The [qualification record](../experiments/2026-11-21-identity-architecture-qualification.md)
owns exact revisions, executed checks and limitations. Independent architecture
review supplied by the user on 2026-11-22 approved the policy, codec and vectors
at #682 `95262b9d`, #684 `a27e0c05` and #685 `7024b088`, limited to standalone
binary interchange. The approval does not cover arbitrary provider adapters,
issuer authentication or universal decoder security. That architecture approval
alone was not merge permission; subsequent qualification and separately
authorized integration completed the standalone gate at the integrated baseline
above. Historical source-bound records retain their original results. Revisit the decision
for any wire/namespace-rule change or newly required provider shape; never
quietly extend version 1.

No existing DBI, storage key, hash recipe, MessageId/ThreadId/AuthorId, defaults
or historical fixtures change. Issuer registry persistence, JSON/text adapters,
Conversation/Event Model and ConversationStore remain out of scope.
