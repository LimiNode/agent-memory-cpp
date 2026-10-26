# Incremental segmented lexical layout proof

Date: 2026-10-26

Status: executable correctness proof; not a production importer

## Hypothesis

MDBX MVCC is sufficient for a first incremental segmented publication slice if
the active snapshot has a collection-wide sequence while each token partition
has its own monotonic generation. An update to one `KnowledgeUnit` should then
rewrite only the partitions whose posting membership changed, the unit row,
and aggregate metadata.

## Layout decision

The proof uses an isolated `seg-v2-proof` keyspace in the existing lexical
posting-segments table. It is intentionally not a migration of `seg-v1`.

- The descriptor, collection snapshot, publication sequence, historical
  dictionary, unit rows, token statistics, witnesses, and collection counters
  are separate rows.
- A partition metadata row stores the partition-local generation.
- A segment key is `segment/<token-id>:<partition-generation>:<segment-id>`;
  the encoded value carries the same generation and is checked on read.
- A collection update increments the global snapshot generation and sequence.
  Untouched token partitions retain their exact key/value bytes and local
  generation.
- Partition generation is a durable high-water mark for the lifetime of a
  `TokenId`. Deactivation removes active rows but retains and advances the
  partition metadata row, so reactivation cannot create an ABA generation.
- MDBX provides atomic publication and a pinned read transaction. There is no
  application-level copy-on-write and no dual writer.

The current `seg-v1` reader requires every segment generation to equal the
global active snapshot generation. Reusing that layout would force a rewrite of
untouched partitions, so the proof uses a new versioned namespace instead of
silently changing the existing rows.

## Executable matrix

`mdbx_incremental_segmented_layout_proof_test` covers:

- stable dictionary identities and a monotonic dictionary high-water mark;
- deterministic postings, membership witnesses, token DF/CF, and collection
  statistics against `FlatLexicalIndex`;
- a replacement of one unit where the untouched token partition keeps
  byte-identical physical rows;
- no-op admission without a new publication sequence;
- stale expected state rejection before commit;
- fault after all writes, with MDBX rollback and reopen preserving the prior
  revision;
- physical admission rejection for checksum, missing/extra/orphan rows,
  malformed addresses, wrong generations, and witness corruption;
- one writable transaction for the changed unit, partitions, and metadata.

The proof also records test-only KV mutation calls. A sparse update has no
`insert_or_assign` or `erase` call for an unrelated partition, while the
telemetry explicitly records that the current algorithm still scans and
rebuilds the full logical index. Byte identity alone is not treated as proof
of zero physical mutation.

## Deliberately out of scope

This slice does not expose a production incremental importer, change the
`MdbxSegmentedLexicalIndex` API, alter `seg-v1` rows, add migration, or claim
scope-wide canonical/manifest consistency. A later PR may turn the proven
layout into a bounded adapter after measuring transaction size and recovery
costs.

## Adapter boundary for the next PR

The next adapter must not copy `ProofStore`. Existing reusable primitives are
`FlatLexicalIndex` for the conformance oracle and
`LexicalSegmentEncoding` for bounded row encoding, checksums, witnesses, and
partition reconstruction. The smallest additional storage helpers are point
lookup of one unit row, durable dictionary/high-water lookup, touched-token
delta calculation, local partition row replacement, and a physical reader that
admits all rows in one pinned transaction.

If those point lookups cannot be implemented without scanning all unit rows,
the next PR must stop and document the smallest required unit-contribution
metadata. The proof's `read_state -> rebuild -> tokens_for_unit_after` path is
deliberately retained as an O(N) oracle and must not become the durable adapter
hot path.
