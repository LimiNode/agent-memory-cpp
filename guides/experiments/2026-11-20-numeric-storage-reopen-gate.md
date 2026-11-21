# Numeric storage reject-old and reopen qualification

Date: 2026-11-20. Context: `feat/numeric-storage-conformance`, stacked on the
corrected numeric KnowledgeUnit/identity/flat adapters and DBI manifest.

## Question and expected result

Can an opt-in numeric v2 environment preserve typed KnowledgeUnits, identity
frontiers and canonical history across fresh processes, reject damaged physical
state, and explicitly reject the original flat-v1 schema without changing its
decoded contents? Expected: every assertion succeeds, no migration is claimed,
and incomplete or failed execution cannot emit a successful receipt.

## Reproduction

Configure an MDBX-enabled C++17 test build, then build
`agent_memory_numeric_storage_conformance` and run:

```sh
ctest --test-dir <build> -R '^agent_memory_numeric_storage_conformance$' --output-on-failure
```

The Python driver bounds each native process to 20 seconds; CTest bounds the
whole gate to 180 seconds. Every invocation creates a fresh artifact directory
under `<build>/tests/numeric-conformance-<config>/run-*` and retains a JSON
receipt. CI uploads only receipts, not database/lock files. Receipts include
the exact source commit, dirty-source flag, dependency tree, executable/source/
manifest hashes, commands, exit codes and captured diagnostics.

## Local observation

Windows 11, MinGW GCC 15.2, Release, MDBX ON, JSON OFF: the 19-process gate
passed. The focused batch also passed 4/4 tests: canonical flat multi-revision
qualification, identity mapping, typed KnowledgeUnits and this process gate.
The retained local artifacts are under `.build/storage-int-gate/tests/`.

The fixture uses thirteen DBIs: five typed unit tables, four shared identity
tables and four canonical tables. It verifies physical counts/flags, two active
units in separate scopes plus a deleted-ID ledger entry, mutable unit revision,
content/kind indexes, eight active/retired identity records and sequence
frontiers, four revisions of one canonical document and another scope's
document. Decoded envelopes, portable canonical records and identity records
form the logical snapshot; canonical revision and block reads are checked
against the reference model. Deleted canonical IDs and retired identities
cannot be reused.

Five controls remove a block row, remove a reverse identity row, corrupt the
flat schema, corrupt the typed-unit schema and install an incompatible
comparator. Each must fail at its intended diagnostic. A separate process exits
24 before head publication; the next process must read the unchanged committed
snapshot. Two normal NEW reopen processes must agree independently.

## Interpretation and limits

The OLD input is a real MDBX **physical-schema fixture**, with decimal composite
keys and AMFS/AMFB v1 records as defined at
`3e21501be41c165a900bd203c494784d3d5bbcba`. It is not an OLD library binary. Its
records are decoded before and after explicit rejection by the NEW adapter;
NEW numeric data are created separately and checked against the same logical
reference. This is `POST_NUMERIC_REJECT_OLD`, not an executed data migration.

The receipt deliberately retains `migration_executed=false` and
`post_migration_upgrade_proof=false` on PASS. OLD-binary compatibility, actual
migration/rebuild execution, interrupted-migration recovery, production
activation, performance and authoritative provenance admission are
**NOT_EXECUTED** by this gate. Synthetic revision-bound source summaries test
stored binding parity, not an authoritative source-frontier decision.

Follow-up: independent source review, exact-head CI and the broader combined
MDBX/JSON/platform matrix. Any future migration must have its own executable
OLD-binary/NEW-binary evidence rather than relabel this receipt.
