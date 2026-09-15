# THQ versus FP32-removal gate (2026-09-15)

## Question

Can the production path omit the 1,536-byte/document FP32 E5 store after the
three-seed whole-posting R4 candidate stream, or is a compact final reranker
still required?

## Protocol

The frozen 152-query THQ fixture and the corrected #411 whole-posting stream
(5,000–5,099 candidates/query) are shared by every arm. Ranking is evaluated
inside the same candidate set, so candidate survival is reported separately
from final ordering:

| Arm | Payload estimate |
|---|---:|
| THQ interval² thermometer | 144 B/document |
| packed ordinal THQ | 96 B/document |
| symmetric per-document INT8 reranker | 388 B/document (384 B + scale) |
| symmetric compact INT4 reranker | 196 B/document (192 B + scale) |
| FP32 exact comparator | 1,536 B/document |

The runner records exact-top10 overlap against candidate-local FP32, survival
of the frozen full-corpus teacher top-10, qrels nDCG@10, and pairwise rank
inversions within the FP32 top-256. The raw receipt is outside Git and all
frozen input hashes are bound in the receipt.

## Results

| Arm | Exact-top10 overlap | Teacher top-10 recall | Qrels nDCG@10 | Top-256 inversions |
|---|---:|---:|---:|---:|
| THQ thermometer | .8145 | .8132 | .6380 | 26,985 |
| Packed ordinal | .8145 | .8132 | .6380 | 26,985 |
| INT8 reranker | .9941 | .9868 | .6562 | 302 |
| INT4 reranker | .6586 | .6553 | .5603 | 7,028 |
| FP32 exact | 1.0000 | .9928 | .6542 | 0 |

Packed ordinal is exactly rank-equivalent to thermometer for this fixture, so
the 96-byte representation is a storage improvement but not a quality change.
Direct THQ top-10 is not a replacement for FP32: its `.8145` candidate-local
overlap is far below the predeclared `.99` gate. INT4 is also insufficient.
The compact INT8 arm clears the overlap gate (`.9941`) and is within the
candidate-local FP32 qrels score (slightly higher here, `.6562` versus `.6542`),
while reducing the dense store from 1,536 to about 388 B/document. This supports
removing FP32 from production in favor of packed THQ → top-256 → INT8 rerank,
subject to a native INT8 materialization/kernel replay and held-out qrels check.

## Limitations and next check

The INT8/INT4 arms are deterministic NumPy controls, not the native codec or
SIMD kernel. Qrels nDCG is measured on the routed candidate set, not a new
full-corpus end-to-end replay. The next required experiment is native INT8
document materialization plus the same top-256 rerank, followed by a held-out
query/domain confirmation. Until that passes, FP32 remains an offline oracle,
not a required production payload.

Independent audit: `semantic_thq_fp32_removal_gate_audit_v1`, 760 rows, PASS.
