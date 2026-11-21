# ADR: MDBX storage architecture for typed records and numeric identity

- **Status:** Accepted for the pre-release migration design; production
  activation is not implied.
- **Date:** 2026-11-19
- **Scope:** `agent-memory-cpp` MDBX adapters, persistent codecs, internal
  identity mappings and the evidence/release surfaces that depend on them.
- **Evidence:** [`../audits/2026-11-mdbx-storage-numeric-identity-audit.md`](../audits/2026-11-mdbx-storage-numeric-identity-audit.md)

## Context

Several adapters use `KeyValueTable<std::string, std::string>`. That type says
only that the C++ wrapper receives byte strings; it does not establish that a
key or value is human-readable text. Some keys are already canonical binary
composites, some values are versioned framed payloads, and some numeric IDs are
needlessly rendered as decimal text. Treating all three cases as one migration
would damage ordering and compatibility guarantees.

The repository is pre-release, but it already has canonical revisions,
historical ledgers, golden corruption controls, reopen tests and a bounded
technical preview. Those artifacts are evidence, not permission to silently
reinterpret old bytes. A physical schema change must therefore state whether a
DBI is migrated, rebuilt, rejected, or read through a retained legacy decoder.

## Decisions

### 1. Keep four storage layers separate

The storage path is:

```text
Domain DTO
  -> storage mapper (logical ↔ physical identity)
  -> versioned record/key codec
  -> caller-owned MDBX transaction
```

Domain DTOs remain backend-neutral. Internal persisted DTOs are allowed, but
MDBX handles, codec instances and identity-dictionary pointers do not enter
public domain values. A record codec does not open a transaction or allocate an
ID. The storage unit of work owns `lookup`, `resolve`, `intern`, primary writes,
reverse mappings and commit/abort.

### 2. Numeric identity is selective, stable and non-reusing

Already numeric logical IDs use a numeric physical key when the chosen DBI
comparator supports it. Repeated textual internal identities may use a
persistent dictionary only after a measured benefit is shown. User data such
as URI, title, free text and file name is not automatically interned.

Every mapping has a separate collision domain, reserves zero, never reuses an
assigned value while history can reference it, survives reopen, and supports
explicit import/export remapping. A read lookup never allocates. A mapping and
the primary/index rows it enables are one transaction or no transaction.

### 3. Match the key codec to the comparator

Native integer keys and bytewise composite keys are separate contracts:

- `MDBX_INTEGERKEY` is for a single supported native integer key;
- a packed integer is allowed only when its field order and width are proven;
- general composite keys use the canonical binary codec with explicit field
  lengths and sort-order guarantees.

No existing DBI comparator flag is changed in place. A comparator/layout change
creates a new schema/keyspace or runs an offline migration with a rollback
copy. Big-endian encoding is a bytewise-composite concern, not a universal rule
for native integer-key tables.

### 4. Version records and reject unknown bytes

Record codecs have explicit versions, bounded lengths, exact-consumption checks
and typed corruption/unsupported-version errors. A DBI-level version may stand
in for a per-record header only when mixed generations cannot coexist. Unknown,
truncated, trailing or unbound data is rejected; it is never treated as a
missing row and never populated with guessed fields.

### 5. Use four explicit compatibility dispositions

Every physical change chooses one disposition before implementation:

| Disposition | Meaning | Required evidence |
|---|---|---|
| `migration` | Canonical rows are exported and rewritten while preserving logical identity, revisions, digests and history | old/new fixtures, counts/digests, atomic commit, migrated reopen |
| `rebuild` | Derived rows are discarded and deterministically recreated from canonical rows | source snapshot, row/order/generation parity, rebuild receipt |
| `reject-old` | An old or unbound physical layout is not read by the new profile | explicit schema marker and typed rejection test |
| `retained-legacy-reader` | A bounded old decoder remains available without changing the meaning of old bytes | version matrix, negative unknown-version tests, deprecation owner |

The audit matrix is the owner of the current per-DBI choice. A future PR may
change a choice only with a new evidence entry and an updated migration test.

### 6. Preserve evidence surfaces

Golden bytes, reopen/upgrade tests, historical commit-based experiments and
technical-preview reports are versioned evidence. Existing artifacts are not
rewritten to make a new schema appear compatible. New numeric fixtures are
added alongside old fixtures; historical reports keep their exact commit and
layout. A preview is requalified against the new exact head before it can be
called current.

## Consequences

This design permits a focused numeric pilot for hot internal IDs without a
repository-wide rewrite. It adds an offline migration/rebuild step and requires
one more layer of fixture bookkeeping. That cost is deliberate: the short
decimal key is not worth losing revision identity, range ordering or the ability
to diagnose a damaged database.

The current `StorageIdentityMapping` pilot is therefore a contract and codec
foundation, not a production default. Canonical `KnowledgeUnit` and C1
migration slices must land separately, with derived indexes rebuilt only after
the primary data has been verified.

## Implementation order and stop conditions

1. Add the numeric primary-key/key-codec pilot for one canonical identity.
2. Add old/new golden fixtures and an offline export/import migration test.
3. Reopen the migrated environment and verify logical/revision/digest parity.
4. Rebuild one derived index and verify scope/order/generation parity.
5. Requalify the technical preview and release matrix at the exact head.
6. Expand only to the next measured hot path; leave textual external IDs and
   legacy adapters unchanged unless their own evidence justifies a migration.

Stop the rollout if old rows are accepted without an explicit disposition, if
an ID is allocated during read, if a reverse mapping can commit without its
primary row, or if a new benchmark mixes measurements from different physical
layouts.

## Rejected alternatives

- Replacing every `KeyValueTable<std::string, std::string>` mechanically.
- Serializing domain DTOs through raw C++ object layout or `memcpy` as a
  durable format.
- Changing a DBI comparator in place and hoping MDBX ordering remains valid.
- Introducing an adaptive inline-string/numeric promotion threshold without a
  break-even benchmark and historical/no-reuse policy.
- Declaring a technical preview current merely because a newly created database
  passes reopen tests.
