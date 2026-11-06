# Shared `_unit_projections` DBI conformance

This note records the executable conformance slice for the physical
`<prefix>_unit_projections` DBI shared by `MdbxProjectionStore` and
`MdbxDerivedPublicationStore`.

The tested boundary is deliberately narrow:

- both owners use one `MdbxStorageContext` and one custom table prefix;
- projection and derived-publication key families coexist and round-trip;
- two projection families with different generations coexist without key
  aliasing;
- deleting a projection does not remove publication rows;
- a caller-owned read transaction can read the publication family;
- malformed projection payloads and keys are rejected;
- rows survive close/reopen with the same prefix.

The current result is **SAFE for the tested key-family, reopen and malformed-
payload scope**. It is not a claim that every future key family is compatible,
nor a claim of cross-process writer serialization beyond the MDBX contract.
No DBI rename, migration or production activation is performed by this slice.
