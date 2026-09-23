# THQ4 → canonical TQ1 residual-correction next wave

Date: 2026-09-24
Status: `PLANNED` — source-bound execution pending restoration of the canonical
1M bundle.

The bounded PQ4/PQ8 replay does not justify the broad statement “residual
correction fails”. It only establishes that a raw-space PQ trained on 1,024
rows with four Lloyd passes did not improve frozen canonical TQ1. The next
wave must separate fit weakness, residual-domain mismatch, and objective
mismatch.

## Required matched arms

All arms use the same frozen THQ top-128 stream, canonical 25k-trained TQ1
payload, final cosine evaluator, and identical query/qrels split.

1. **Strong raw-space PQ control** — PQ4/PQ8, 25k residual-training rows,
   25 Lloyd iterations, three deterministic restarts; report the selected
   restart and training distortion.
2. **Rotated-normalized residual PQ** — encode
   `δ = R(e) * sqrt(D) / ||e||` in the TQ rotated domain, persist the residual
   norm, and decode with `R⁻¹(δ̂) * ||e|| / sqrt(D)`. This is a distinct codec,
   not a relabeling of raw-space PQ.
3. **OPQ4/OPQ8 residual control** — fit the rotation and product codebooks
   only on the residual-training fold; persist rotation provenance and use an
   independent decode audit.
4. **Full-dimensional LSQ4/LSQ8 or AAQ4/AAQ8 correction** — optimize the
   small residual with an additive model rather than independent PQ blocks.
   Reconstruction and ranking-aware objectives must be reported separately.
5. **QJL score correction** — use `q·e` estimation directly with packed
   signs at `m = 32/64/128` (4/8/16 bytes) plus residual norm. The reference
   arm uses a persisted Gaussian projection matrix, for which the
   `sqrt(pi/2)` estimator has the stated unbiasedness; a Rademacher projection
   is a separate fast control and must not be merged into the reference claim.
   The scorer must report both a TQ-norm denominator arm and an exact corrected
   norm upper control; QJL is not a reconstructed-vector claim.

For the QJL cosine rows, report the two denominator variants explicitly. The
serving-shaped arm uses `||b||` from the frozen TQ1 base and computes
`(q·b + q·e_hat) / (||q|| ||b||)`. The exact-corrected-norm arm is an oracle
upper control using `||b + e||`; it is not a persistable serving score and must
never be counted as a production byte budget.

QJL storage accounting must include `m/8` sign bytes plus one `fp32` residual
norm per document, and the global projection matrix (`m × 384 × fp32`) as a
separate model payload. The matrix is shared and is not multiplied by the
document count, but omitting it would make the 4/8/16-byte labels misleading.

## Mandatory diagnostics

For every query and every arm, record:

- residual reconstruction MSE before/after correction;
- absolute error of the exact FP32 cosine score;
- pairwise ordering agreement against exact document scores;
- exact top-10 overlap and teacher top-10 overlap;
- qrels nDCG@10, p05, and worst-query loss;
- persisted bytes and bytes touched for `K=32/64/128`.

For every arm, report the paired delta against frozen canonical TQ1 for both
the reconstruction metrics and qrels metrics. A lower reconstruction error is
not a retrieval win unless the held-out ranking and qrels deltas agree.

This prevents conflating better E5 geometry with better qrels relevance. A
quality decrease with lower reconstruction error is an objective-mismatch
signal, not evidence that all residual cascades are invalid.

## Adaptive correction policy

The current global median-gap policy is retained only as a baseline. A proper
adaptive gate must estimate per-document score uncertainty and correct only
when the uncertainty interval can change the top-10 order. QJL provides a
sample-standard-error proxy, not a distribution-free confidence interval;
calibration thresholds must be learned on a separate query fold and then
evaluated once on held-out queries. The audit must record the projection
distribution, seed, calibration fold, and correction-decision counts.

No production codec selection is allowed until at least one of the stronger
residual-domain or score-aware arms beats canonical TQ1 on the same held-out
queries with source-replay and independent decode evidence.
