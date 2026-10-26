# Incremental segmented lexical point-update design

Date: 2026-10-26

Status: design-only Draft; no production implementation

Related proof: PR #556, `seg-v2-proof` executable layout proof

## Decision question

Can a single-unit update avoid the current global
`read_state -> rebuild -> tokens_for_unit_after` path while retaining the
existing lexical identity, expected-state, MDBX transaction, rollback, and
pinned-reader contracts?

The answer is conditional:

- The segmented namespace already has addressable `unit/<unit-id>` rows and
  deterministic dictionary keys, so a unit contribution and a token identity
  can be looked up without a second unit index.
- The current flat-v2 payload cannot provide those lookups: its units and
  dictionary are serialized in one value, so locating one unit requires
  reading and decoding the whole value. Flat-v2 therefore remains a rebuild
  baseline and is not a point-update target.
- The existing `seg-v1` rows do not carry a partition-local generation and
  require all segments to equal the global snapshot generation. They cannot
  be changed in place for this design. The adapter needs a new versioned
  segmented layout, bounded by an explicit migration boundary.

This is a feasibility and cost contract, not authorization to implement the
adapter.

## Existing addressable rows

The current segmented adapter demonstrates the useful physical primitives:

| Family | Key | Value needed by an update |
| --- | --- | --- |
| unit | `unit/<canonical-unit-id>` | serialized `FlatLexicalUnitInput`, including the normalized-token sequence |
| dictionary | `dictionary/<hex(token UTF-8)>` | token text and durable `TokenId` |
| high-water | `high-water` | next token id; only advances, never reuses ids |
| segment | `segment/<token-id>:<segment-id>` in seg-v1 | posting segment, but no independent partition generation |
| token stats | `token-stats/<token-id>` | DF and CF |
| witness | `witness/<token-id>` | membership witness for the posting partition |
| collection stats | `collection-stats` | active-unit and total-token counters |

`unit/<id>` is the current unit contribution. Its token list is sufficient to
compute the old token multiset; no second unit-to-token index is required for
the first adapter slice. A dictionary lookup computes the same canonical
`hex_component(token)` key used by the existing segmented layout, so a new
token can be admitted or allocated without scanning the dictionary. Lookup
cost is still a database-tree lookup, not an O(1) promise.

## Minimal new metadata family

The smallest durable addition needed by the incremental layout is:

```text
partition-meta/<token-id>
    value: { layout_version, statistics_epoch, partition_generation }
```

The exact encoded value must be a bounded, versioned binary record. It must
validate the token id, layout version, statistics epoch, and a non-zero
generation. `partition_generation` is a lifetime high-water mark: it advances
for every touched token, including a token whose active postings become empty,
and is never decremented or deleted.

Why this family is necessary:

1. Existing `seg-v1` rows identify a token and segment but not a generation
   independent of the collection snapshot.
2. Rewriting the global snapshot generation into untouched rows defeats the
   point of a sparse update.
3. A reader needs to distinguish an old segment generation from the active
   partition generation and reject mixed generations in one pinned read.

The unit row, dictionary rows, stats, witness, and collection counters already
provide the other required information. No generic `UnitContributionIndex`,
`PartitionManager`, or transaction framework is justified by this slice.

### Migration and storage boundary

`seg-v1` remains readable only under its existing all-segments-equal-global-
generation contract. The new adapter uses a new layout/version marker and
requires an offline rebuild or a quiesced migration from seg-v1. Legacy rows
must not be interpreted as if they had partition generations. During
migration, old writers are stopped; after publication, the new reader admits
only the new version. This is one explicit boundary, not indefinite dual
serialization.

The additional metadata cost is one small row per historical token. A token
that is deactivated keeps its metadata row, so dictionary and generation
high-water storage are monotonic. Segment, stats, and witness rows for an
empty active partition may be removed while `partition-meta` remains.

## Point-update algorithm (design contract)

All steps below occur in one writable MDBX transaction.

1. Read and validate the descriptor, layout marker, publication sequence,
   collection counters, dictionary high-water, and the target
   `unit/<unit-id>` row. An absent unit is an insert; a present unit supplies
   the old normalized-token multiset and revision metadata.
