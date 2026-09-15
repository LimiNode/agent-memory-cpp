# FP32-free native finalist materialization (2026-09-15)

The corrected v2 oracle established this provisional logical finalist: ordinal THQ with a
96-byte/document payload followed by a linear INT8 scalar reranker with a
388-byte/document payload, for 484 logical bytes/document. This PR
materializes that pair over the corrected whole-posting candidate union. This
is a query-derived evaluation subset, not the persistent production table.

The frozen candidate stream contains 762,082 posting entries and 463,258
unique document IDs. The materializer writes, in ascending document-ID order:

* `thq3-ordinal.u8`: 96-byte packed ordinal records;
* `int8-linear.i8` plus `int8-scales.f32`: 388-byte INT8 records;
* `document_ids.i4`: the canonical mapping for both tables.

The independent audit checks file size/SHA, unique ID parity, byte-for-byte
THQ3/INT8 recomputation from the frozen source vectors, the
484-byte codec-payload contract, and all frozen input hashes. Including the
`document_ids.i4` mapping, this subset is 488 bytes/document. The receipt is
explicitly `PENDING_NATIVE_REPLAY`: no native scoring, OS/MDBX page count,
cold/warm latency, or production activation is claimed by this materializer.

The production representation remains a separate pending materialization over
all 1,000,000 documents: 96 MB THQ plus 388 MB INT8 (484 MB codec payload),
with no document-ID sidecar because position is the document ID.

The four binary payloads are retained in the local materialization directory
but are intentionally not committed to Git (the INT8 payload alone is about
170 MB and exceeds the repository's 100 MB object limit). Their absolute paths,
sizes and SHA-256 values are bound by `finalist.receipt.json`; a reviewer with
the frozen checkout can regenerate them deterministically before running the
audit.

The corrected replay fixes interval-squared ADC and expands the nonlinear
scalar controls, but corrected packed-ordinal accounting makes levels-4 the
same 96 bytes as levels-3. This THQ3 subset is therefore retained as a codec
control, not a canonical winner. Direct INT8 and levels-4 THQ top-128 followed
by INT8 are the native decision-gate arms.

The next corrective PR must consume these exact files in a native scorer and
compare THQ→INT8 top-10 with the candidate-local FP32 oracle. Only that replay
can decide whether the logical 484-byte finalist is worth physical layout and
MDBX work.
