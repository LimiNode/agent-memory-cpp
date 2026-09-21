# THQ4 matched paper-faithful RSLM gate (2026-09-21)

## Question

Does the published RSLM codec retain the quality observed from the earlier
local FWHT/Lloyd-Max control when it is used as a residual codec after the
canonical THQ4 interval-squared top-128 filter?

## Source and protocol

The implementation follows the initial published official Google Research
RSLM notebook (`arXiv:2608.30384`). Provenance is recorded at three levels:

* initial repository commit: `34628fefe172e081abc9d0a368fabe0009975a7f`;
* content-changing commit: `40b1135c9eb083dee0edb513c2723ca65e289e8f`;
* later repository snapshot: `4700efb9afa54286b0e04473ba80a13e8461e25f`;
* notebook blob: `41b4f1aca8bea8eba952d3ca874711f97e7025f2`;
* notebook SHA-256: `f16c92855f0ae55dd36442cf30f09c18aa5fcceff9b80beb4e9db48aa4a9b315`.

The reference module records the fixed `FLIPS`, `PERM`, C1D/C2D/C4D codebooks,
two-pass block-128 FWHT, and UE7M9 implementation. The RSLM4 zero-vector
record is canonicalized to zero symbol bytes and zero scale, matching the
official codec. Committed golden vectors cover transform, UE7M9, symbol
assignment/packing, decode, and the zero-vector edge case.

All six arms use the same frozen candidate stream, THQ4 interval-squared
top-128 filter, FP32 query, THQ4 centroids, and final ranking boundary:

* `rslm2/3/4-faithful`: official relative mode, including inner UE7M9 codec
  scale and outer UE7M9 full-vector norm correction. Direct/raw codec payloads
  are 98/146/194 B; relative records are 100/148/196 B because they carry both
  two-byte scales;
* `rslm2/3/4-local`: historical randomized block-FWHT/per-coordinate
  Lloyd-Max control, without paper scale storage.

The primary paper-faithful line is MIPS/IP: exact and approximate scores are
dot products, and the faithful relative arm retains the positive outer scale.
The cosine-adapted line is a separate product diagnostic: the outer scale is
omitted from the score because positive scale cancels in cosine ranking. The
canonical document and query inputs are checked to be unit-normalized within
`1e-4`; observed maxima are `1.7881393432617188e-7` and
`1.1920928955078125e-7` respectively.

The local control fit was bounded to 8,192 training rows and two iterations to
keep the NumPy replay finite. It must not be compared to the older full-fit
local result as if the protocols were identical.

## Evidence

The full replay covered all 152 queries and 463,258 unique candidate
documents. Final raw result SHA-256:
`584a6d58ff75a91cee585a454af3ac315d4d66890da350675d8be1d970623135`.

The committed compact summary is
`2026-09-21-thq-rslm-faithful-gate.result.json`; its raw-result binding is
checked by the fail-closed audit
`2026-09-21-thq-rslm-faithful-gate.audit.json`.

The audit rehashes every supplied corpus, candidate, producer-module, and
runner path. It reports `source_binding: true`, `source_replay: false`, and
`rslm_assignment_replay: false`: this is real source binding and structural
validation, not an independent reimplementation of RSLM assignment.

## Result

| arm | side bytes | THQ4 + side | IP candidate overlap | IP nDCG@10 | cosine candidate overlap | cosine nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| RSLM2 faithful | 100 (98 raw + 2 outer) | 196 | 0.936842 | 0.649370 | 0.936184 | 0.645689 |
| RSLM2 local | 96 | 192 | 0.894079 | 0.636814 | 0.936184 | 0.652428 |
| RSLM3 faithful | 148 (146 raw + 2 outer) | 244 | 0.970395 | 0.654857 | 0.969079 | 0.656721 |
| RSLM3 local | 144 | 240 | 0.938158 | 0.657863 | 0.969737 | 0.660116 |
| RSLM4 faithful | 196 (194 raw + 2 outer) | 292 | 0.980263 | 0.658635 | 0.985526 | 0.659201 |
| RSLM4 local | 192 | 288 | 0.953289 | 0.651702 | 0.975000 | 0.660974 |

For orientation only, the pre-existing Faiss RQ audit on the canonical seed
reports RQ32 `.656683` at 32 B/document side payload (128 B/document with
THQ4) and RQ48 `.651655` at 48 B side payload (144 B/document with THQ4).
Those values are not relabelled as matched RSLM rows: their assignments and
fit artifacts are bound by the separate RQ audit.

## Interpretation and status

The official transform, codec, inner scale, outer scale, and both scoring
semantics are now executable in one full 152-query runner replay. On this
fixture, faithful RSLM4 is close to the bounded local control but uses four
additional side bytes and a much larger total cascade than RQ32/RQ48. RSLM3
remains a genuine intermediate finalist; RSLM2 is deprioritized against the
smaller RQ32 baseline. These are quality-only NumPy results, not native
latency or production-selection evidence.

The faithful source/codec question is **confirmed for implementation and
source provenance**, with the explicit boundary that assignment replay is not
independent. RSLM4Lite is excluded: the official notebook does not support it
in residual mode, so a `THQ base + residual RSLM4Lite` construction would be a
product-specific variant and must not be called faithful RSLM.

Remaining work is a native finalist gate with the fixed THQ4 exact byte-LUT
filter and alternative final arms `THQ-joint2`, `RQ32`, `RSLM3`, `RSLM4`, and
`INT8`, followed by held-out-domain confirmation. FastScan is the THQ filter
kernel, not an additional final-codec arm. A full-fit local control and a
matched RQ32/RQ48 replay remain optional sensitivity checks rather than
prerequisites for the source/codec conclusion.