2. Apply `expected_current` admission to the unit row before any write. A
   stale unit revision, scope, projection kind, or projection-version tuple
   fails closed.
3. Resolve every new token with direct dictionary lookup. Allocate ids only
   for absent normalized token text, from the durable high-water; never reuse a
   historical id. The dictionary key and value are checked for a one-to-one
   text/id relationship.
4. Form `T`, the union of old and new token ids. For each touched token, read
   only its current `partition-meta`, stats, witness, and segment rows. Admit
   the current partition generation and reconstruct that one posting
   partition using the existing segment decoder and witness checks.
5. Remove the old unit posting from each old-token partition and add the new
   posting to each new-token partition. Recompute only the touched token's DF,
   CF, membership witness, and bounded segment rows. A shared token therefore
   updates its local counts once, even when several units reference it.
6. Advance each touched token's partition generation, preserving its previous
   high-water even when the resulting partition is empty. Replace only the
   touched segment/stat/witness rows; leave unrelated token rows byte-identical.
7. Update the target unit row, newly allocated dictionary rows, dictionary
   high-water, collection counters by delta, global snapshot generation, and
   publication sequence. The publication marker is written last in the same
   transaction.
8. Commit. Any exception, failed physical admission, stale expected state, or
   injected interruption aborts the transaction and leaves every prior row,
   generation, counter, and publication marker visible after reopen.

The read path opens one pinned read-only transaction. It reads the publication
marker and the physical rows required by the requested snapshot from that same
transaction, validates descriptor/layout/epoch, partition generations,
segment addresses, statistics, and witnesses, and keeps the transaction owner
alive for the returned frontier. A full validation/conformance reader may scan
the namespace, but the single-unit writer must not use that scan as its hot
path.

## Cost model

Let:

- `N` = active unit count;
- `V` = historical dictionary entry count;
- `L_old`, `L_new` = token occurrences in the replaced unit before/after the
  update;
- `T` = number of distinct token ids in the old/new union;
- `P_t` = decoded postings (or encoded bytes/rows) in touched token partition
  `t`;
- `S_t` = number of segment rows in touched partition `t`;
- `H` = postings in the hottest shared token partition touched by this update.

For the proposed segmented adapter, the expected work is bounded by:

```text
unit lookup                 O(log N) database lookup + O(L_old) decode
dictionary resolution       O((L_new distinct) * log V) lookups
partition reads             sum_t O(P_t + S_t) for t in T
delta/statistics work       O(L_old + L_new + T)
partition writes            sum_t O(P_t + S_t) plus O(T) metadata rows
global metadata             O(1) rows, plus newly allocated dictionary rows
```

This is not an O(1) update. A shared-hot token can make one update
`O(H)` because its posting partition must be reconstructed and re-encoded;
partitioning by token alone does not remove that bound. `N` disappears from
the normal point-update term only because the unit and dictionary rows are
addressable. If either lookup degenerates to a namespace scan, the cost
returns to at least `O(N + total active token occurrences)` and the adapter
must not be presented as bounded.

The flat-v2 baseline remains approximately:

```text
read/decode payload       O(N + total active token occurrences)
rebuild and publish       O(N + total active token occurrences)
serialized write          O(payload bytes)
```

The meaningful benchmark counters are therefore rows/bytes read and written,
`T`, `sum(P_t)`, `sum(S_t)`, `H`, transaction duration, and final MDBX map growth,
not
only elapsed wall time.

## Minimal production API (future slice)

The adapter should expose only the already established lexical surface:

```cpp
void upsert(
    FlatLexicalUnitInput input,
    std::optional<FlatLexicalExpectedState> expected_current = std::nullopt
);
bool erase(const KnowledgeUnitId& unit_id);
MdbxSegmentedLexicalIndexSnapshot read_snapshot() const;
```

The public API does not expose partition generations, posting internals, or
transaction handles. Diagnostics may report bounded counters (rows/bytes
read/written, touched-token count, and transaction duration) under an
explicit test/benchmark option, but diagnostics are not part of correctness.

## Acceptance slices before implementation

An implementation may proceed only after these deterministic controls exist:

