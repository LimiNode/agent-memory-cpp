# NeuRoute fixed Top-M learned-router frontier

Date: 2026-09-02

## Question

Can a fixed-budget learned address router recover the final-boundary fidelity
that centroid K1 missed in PR #267, while limiting exact FP32 K8 to at most
8,192 occupied addresses?

## Leakage boundary and treatments

Requests 0--75 are configuration data. Every configuration prediction is
out-of-fold under four deterministic `request modulo 4` folds. Model family and
budget selection use the full frozen native R4 cascade, not classifier loss.
Only the best two distinct configuration treatments are retrained on all 76
configuration requests and evaluated on requests 76--151.

Targets isolate three notions of usefulness: discounted global FP32-K8 top-1024
rows, addresses containing full-FP32 exact top-10 documents, and their equal
hybrid. The frontier compares direct ridge multi-output prediction, rank-32 and
rank-64 target factorization, deterministic 256-dimensional nonlinear random
features, and a centroid-K1 plus learned rank-64 residual. Budgets are 2,048,
4,096, and 8,192 addresses.

The native diagnostic consumes hash-bound shortlist bytes, performs exact local
FP32 K8, and runs unchanged INT8 K32/R0, postings, Hamming768, ADC64, and exact
reranking. Reported Python router timing is directional batch-generation cost;
the native replay timing excludes router generation. PR #270 is responsible
for the in-process execution measurement.

## Result

No fixed learned router passed the registered gate. The compact result has
SHA-256
`6a90ef4840f4489a68dbc35d71ac784c85564908aea1e2ee0838dad293835af5`;
the validated evidence has SHA-256
`dd99ca5544d60c758f5044bc452d04724ff2216aa15a5ef37b6958735fa5d919`.

The maximum-budget OOF configuration rows were:

| Router, M=8192 | Teacher top-1024 coverage | Actionable-address coverage | Final overlap | Candidate retention | Mean / worst-stratum nDCG loss | Directional router / local-K8 p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Direct linear, global target | 0.6457 | 0.5928 | 0.6162 | 0.7134 | 0.1846 / 0.2236 | 0.38 / 31.43 ms |
| Direct linear, actionable target | 0.1412 | 0.1717 | 0.1724 | 0.1554 | 0.4853 / 0.5122 | 0.38 / 25.82 ms |
| Direct linear, hybrid target | 0.6438 | 0.5914 | 0.6149 | 0.7126 | 0.1850 / 0.2197 | 0.38 / 31.52 ms |
| Rank-32 hybrid | 0.6562 | 0.6020 | 0.6206 | 0.7223 | 0.1803 / 0.2156 | 1.35 / 31.14 ms |
| Rank-64 hybrid | 0.6438 | 0.5914 | 0.6149 | 0.7126 | 0.1850 / 0.2197 | 1.53 / 31.25 ms |
| Nonlinear random-GELU-256 hybrid | 0.5999 | 0.5636 | 0.5838 | 0.6644 | 0.2203 / 0.2534 | 0.51 / 31.03 ms |
| Centroid K1 + rank-64 residual | 0.9966 | 0.9474 | 0.9732 | 0.9917 | 0.0087 / 0.0217 | 4.30 / 31.58 ms |

Configuration opened the residual and rank-32 treatments. Retraining on all 76
configuration requests did not close the gap on locked internal:

| Locked-internal router, M=8192 | Teacher top-1024 coverage | Final overlap | Candidate / Hamming / ADC overlap | Mean / worst-stratum nDCG loss |
| --- | ---: | ---: | ---: | ---: |
| Centroid K1 + rank-64 residual | 0.9962 | 0.9654 | 0.9915 / 0.9914 / 0.9842 | 0.0174 / 0.0328 |
| Rank-32 hybrid | 0.6926 | 0.6566 | 0.7545 / 0.7632 / 0.7217 | 0.1360 / 0.1400 |

The residual is informative but not sufficient. Its approximately 99.6%
teacher-address coverage still omits about four global-K8 top-1024 rows per
query on average, and those omissions amplify at the final boundary. This is
direct evidence that teacher-set coverage alone is not a safe selection
surrogate for the full cascade.

## Decision

The fixed learned frontier is negative and does not license native integration
or production use. Direct address heads are too data-starved at 76 training
queries; adding a learned residual to the document-derived centroid order is
far stronger, but still misses the preregistered downstream gates.

The independently authorized #269 generator bake-off proceeds to test
document-only ANN over centroids and all K8 prototypes. It treats the residual
as a failed control, not as a selected production candidate.

## Limitations

- The learned frontier has only 76 configuration queries and therefore tests
  data efficiency as much as model capacity.
- The exact-document target is built only from permitted training requests;
  locked-internal labels never fit or select a model.
- A successful external-shortlist replay licenses the #269 bake-off and #270
  integration work, not production deployment.

## Reproduction

The ignored raw result is `tmp/neuroute-fixed-top-m-router/result.json`. Run the
fixed Top-M runner with the frozen configuration protocol, layout and K8
manifests, and safe native executable, then validate it with the paired evidence
writer.
