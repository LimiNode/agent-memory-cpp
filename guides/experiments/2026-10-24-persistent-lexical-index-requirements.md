# Persistent lexical IndexStore requirements and oracle handoff

Date: 2026-10-24
Base: `liminode/main` at `c1dfb7a3` (includes projection-aware retrieval PR #533)

This is a requirements/evidence handoff, not a production storage change. It
does not define a competing MDBX schema and does not alter canonical content,
`ChunkPayload`, `SegmentSet`, compression, or retrieval defaults.

## Architectural boundary

The required boundary is:

`Canonical Storage → SearchProjection → Persistent Lexical IndexStore → Retrieval → Canonical Hydration`

Canonical revisions, source identity, content bytes, and semantic unit identity
remain authoritative. Search projections and lexical indexes are durable,
versioned derived structures. A scorer may change without rewriting canonical
data; a tokenizer change creates a new lexical index epoch. Retrieval must bind
all candidates and statistics to one published snapshot, or return an explicit
`Partial`/`Unavailable` outcome while derived data catches up.

## Current capability matrix

| Capability | Status on current `main` | Handoff boundary |
| --- | --- | --- |
| `SearchProjection`, `ProjectionVersionRef`, durable `Original` projection | implemented/production | source for derived indexing |
| In-memory `ExactLexicalIndex` BM25 control | implemented/production | correctness control, not persistent IndexStore |
| Projection-aware retrieval and publication checks | implemented/production | must remain revision-bound |
| Persistent term dictionary and postings | contract-only | next storage implementation |
| Persistent TF/DF/document statistics | contract-only | same snapshot as postings |
| Active snapshot, epoch/generation publication and recovery | contract-only | atomic publication/fencing required |
| BM25F field materialization | contract-only | requires per-field stats from projections |
| WAND/BMW bounds, posting compression, positional blocks | missing/deferred | separate execution/compression PRs |

Existing normative shapes live in
[`guides/lexical-search-roadmap.md`](../lexical-search-roadmap.md):
`LexicalPosting`, `LexicalTokenStats`, `LexicalCollectionStats`,
`LexicalStatisticsSnapshotRef`, and `LexicalActiveSnapshot`. This handoff
reuses those names instead of introducing a second model.

## Required persisted identity

Every derived row and every cache/block header must be attributable to:

```text
scope_id
projection_kind
projection_schema_id / projection_schema_version
tokenizer_fingerprint / normalization_fingerprint
statistics_epoch
statistics_generation
posting_layout_version
unit_id + ProjectionVersionRef
resource_id + resource_generation
```

The DBI key is authoritative for scope and projection. Values must not carry a
second conflicting copy of those fields. `statistics_epoch` is never reused
for a different index incarnation; generation advances within one epoch for
incremental updates.

The immutable epoch descriptor carries schema, tokenizer, normalization, epoch,
and supported layout fields. The active pointer carries descriptor identity,
current generation, and layout. Posting and unit-stat rows additionally carry
unit_id, ProjectionVersionRef, resource_id, and resource_generation; collection
statistics and token dictionaries are collection-level rows and must not invent
per-unit fields.

## Shared data contract for scorers

BM25, BM25+, and BM25L must be able to consume the same published base data:

- normalized token dictionary and token IDs;
- term-to-posting entries with unit ID, field ID, TF and optional positions;
- per-unit field/token lengths;
- DF, collection frequency, unit count, total token count, and average lengths;
- explicit scorer parameters (including BM25+/BM25L delta), IDF convention,
  score tolerance, and tie policy.

BM25F additionally requires per-field statistics and field weights. Phrase and
proximity require positions/locators. WAND/Block-Max WAND require scorer- and
snapshot-bound upper bounds; BM25 bounds must not be reused silently for BM25L
or a different statistics generation.

## Epoch, generation, and read-transaction semantics

statistics_epoch is the immutable identity of one index incarnation. A
tokenizer, normalization, schema, full rebuild, incompatible layout migration,
or restore over potentially stale rows allocates a new epoch.
statistics_generation is a logical collection-wide publication counter within
that epoch; a targeted add/remove/replace advances it without copying the whole
collection. A physical MDBX MVCC/read transaction is a storage snapshot and is
not itself an epoch or generation. Immutable posting blocks also record the
generation they were built from. A same-epoch block can be used as an unscored
base only when a complete add/remove/replace overlay reconstructs active
membership; otherwise the route is Partial or Unavailable.

## Publication and update requirements

1. Build a complete new epoch or generation namespace before publication.
2. Remove the old active contribution from aggregate statistics before adding
   its replacement; stale physical rows may wait for compaction.
3. Publish one active snapshot pointer containing epoch, generation and layout.
4. Readers resolve the pointer first, then read all stats/postings with that
   exact prefix in one read frontier.
5. An epoch mismatch is obsolete. A same-epoch generation mismatch is usable
   only with a complete add/remove/replace overlay; otherwise return
   `Partial`/`Unavailable` and never score the stale block as exact.
6. Update, erase, crash recovery, and reopen must not resurrect old postings or
   expose a unit whose projection version is newer than the index row.

## Oracle and acceptance gates

The companion fixture
[`persistent_lexical_oracle_v1.json`](../../tools/lexical-research/fixtures/persistent_lexical_oracle_v1.json)
is intentionally small and independent of the production implementation. It
covers:

- exhaustive BM25/BM25+/BM25L ranking over shared TF/DF inputs;
- independent resource-level and chunk-level statistics, with a mixed arm kept
  only as a diagnostic;
- deterministic ties and reopen-stable scores;
- update replacement, erase/tombstone, stale epoch, and incomplete overlay;
- projection-version and resource-generation mismatches;
- scorer-bound versus incompatible block-max metadata.

The implementation must pass these gates before execution optimization is
accepted:

| Gate | Required result |
| --- | --- |
| exact top-K and score parity | equal to exhaustive oracle for each scorer within the fixture tolerance |
| active snapshot binding | no cross-epoch/generation statistics mixing |
| update/erase | no stale or resurrected candidates |
| incomplete overlay | explicit `Partial`/`Unavailable`, never a complete result |
| reopen/recovery | same published snapshot gives same scores/ties |
| acceleration | WAND/BMW is exact under matching bounds, otherwise disabled |
| storage boundary | no canonical bytes or semantic IDs rewritten for indexing |

## Non-goals and follow-up

This PR does not choose BM25+ or BM25L for production, implement BM25F,
introduce MDBX DBIs, or benchmark codecs. Those require independent PRs after
the IndexStore contract is accepted. The next implementation PR should map
these requirements onto the existing roadmap types and add a persistent
IndexStore with exact oracle parity before adding WAND or compression.