- point lookup of an existing, missing, and stale unit row;
- direct dictionary lookup, stable TokenId, and high-water after restart;
- insert, replacement, deletion, and no-op with `T=0`, `T=1`, and multiple
  touched tokens;
- a shared-hot token with two or more units, checking DF/CF deltas;
- untouched-token byte identity and zero mutations across segment,
  partition-meta, stats, and witness families;
- generation monotonicity through deactivation and reactivation;
- one writable transaction for all rows, rollback after each publication step,
  and reopen recovery;
- expected-state conflict, stale writer, malformed/missing/extra physical row,
  wrong generation, checksum, and witness negatives;
- one pinned reader that observes either the old or new publication, never a
  mixed partition set;
- matched comparison with the `FlatLexicalIndex` oracle for contents,
  ordinals, statistics, dictionary identity, and collection counters.

The benchmark must report `N`, `V`, `T`, `sum(P_t)`, `sum(S_t)`, `H`, rows/bytes read
and written, transaction duration, and MDBX map growth for representative
local, shared-hot, insert, replace, delete, and no-op workloads.

## Scope decision

**GO for a separate bounded-adapter implementation PR**, but **NO-GO for
implementation in this design PR** and **NO-GO for claiming O(1)**. The next
PR may implement the minimal new `partition-meta` family and the single-unit
transactional path only after preserving the existing seg-v1 boundary and
passing the acceptance slices above. It must not copy the proof store into
production or change flat-v2, seg-v1, the importer, or the publication
coordinator in the same change.

This document deliberately leaves the implementation uncommitted until the
row/byte cost model and shared-hot behavior are measured against the oracle.

## Compatibility addendum

This addendum is the design gate for a future production `seg-v2` adapter. It
does not add a public type, persisted row, DBI, coordinator dependency, or
implementation code.

### Independent identities and versions

The following identities are deliberately separate:

| Identity/version | Meaning | Current owner or representation |
| --- | --- | --- |
| DBI manifest version | Which physical tables exist and their lifecycle/budget rules | `guides/dbi-manifest.yaml`, currently v3 |
| retrieval index family | Classical lexical, learned sparse, or another derived family | logical descriptor/key namespace; no new runtime type in this gate |
| index descriptor identity | Scope, projection kind, schema identity and compatible index incarnation | `LexicalIndexDescriptor` plus versioned descriptor payload |
| projection schema/version | Meaning and source revision of the indexed projection | `ProjectionVersionRef`, projection store contract |
| tokenizer/normalization/vocabulary identity | Token text and dictionary semantics | existing tokenizer/normalization fingerprints; learned vocab must add its own fingerprint |
| model/scoring identity | Model revision, codec identity, and scoring semantics | required descriptor fields for learned sparse; absent from classical BM25 |
| posting layout version | Physical posting row/key semantics | `posting_layout_version`; must not imply scoring version |
| payload encoding version | Serialization of one descriptor, unit, stat, or segment row | per-family encoded envelope/version |
| statistics epoch | Which index incarnation owns DF/CF statistics | `LexicalIndexDescriptorRef.statistics_epoch` |
| collection publication generation | Coherent published collection state | `LexicalActiveSnapshot.statistics_generation` and publication sequence |
| partition-local generation | Monotonic generation for one token partition | future `partition-meta` family; never alias global generation |
| source revision | Canonical/projection revision that produced one unit contribution | `FlatLexicalUnitInput` and `ProjectionVersionRef` |

No one field may be used to infer another identity. In particular, a layout
version is not a scoring-algorithm version, and equal numeric `TokenId`
values from different vocabularies are never interchangeable.

### Explicit physical contract for seg-v2

The proof namespace `seg-v2-proof` is test-only and currently reuses the
existing codec constant `kLexicalSegmentLayoutVersion = 1`. That reuse is
acceptable only because the proof namespace is not admitted by production
readers. The production adapter must not publish this combination.

Production seg-v2 must carry a distinct physical contract identifier (the
initial proposal is `posting_layout_version = 2`) in its descriptor, key
namespace, segment envelope, and partition metadata. The existing segment
wire shape may be reused only if its envelope validates version 2 and its
documented `statistics_generation` meaning is partition-local. Otherwise a
new payload encoding version is required. A seg-v1 reader must reject seg-v2
rows; a seg-v2 reader must reject seg-v1 rows, unknown layout/encoding values,
wrong epoch, wrong descriptor, and global-generation semantics presented as a
partition generation.

