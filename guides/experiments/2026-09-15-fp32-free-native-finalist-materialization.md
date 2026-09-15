# FP32-free native finalist materialization (2026-09-15)

The pre-correction v2 oracle selected a provisional logical finalist: ordinal THQ with a
96-byte/document payload followed by a linear INT8 scalar reranker with a
388-byte/document payload, for 484 logical bytes/document. This PR
materializes that pair over the corrected whole-posting candidate union.

The frozen candidate stream contains 762,082 posting entries and 463,258
unique document IDs. The materializer writes, in ascending document-ID order:

* `thq3-ordinal.u8`: 96-byte packed ordinal records;
* `int8-linear.i8` plus `int8-scales.f32`: 388-byte INT8 records;
* `document_ids.i4`: the canonical mapping for both tables.

The independent audit passed and checks file size/SHA, unique ID parity, the
484-byte logical contract, and all frozen input hashes. The receipt is
explicitly `PENDING_NATIVE_REPLAY`: no native scoring, OS/MDBX page count,
cold/warm latency, or production activation is claimed by this materializer.

The four binary payloads are retained in the local materialization directory
but are intentionally not committed to Git (the INT8 payload alone is about
170 MB and exceeds the repository's 100 MB object limit). Their absolute paths,
sizes and SHA-256 values are bound by `finalist.receipt.json`; a reviewer with
the frozen checkout can regenerate them deterministically before running the
audit.

Because the interval-squared implementation in the v2 runner was corrected
after this materialization, this payload is now a reproducible historical
control, not an accepted codec selection. The next corrected v2 replay must
confirm or supersede the 484-byte choice before these files are used for a
production decision.

The next corrective PR must consume these exact files in a native scorer and
compare THQ→INT8 top-10 with the candidate-local FP32 oracle. Only that replay
can decide whether the logical 484-byte finalist is worth physical layout and
MDBX work.
