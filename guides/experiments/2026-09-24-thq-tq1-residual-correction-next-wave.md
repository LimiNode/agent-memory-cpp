# THQ4 → canonical TQ1 residual-correction next wave

Date: 2026-09-24
Status: `SOURCE_AVAILABLE; PARTIALLY_EXECUTED` — the canonical source bundle
has been recovered and the first source-bound strong-PQ, LSQ, and QJL replay is
recorded in [the executed residual replay note](2026-09-24-source-bound-residual-replay.md).
OPQ, TQ-domain normalized correction, full-25k LSQ, and a fresh held-out query
confirmation remain pending; no production choice is made here.

## Recovered source binding

The persisted E5 bundle is currently available at the legacy payload location
`E:\_repoz\agent-memory-cpp\tmp\native-ann-confirmation-v1\de-1m\e5`.
It is the exact materialization described by `manifest.json`:

| input | shape/count | SHA-256 |
|---|---:|---|
| evaluation documents | `1,000,000 × 384`, float32 little-endian | `d4f67ebe91faa159eaaeb7884281ad0d0057c27cdb67c4007f260c6442636007` |
| evaluation queries | `305 × 384`, float32 little-endian | `fa6c467e01bbe8a8e725d75fd5ac84c90008235be921c4a4d360d756d0d0d7b2` |
| train vectors | `25,000 × 384`, float32 little-endian | `1f581860cff679989f0661fb27623c650bc130e8ed4593b0abe08e5474c00b80` |
| evaluation qrels | `3,144` rows | `5b3d22a491558ae0955a7a968c4fec9974ddc463cb5f83fc67334cbfc87ab071` |

The read-only validator is
`tools/agent-memory-bench/validate-canonical-de1m-source.py`; it checks the
manifest, exact byte sizes, model/schema metadata, ID uniqueness, qrels
membership/cardinality, and every source SHA. Its optional `--deep` mode
streams all 1M document rows (and all query/train rows) and verifies actual
unit norms rather than trusting only the manifest flag. The 2026-09-24 deep
validation passed with maximum norm error `1.83e-7`.
A junction is now present at
`E:\_repoz\agent-memory-workspaces\canonical-de1m-source\payload` and has
passed the same validator.  It avoids a second 1.5-GB copy while the legacy
materialization is audited.  Do not copy or mutate the payload implicitly from
a research runner; the legacy target remains the source of truth until a
future byte-for-byte migration is explicitly verified.

The ordered relationship between this historical split and the recovered
305-query source is bound by
`2026-09-24-canonical-query-lineage.receipt.json`. It proves exact float32
query-vector equality, the persisted 152-row order, and 1,557 qrels matches,
plus the exact 153-row complement. It does not prove the historical selection
rule, so the complement is not labelled an untouched holdout. Until that
selection lineage is recovered, results are reported as `legacy-152`
reproduction or descriptive `full-305` aggregates, never as a blind
153-query confirmation.

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
   Also run the TQ-domain error arm: with TQ's persisted normalized residual
   `u = R(r) * sqrt(D) / ||r||` and decoded `û`, quantize `δ_TQ = u - û`
   directly and reconstruct the correction as
   `R⁻¹(δ̂_TQ) * ||r|| / sqrt(D)`. This arm reuses the TQ residual-norm
   sidecar and must be accounted separately from the raw residual-norm arm.
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
   The scorer must report a source-norm serving arm and a TQ-reconstruction-norm
   ablation; QJL is not a reconstructed-vector claim. An exact `||b+e||`
   denominator is not a distinct oracle here because `e = x - b`, hence
   `b + e = x`; if an oracle is needed, expose the exact numerator `q·x`
   separately rather than counting a duplicate denominator arm.
   The implementation exposes separate `estimate_dot_reference(...)` and
   `estimate_dot_rademacher_control(...)` entry points; there is no generic
   scorer that can silently apply Gaussian scaling to a Rademacher matrix.

For the QJL cosine rows, report the denominator variants explicitly. Because
the canonical E5 vectors are L2-normalized, the primary serving-shaped arm
uses the persisted source norm (normally `1`) and computes
`(q·b + q·e_hat) / (||q|| ||x||)`. A `||b||` denominator from the frozen TQ1
base is a separate ablation, not the production reference. Do not report
`||b + e||` as another arm: with the defined residual it is exactly the source
norm and duplicates the primary arm.

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

The 152-query collection has already been reused for exploratory architecture
decisions. A 76/76 split is useful for diagnostics, but it is not an untouched
final test. Any positive codec claim must therefore be confirmed once on a
new, pre-registered query/qrels set. Training losses must use judged-positive
versus judged-negative pairs (or an explicitly materialized hard-negative set);
unjudged candidate documents are excluded from training loss.
