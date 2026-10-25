# Isolated MDBX segmented lexical adapter

This experimental adapter is the storage slice layered on the physical row
contract in [`lexical-segment-physical-contract.md`](lexical-segment-physical-contract.md).

It uses a `seg-v1` keyspace inside the existing key/value table. The keyspace
is intentionally disjoint from the existing `flat-v2` payload: no old bytes
are reinterpreted, and no rolling upgrade or implicit dual writer is enabled.
The adapter is admitted only for `Original` projection and layout version `1`.

The keyspace persists descriptor and active-snapshot rows, publication sequence,
historical token dictionary/high-water, active unit contributions, posting
segment rows, token statistics, collection statistics, and per-token witnesses.
One writable MDBX transaction replaces the lexical keyspace for a publication;
an abort therefore leaves the previous complete lexical snapshot visible after
reopen. The current implementation is an explicitly bounded rebuild baseline:
it rewrites the segmented rows in one transaction, but does not serialize the
entire index as one value. Incremental segment replacement is deferred to a
later measurement-driven slice.

Readers load all rows through one pinned read transaction. Every segment is
decoded with the physical codec, partitions are admitted with the read-integrity
helper, witnesses and token/collection statistics are checked against the
replayed logical `FlatLexicalIndex`, and incomplete or mismatched state fails
closed. The read frontier is lexical-only; it does not claim canonical,
projection, manifest, or scope-wide coordinator linearizability.

The adapter deliberately has no DBI manifest change. A future production
profile must establish an explicit migration/quiescence boundary before
coexisting with `flat-v2` in one scope.
