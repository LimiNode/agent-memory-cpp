# Segmented lexical conformance

The conformance slice exercises the isolated `seg-v1` MDBX adapter without
expanding its consistency claim. It covers:

- reopen after first publication, revision replacement, erase, and clear;
- deterministic before-commit and after-write fault points;
- corruption rejection through the physical row checksum;
- a pinned lexical reader that remains on the old generation while a writer
  publishes on another thread, followed by a fresh-reader check;
- stale expected-unit rejection;
- parity of active units, dictionary identities, token DF/CF, and collection
  statistics against the existing `flat-v2` adapter.

The tests intentionally use the same MDBX connection where the library's
thread-affinity contract requires it, and release pinned read snapshots before
starting another transaction on that thread. They do not claim canonical or
manifest linearizability and do not protect against an old binary that ignores
the `seg-v1` profile boundary.
