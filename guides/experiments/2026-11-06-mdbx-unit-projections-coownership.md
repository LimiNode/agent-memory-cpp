# MDBX `_unit_projections` Co-ownership Conformance

Status: executable conformance slice on `main` `92a2cefd`.

This experiment qualifies the existing shared `_unit_projections` DBI without
changing its name, key layout, payload codecs, or DBI manifest. The same
custom-prefix database is opened through:

- `MdbxProjectionStore` for Original and DenseContextual rows;
- `MdbxDerivedPublicationStore` for the body/manifest/active-marker key family;
- `MdbxSnapshotRevisionBoundRetriever` for projection production and final
  revision-bound admission;
- `MdbxResourceImporter` as the producer of a real lexical Original row.

The executable is
`agent_memory_mdbx_unit_projections_coownership_test`.

## Qualification matrix

| Invariant | Result | Evidence |
|---|---|---|
| Key-family isolation | SAFE | Projection scans ignore `derived_publication:` rows; derived reads ignore projection rows. |
| Owner-specific scan/delete | SAFE | Projection deletion removes only the requested kind/revision; DenseContextual and all derived fields remain readable. |
| Reopen parity | SAFE | Both owners reopen the same custom prefix and recover all surviving rows. |
| Malformed payload rejection | SAFE | Corrupt derived manifest returns `Unavailable`; corrupt projection payload throws and does not affect derived reads. |
| Foreign-key safety | SAFE | Deleting an Original row cannot delete another projection kind or a derived publication. |
| Snapshot reader compatibility | SAFE | A real importer-created Original row is admitted by the snapshot reader through the shared prefix. |
| Mixed read snapshot | SAFE | One read transaction sees the pre-write projection/publication pair while a concurrent writer publishes both later rows; post-commit reads see both new rows. |
| Crash/recovery beyond reader-level rollback | INCONCLUSIVE | This slice does not claim durable crash injection or process-restart recovery for the shared DBI. |

## Interpretation

The existing key-family separation is sufficient for the tested co-ownership
surface. No additional DBI or migration is required by this conformance slice.
`SAFE` is limited to the listed invariants and does not certify a general
multi-process publication coordinator, crash recovery, or production activation
of derived retrieval.