The compatibility fixture for the implementation gate must contain these
negative pairs and expected result:

| Stored rows | Reader | Result |
| --- | --- | --- |
| seg-v1 global-generation rows | seg-v2 reader | reject before publication |
| seg-v2 partition-generation rows | seg-v1 reader | reject as unsupported layout/namespace |
| unknown layout or payload encoding | either reader | reject, never heuristic decode |
| seg-v2 rows with wrong epoch/descriptor | seg-v2 reader | reject |
| seg-v2 row whose generation is collection-global | seg-v2 reader | reject semantic admission |
| old reader after new active-root switch | old reader | reject/ignore unknown root, never read new rows as old |

### Algorithm compatibility audit

The storage contract must not force every retrieval algorithm into
`FlatLexicalPosting::term_frequency`:

| Algorithm family | Reuse classical postings? | Required boundary |
| --- | --- | --- |
| BM25 / BM25+ / BM25L | yes, when scoring only changes | scoring identity and formula stay outside posting layout |
| BM25F / field-aware | not without fields | field-aware posting metadata or a separate field namespace |
| positional phrase/proximity | not with TF-only rows | positions/payload encoding and a distinct layout contract |
| WAND / Block-Max WAND | maybe | block-max metadata is a new payload/metadata contract; query planner remains separate |
| DeepImpact / uniCOIL / SPLADE / BGE-M3 sparse | no shared TokenId assumption | independent vocabulary, model fingerprint, weighted impacts and derived-index identity |
| INT8/INT5 impact quantization | only with explicit codec identity | quantization scale/codec version is part of payload identity |

Learned sparse impacts are not integer term frequencies. A learned sparse
index may share canonical units and source revisions, but it owns its
vocabulary, model/scoring identity, statistics, and active snapshot. It must
not reuse classical `TokenId` values without a proven vocabulary equivalence.

### Rebuildable migration lifecycle and DBI budget

The first production migration is rebuildable and adjacent, not in-place:

```text
old active -> new building -> new validated -> atomic activation -> old retired
```

The builder reads canonical units/projections, writes the new logical
namespace inside the existing lexical KV DBI where possible, validates all
rows against the independent `FlatLexicalIndex` oracle, and changes one active
root/descriptor reference atomically. Before activation, crashes leave the old
active root untouched; during activation, MDBX rollback leaves either old or
new metadata; after activation, the old root remains until an explicit retire
operation. Unknown roots and incompatible writers fail closed. Rebuild is
quiesced when old writers could otherwise publish into the new namespace; no
online migration guarantee is made.

The manifest already accounts for the lexical families and a default
`max_dbs_default: 96` with `minimum_free_slots: 16`. The design therefore
prefers a new key namespace in the existing lexical table (migration peak is
additional map bytes, not an automatic DBI) and adds no DBI in this gate. If a
future implementation needs a separate DBI, it must provide a manifest row,
`max_dbs` admission, peak-map estimate, insufficient-map/max-dbs negative
tests, and a measured reason that the namespace cannot provide equivalent
access. A generic MigrationManager is out of scope.

### Gate and patch-drift audit

The current PR scope remains narrow:

- PR #556: executable proof/test and experiment note only; production C++ LOC
  delta is zero.
- PR #557: design documentation only; no public API, production type, DBI,
  codec, or coordinator dependency was added.
- The proposed production delta is one `partition-meta` family and one
  versioned segmented adapter, not a universal index/migration framework.

The existing proof reuses the checked-in segment codec, token identities and
`FlatLexicalIndex` oracle. It does not copy its test-only `ProofStore` into
production. Any implementation diff that changes importer, canonical storage,
publication coordinator, learned model inference, or an unrelated profile is
patch drift and must be removed or split.

**Design gate result:** GO for one separate Draft implementation PR with an
explicit seg-v2 physical contract and the compatibility negatives above;
NO-GO for activating it, changing the default backend, executing migration,
or coupling it to the coordinator in this design PR.
