# THQ4 matched paper-faithful RSLM gate (2026-09-21)

## Question

Does the published RSLM codec retain the quality observed from the earlier
local FWHT/Lloyd-Max control when it is used as a residual codec after the
canonical THQ4 interval-squared top-128 filter?

## Source and protocol

The implementation is copied from the initial published official Google
Research notebook release:

* repository commit `34628fefe172e081abc9d0a368fabe0009975a7f`;
* notebook blob `41b4f1aca8bea8eba952d3ca874711f97e7025f2`;
* notebook SHA-256 `b89f7f820d43878d1db25d96b1be3f5aff7ae9820014143a2897d02e09d538ce`;
* paper `arXiv:2608.30384`;
* fixed `FLIPS`, `PERM`, C1D/C2D/C4D codebooks, two-pass block-128 FWHT,
  and UE7M9 are recorded in `rslm-faithful-reference.py`.

All six arms use the same frozen candidate stream, THQ4 interval-squared
top-128 filter, FP32 query, THQ4 centroids, and final cosine top-10 scorer:

* `rslm2/3/4-faithful`: official relative mode, including the inner UE7M9
  codec scale and the outer UE7M9 full-vector norm correction. The direct/raw
  codec payloads are 98/146/194 B; the relative residual records used here are
  100/148/196 B because they carry both two-byte scales;
* `rslm2/3/4-local`: historical randomized block-FWHT/per-coordinate
  Lloyd-Max control, without paper scale storage.

The local control fit was explicitly bounded to 8,192 training rows and two
iterations to keep the NumPy replay finite; it must not be compared to the
older full-fit local result as if those were the same protocol.

Inputs were independently found locally and match the existing canonical
receipts. The full replay covered all 152 queries and 463,258 unique candidate
documents. Raw result SHA-256:
`2cf18c5189f51e22af16baf6985743be73a32e19c26f07d498c9256b43a92e6b`.
The committed fail-closed source-binding audit is
`2026-09-21-thq-rslm-faithful-gate.audit.json`.
The canonical replay was first produced against the later notebook snapshot;
the pinned initial release differs only by explicit little-endian spelling.
The committed golden vectors were executed against the initial release and
match symbol, scale, and decode bytes, so the quality rows are unchanged by
that provenance correction.

## Result

| arm | side bytes | THQ4 + side | candidate-FP32 top-10 overlap | teacher overlap | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| RSLM2 faithful | 100 (98 raw + 2 outer) | 196 | 0.936184 | 0.931579 | 0.645689 |
| RSLM2 local | 96 | 192 | 0.936184 | 0.930921 | 0.652428 |
| RSLM3 faithful | 148 (146 raw + 2 outer) | 244 | 0.969079 | 0.963158 | 0.656721 |
| RSLM3 local | 144 | 240 | 0.969737 | 0.963816 | 0.660116 |
| RSLM4 faithful | 196 (194 raw + 2 outer) | 292 | 0.985526 | 0.978947 | 0.659201 |
| RSLM4 local | 192 | 288 | 0.975000 | 0.969737 | 0.660974 |

For orientation only, the pre-existing Faiss RQ audit on the canonical seed
reports RQ32 `0.656683` at 32 B/document side payload (128 B/document with
THQ4) and RQ48 `0.651655` at 48 B/document side payload (144 B/document with
THQ4). Those numbers are not relabelled as a matched replay here: their
assignments and fit artifacts are bound by the separate RQ audit.

## Interpretation

The paper-faithful transform and scale path are now executable in a full
152-query runner replay. The audit independently validates provenance,
cardinality, metric bounds, and storage accounting, but does not recompute
RSLM assignments or scores. On this fixture, faithful RSLM4 is close
to the bounded local control but uses four additional side bytes (two for the
raw codec scale and two for the relative full-vector correction) and a much
larger total cascade than RQ32/RQ48. This is evidence about this canonical
shell, not a production winner or a native-latency claim.

The committed golden-vector self-test covers the transform, UE7M9 values,
symbol assignment and packing, decode, and the final inverse transform. It is
bound to the pinned notebook release; the full audit remains source-binding,
not an independent RSLM assignment replay.

The result does not close the research wave. Remaining gates are a native
RSLM scorer/materialization, a full-fit local control with the same matched
protocol, a truly matched RQ32/RQ48 replay from persisted codes, and held-out
domain confirmation. RSLM4Lite is intentionally excluded until its embedded
scale bit packing is implemented and audited separately.
