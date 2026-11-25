# OLD Library to Numeric Storage: Migration Plan

Status: PROPOSED, design only, 2026-11-21. Owner: storage compatibility.
Baseline: `main@acd5ad5545386b3feeaa79f8a587855b60801b4d`.
No migration executable, schema change, real database access or activation is
authorized by this document. See the
[product integration ADR](storage-product-integration-adr.md) and
[current source audit](storage-product-integration-audit.md).

## Four distinct gates

| Gate | Current evidence | Not established |
| --- | --- | --- |
| Historical commit-based replay | Exact historical OLD/NEW executables and the separately audited dependency pin transition; retained `HISTORICAL_ONLY` receipts | Numeric migration or arbitrary revision/dependency compatibility |
| Reject-old physical schema conformance | Fresh-process numeric fixture, reopen/recovery/corruption/OLD-unchanged controls | Execution by a genuine OLD library, full logical inventory, migration |
| Real OLD-library -> NEW-library migration | NOT_IMPLEMENTED / NOT_EXECUTED | All conversion, interruption and logical-equivalence gates below |
| Product activation on existing data | NOT_EXECUTED | Default switch, serving tails, rollback under real workload |

Until a real converter and its qualification execute, keep
`migration_executed=false` and `post_migration_upgrade_proof=false`.
Opening a fresh numeric store beside an old fixture is not conversion.

## Profile admission and executable prerequisites

Before choosing a supported OLD profile, inventory actual deployed schemas.
Admit only a fully named tuple:

`OLD library commit + dependency pins + compiler/ABI + domain/record codec +
physical DBI names/key widths/flags/comparators + profile signature`.

Use the real OLD library to create and independently read the fixture; record
the executable digest, build flags, source cleanliness and declared profile.
Do not handcraft its rows and relabel them as executable-generated evidence.
If the OLD binary cannot enumerate retired IDs, versions or authoritative
publication state, that is a migration blocker: first implement a reviewed
read-only exporter for that exact OLD profile. Do not guess missing ledgers.

The integrated libmdbx/mdbx-containers pins are not a general admission rule for
unknown old formats. Unknown markers, comparator mismatches, missing required
tables, mixed generations or an unrecognized body codec must fail before writes.

## Preparation and dry-run

1. Quiesce OLD writers and background indexing. Capture the authority owner and
   exact source frontier. No online dual writer is supported initially.
2. Take a backend-consistent backup/snapshot using the OLD-supported procedure.
   Record complete data/lock-file disposition, size, hashes, database identity,
   schema inventory and source frontier. Test restoring a separate copy.
3. Open only that separate copy for qualification. A lock file is operational
   state, not a portable logical schema or a substitute for backup validation.
4. Perform a read-only dry-run. Emit the complete logical inventory, conflicts,
   unsupported records, estimated bounds and proposed mapping, without creating
   DBIs, consuming allocation sequences or rewriting source metadata.
5. Reject if any record, retired identity, component, projection or provenance
   edge lacks an explicit conversion rule. Discarding unknown records is not a
   successful migration policy.

## Full logical inventory

Capture deterministic, versioned export records for:

- all live, erased, superseded and retired logical IDs and allocator high-water
  marks, including holes and used-ID ledgers;
- resource generations, current/withdrawn manifests and historical revisions;
- canonical documents, block identities, structure order/parents, every retained
  block/document revision, metadata and body/materialization recipe;
- KU envelopes, kinds, scopes, content keys, lifecycle, supersession and lineage;
- source identities, external IDs, exact source revision tokens and digests;
- artifacts, bindings, retention/availability, representations and evidence
  anchors, preserving unavailable raw material as unavailable;
- required component/projection versions and publication authority/frontiers;
- pending intents, receipts, tombstones and recovery ownership. Initially
  require these to be settled offline or reject; do not import a live lease.

Counts, sorted logical hashes and cross-reference checks must cover the entire
inventory. A small sample or current-head-only export cannot establish lossless
migration. Byte-order-sensitive persisted digests retain their original recipe.

## Conversion into a fresh target

The first supported design is offline shadow conversion, not in-place DBI
reinterpretation. OLD remains immutable and independently readable.

- Create a fresh target namespace/environment only after profile admission.
  A physical comparator/key-width change uses a new DBI generation.
- Preserve portable logical/source/digest identities. Allocate physical numeric
  document/block keys with explicit forward/reverse remapping; their values do
  not have to equal the OLD addresses.
- Preserve KU numeric identity and no-reuse where its allocator domain is
  retained. If importing into a different existing domain, require an explicit
  total remap of every reference and retired ID; do not silently merge domains.
- Carry retired identities and allocator history, not only currently live rows.
  The fresh namespace loader is not a replacement operation for a live mapping.
- Import immutable versions before publishing current heads. Preserve independent
  document/block/unit/projection revisions; never renumber them to make unrelated
  counters equal.
- Rebuild derived indexes under their exact recipe/version. Rebuildability does
  not permit inventing missing canonical bytes or replacing `Source@1` by `@2`.
- Validate each bounded batch and journal progress in a versioned migration
  manifest outside the serving authority until final publication is reviewed.

The manifest must bind source backup/executable/profile, target namespace/pins,
converter version, options, mapping hashes, batch digests and completion state.
Its schema, location, durability and DBI budget remain review prerequisites;
this plan does not install them.

## Interruption, repeat execution and rollback

The initial safe policy may explicitly prohibit automatic resume: an incomplete
target is quarantined and a new empty target is created from the unchanged
source. That is safer than claiming idempotency without a verified journal.

If resumable conversion is selected instead, each journaled batch must be
idempotent under the exact source/target/converter tuple. Check allocator and
retired-ledger state as well as payloads. Duplicate, reordered, changed-source
and changed-options resume attempts must reject. A journal entry cannot claim
completion before its target writes are durably committed and independently
validated. Test death before and after each journal/publication boundary.

Rollback before activation means discarding/quarantining the incomplete shadow
target and continuing with the unchanged OLD snapshot. Activation is a separate
host operation: quiesce, verify the target, switch a host-owned route, reopen and
observe. Once NEW accepts writes, reverting to OLD loses those writes unless a
separately qualified reverse replay exists. Therefore the first activation must
use an explicit read-only observation window or prohibit rollback after writes.
Do not promise automatic reverse conversion.

## Acceptance before any activation request

The future converter qualification must run separate OLD-create, OLD-export,
NEW-convert, NEW-read and OLD-recheck processes with outer time/resource limits
and isolated temporary MDBX roots. Require:

1. Complete logical inventory equivalence, not just physical file hashes.
2. Exact scope, digest/provenance and historical-revision equality.
3. Retired-ID rejection and allocator monotonicity after fresh-process reopen.
4. Rebuilt indexes return only authorized, current-bound results; historical
   access remains explicit and exact.
5. Crash/interruption controls, repeated invocation policy, backup restore and
   rollback gates, malformed/unknown format rejection and no source mutation.
6. Installed C++17 OLD/NEW consumers and the named compiler/platform matrix.
7. Receipts bind all executed binaries, profiles, options, hashes and observed
   results. Only then may migration flags become true for that exact run.

Current prerequisites available for reuse are the numeric conformance harness,
physical schema/comparator checks, installed-consumer fixtures and the retained
historical replay. Missing prerequisites are the admitted actual OLD profile,
complete read-only exporter, migration journal/converter and proven numeric
product publication path. None is supplied by changing a receipt label.
