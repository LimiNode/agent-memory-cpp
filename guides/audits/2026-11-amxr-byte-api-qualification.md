# Additive AMXR byte API qualification

Date: 2026-11-24. Corrective foundation slice based on
`main@77a278bef338074227efa05365e83e967334f429`.
Owner: [identity policy](../identity-policy.md#additive-binary-buffer-api).

## Change and unchanged contracts

Add `serialize_external_object_ref_bytes` / `deserialize_external_object_ref_bytes`
using the existing ByteBuffer/ByteView. A shared encoder emits directly into
either vector or legacy string with one exact-size reservation. The decoder
borrows the input frame, materializes only owning domain fields, and rejects
null/nonempty input before access. Legacy functions remain linkable with their
original signatures and exceptions; no ambiguous overload is introduced.

AMCE nested AMXR and importer source-key/budget helpers consume byte output
directly. The source key takes the returned vector by move rather than building
a string and assigning another container. Ordering compares unsigned bytes.
The existing SHA-256 string-view API has an explicit no-copy internal adapter;
hash input/preimage is unchanged. No standalone AMXR/AMCE format version,
persisted bytes, ID equivalence, resource limit, default profile, DBI or required
dependency changes. No unrelated strings/legacy public domain IDs are replaced.

## Verification controls

`external_object_ref_test` retains independent authored AMXR literals/digest and
validates byte/legacy parity. It exercises signed/unsigned/text/composite keys,
NUL/FF payloads, exact 65536-byte bound, all existing invalid/truncated/oversized
cases, null/0 and nonnull/0 malformed frames, null/nonzero rejection, owning
return values after input overwrite, repeated use of one input buffer, canonical
reserialization and byte/legacy agreement across deterministic bit mutations.
Legacy signatures remain exercised by installed example consumers.

Existing AMCE goldens and source-import regressions prove nested compatibility;
existing failure-injection suites check named external-copy, store and importer
atomicity. Passing those suites is not a claim that arbitrary STL variant copying
is repaired; the named exception-safe copy paths remain unchanged.

Local baseline qualification uses GNU 15.2 and MSVC 19.44, C++17 Release,
MDBX/JSON/Zstd OFF, examples+tests ON: full CTest and installed consumers.
Exact final HEAD/check totals, independent review and remote CI are attached to
the Draft PR after execution, not inferred from this source file.
Optional-MDBX/JSON/Zstd CI lanes, sanitizer runs and relocated consumers must be
reported separately if not run. There is no measured latency/RSS speedup claim;
the removed source-key staging conversion follows directly from ownership/dataflow.

No merge, release, migration, DBI deployment or durable implementation is part
of this correction.
