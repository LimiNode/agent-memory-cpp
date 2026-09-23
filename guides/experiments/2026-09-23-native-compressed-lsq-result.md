# Native compressed LSQ cascade result

Date: 2026-09-23
Status: `EXECUTED` / source-bound candidate-local gate

The direct C++ scorer was run twice for each packed Faiss LSQ payload over the
frozen 152-query R4 stream. The timed region contains THQ4 byte-LUT filtering
and direct compressed-code cosine scoring; no FP32 vector is materialized.

| arm | side bytes/doc | warm run | THQ p50/p95/p99 ms | codec p50/p95/p99 ms | total p50/p95/p99 ms |
| --- | ---: | --- | --- | --- | --- |
| LSQ32 | 36 (32 code + FP32 norm) | run 2 | 1.371 / 1.492 / 1.573 | 2.184 / 2.431 / 2.707 | 3.551 / 3.908 / 4.159 |
| LSQ48 | 52 (48 code + FP32 norm) | run 2 | 1.414 / 1.544 / 1.628 | 3.479 / 3.695 / 3.878 | 4.904 / 5.204 / 5.351 |

Independent audits pass for both arms. Ordered top-10 parity is `152/152`;
THQ top-128 set parity is `152/152`. The two ordered THQ differences are
deterministic equal-score tie-order swaps and do not change the retained set
or final top-10. Candidate-local codec pages average `92.97` (LSQ32) and
`103.74` (LSQ48); shared-model pages are `3074` and `4610`. Hypothetical
row-aligned full-corpus codec pages are `8790` and `12696`, respectively.

The page audit uses packed row positions for the candidate-local payload and
counts only shared codebooks/centroids as model pages. It does not claim OS
page latency or full-corpus serving performance. Cold/warm here means two
consecutive native process runs on one host; no production codec is selected.
