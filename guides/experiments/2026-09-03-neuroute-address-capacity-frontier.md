# NeuRoute latent address-code capacity frontier

Date: 2026-09-03. This research continuation is intentionally separate from
#280: the R4 address ID is only 16 bits, so widths above 16 must use a learned
latent binary code rather than nonexistent higher address bits.

## Question

Does a query-supervised latent binary code for occupied R4 addresses continue
to improve bounded local-K8 retrieval at 16, 24, 32, 48, and 64 bits, and where
does the information-capacity curve saturate?

## Contract

The experiment uses the same frozen three R4 seeds, 8,141-query teacher cache,
configuration/reused-confirmation partition, authoritative E5 qrels, native
Hamming768 -> ADC64 -> exact-E5 cascade, and 1,024/2,048/4,096 address
budgets as #280. The 16-bit literal address-bit selector is retained as the
baseline. For widths above 16, a sparse rank-mass teacher matrix over occupied
addresses is factorized with deterministic sparse SVD; the signs of the top-D
right singular vectors are the learned address code. A projected query model
predicts signed code contributions, and occupied addresses are ranked by their
latent-code dot product. This tests representational capacity only; the
latent-code address scan is not a production path and global FP32 K8 remains
an offline teacher/reference.

## Result

The complete replay did not reproduce the monotonic trend seen for literal
12/14/16 address bits. At the fixed 4,096-address budget, the latent SVD-sign
construction was materially worse than the 16-bit literal baseline:

| code | configuration nDCG loss | configuration final overlap | locked-confirmation final overlap |
| --- | ---: | ---: | ---: |
| literal address bits16 | 0.0544 | 0.8180 | 0.7899 |
| latent sign24 | 0.1457 | 0.6298 | 0.5947 |
| latent sign32 | 0.1349 | 0.6469 | 0.6232 |
| latent sign48 | 0.1229 | 0.6772 | 0.6456 |
| latent sign64 | 0.1292 | 0.6759 | 0.6465 |

Offline teacher coverage shows the same failure (rank-weighted coverage
`0.7816` for literal 16-bit versus `0.6160/0.6317/0.6528/0.6620` for
latent 24/32/48/64). No candidate passed the registered full-cascade gates.
The curve is therefore not evidence of saturation at 64 bits; it is a
negative result for this particular SVD-sign latent construction. The
post-16-bit stopping rule remains empirical, but another wider run is not
justified without a materially different, better-motivated latent-code
objective (for example, supervised binary matrix factorization or a
query-to-code model trained with ranking loss).

Reproducibility bindings: `result.json` SHA-256
`22148a43d59ba199328ed27abe4a1a741c118bd038954abf4abec5ebf87593a6`;
validated `evidence.json` SHA-256
`a23b911a4a2379aabc88f7ad64fe7831a538d811479b0116e64cb5d637153d27`.

## Limitations

- The SVD teacher contains only the cached top-1,024 addresses per training
  query; unobserved addresses are implicit negatives.
- Latent code construction is a new representation family, not a wider
  physical R4 address. Its query-time exhaustive address scoring is diagnostic.
- The reused confirmation partition is not a pristine external holdout.
- Sparse-SVD and Python selector timings are directional; native timing covers
  only the downstream replay.

## Reproduction

Run `run-neuroute-address-capacity-frontier.py` with the validated policy,
configuration, R4 layout, K8 manifest, native executable, multilingual query
pool, and training-cache roots. Validate the result with
`write-neuroute-address-capacity-evidence.py`.
