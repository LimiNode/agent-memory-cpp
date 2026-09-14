# Full occupied-address native R4 route gate (2026-09-14)

## Question

Does the strong three-seed R4 quality frontier survive a full occupied-address
native route, and does unsigned INT8 representative storage preserve the FP32
route ordering for clipped `K=8/16/32` prefixes?

This is a native scalar route and quality-control experiment.  It is not an
AVX2/SIMD, MDBX, OS-page, or production-latency benchmark.

## Protocol

- frozen DE-1M fixture: 1,000,000 documents, 152 queries, 384 dimensions;
- three frozen semantic R4 seeds: `2026082701`, `2026082702`, `2026082703`;
- all occupied addresses (about 65k per seed), not the former 1,024-address
  shortlist;
- clipped representative prefixes `K=8/16/32`, with independently materialized
  `min(base_count, K)` sidecars;
- native scorer: unsigned INT8 uniform code plus per-record FP32 scale, scalar
  C++17, one warmup and one measured pass;
- FP32 reference: the same clipped representatives from `fp32.records`;
- three-seed fusion under one global unique-document budget of 5k/10k/20k/50k;
- final cascade: THQ4 interval-squared ADC top-256 and exact FP32 top-256 inside
  the native INT8 candidate set;
- teacher IDs are used only for evaluation, never for route construction.

The native runner records score and address-sort timing separately.  The route
quality runner records candidate recall, posting work, INT8-vs-FP32 route
agreement, and final THQ/exact teacher recall.  Payload columns remain logical
bytes; physical OS/MDBX page bytes are not measured.

## Results

### Candidate recall and cascade

| K | unique budget | INT8 mean | FP32 mean | INT8−FP32 mean | THQ top-256 | exact top-256 |
|---:|---:|---:|---:|---:|---:|---:|
| 8 | 5k | .921053 | .920395 | +.000658 | .921053 | .921053 |
| 8 | 10k | .941447 | .941447 | .000000 | .941447 | .941447 |
| 8 | 20k | .958553 | .958553 | .000000 | .958553 | .958553 |
| 8 | 50k | .979605 | .979605 | .000000 | .979605 | .979605 |
| 16 | 5k | .990789 | .990789 | .000000 | .990789 | .990789 |
| 16 | 10k | .994737 | .995395 | −.000658 | .994737 | .994737 |
| 16 | 20k | .995395 | .995395 | .000000 | .995395 | .995395 |
| 16 | 50k | .997368 | .997368 | .000000 | .997368 | .997368 |
| 32 | 5k | .999342 | .999342 | .000000 | .999342 | .999342 |
| 32 | 10k | .999342 | .999342 | .000000 | .999342 | .999342 |
| 32 | 20k | .999342 | .999342 | .000000 | .999342 | .999342 |
| 32 | 50k | .999342 | .999342 | .000000 | .999342 | .999342 |

The minimum recall is `.60` for K8 at 5k and `.90` for K16/K32 at the same
budget; the table reports means over all 152 queries.  Every audited row has
candidate recall equal to THQ top-256 and exact top-256 recall.  The rerankers
therefore do not recover a teacher document absent from the route candidate
set, and THQ introduces no additional loss on this frozen cascade.

### Native scalar route timing

The following values aggregate the three seeds and 152 queries.  They are
per-query arithmetic timings for the full occupied-address scan, not fused
end-to-end service latency.

| K | mean representatives/seed/query | score+sort p50 (ms, 3 seeds) | p95 | p99 |
|---:|---:|---:|---:|---:|
| 8 | 452,700 | 642.5 | 652.8 | 659.2 |
| 16 | 701,604 | 945.6 | 955.6 | 961.3 |
| 32 | 888,852 | 1,170.4 | 1,186.6 | 1,195.3 |

The p50/p95 columns above are quantiles over the 152 per-query sums; the
native receipt contains the exact per-seed samples.  Address sorting contributes
about 8 ms p95 per seed; representative score/max reduction dominates.

### INT8 versus FP32 route agreement

Across 3 seeds × 152 queries, the mean top-1024 address overlap is about
`1012/1024`, rank MAE is `229–237` addresses, score correlation is
`.999879–.999887`, and score MAE is about `.00024` for all K.  Quantization is
therefore not the limiting factor for this route; the dominant cost is the
full representative scan itself.

## Interpretation

1. The previously observed K16 frontier is reproduced on the full occupied
   topology, not only on the 1,024-address component.  K32 reaches `.999342`
   mean teacher recall already at 5k candidates; K16 reaches `.997368` at 50k.
2. Relative to the FP32 reference, INT8 changes candidate recall by at most
   one teacher out of ten on any row; aggregate differences are zero except
   for two isolated K8/K16 5k/10k rows, with absolute mean delta `.000658`.
3. THQ/exact rerank is downstream-safe but not a route-quality solution: all
   missing teacher IDs are absent before rerank.
4. A scalar full-address K16 route is about `0.95 s` per query over three
   seeds on this machine only as a component arithmetic measurement, and K32
   is about `1.17 s` before postings and payload reads.  These numbers are not
   MDBX or service latency claims.

The next architecture gate should therefore target representative-layer
route generation rather than another semantic-topology search:

- exact global representative top-R oracle (`R=128..8192`) to quantify how
  much of the K16/K32 frontier can be retained without scanning every
  representative;
- a hierarchical K1/centroid coarse scan followed by K16/K32 address refine;
- only if those controls retain the frontier, an ANN or SIMD/bit-packed
  representative layer followed by the existing postings → THQ → exact
  cascade.

HNSW and a new posting topology are not justified by this gate: route quality
  is already strong, while dense representative arithmetic is the measured
  bottleneck.

## Provenance and audit

Authoritative receipt: `EXECUTED`, SHA-256
`bc8d608eb8c83dcdcfbac26c3424681c52b2373f111fffec9450e56832ccc874`.

Raw output: 1,824 quality rows plus 1,368 route-metric rows, SHA-256
`f496ebc90ddf56d650857231d24bdd5fc91a5b3d344811979fa6bec8b60b0d7f`.

Independent audit: `PASS` (`1824` recall rows, `9` native outputs, `1368`
route-metric rows), audit SHA-256
`4cca70e2a1297940b848a29b4f4f20640878b1848ab1b4e9b2223735de352a70`.

Bound inputs:

- frozen THQ manifest: `f58e074e481dc910ca7bb12b35bc27dc51097640704fb2b0c749018bcf2edd57b`;
- R4 layout manifest: `95886a3b62eb0c2fc9182b721e94a252097395edc34d7604f7d11872bb5c039c`;
- R4 codec manifest: `1566688756f1922c9f3cce83c46c9623d2231f221bbe496b12ae978c0cdad8db`;
- native executable SHA: `cc5dffeb66d0b8ad372df2275153cc24641b2133a9cb8ac3308a32f60d5ac23a`;
- native runner source SHA: `af591addfa6b990cabda22153638de7db8aef87311875da8caee1c529e394c66`;
- quality runner source SHA: `460ab57898f9ae4038761781f8d57f1e267507a7f1fe7080bc1d5a42255c0c92`;
- audit source SHA: `419242925c43ba5501392e2dea2f69aa9240874ffb69607b1e930fd832d8bd31`.

The raw receipt and native artifacts are retained outside Git under
`E:\_repoz\agent-memory-workspaces\r4-full-native-route-raw`.  They are not
claimed as MDBX/page measurements.
