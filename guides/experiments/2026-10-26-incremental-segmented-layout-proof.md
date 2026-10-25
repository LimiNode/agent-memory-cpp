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
- one writable transaction for the changed unit, partitions, and metadata.

## Deliberately out of scope

This slice does not expose a production incremental importer, change the
`MdbxSegmentedLexicalIndex` API, alter `seg-v1` rows, add migration, or claim
scope-wide canonical/manifest consistency. A later PR may turn the proven
layout into a bounded adapter after measuring transaction size and recovery
costs.
