# NeuRoute R4 INT5 quantization anatomy

## Context

- Date: 2026-08-31
- PR: stacked on the nonlinear-INT5 layout-stress study
- Status: full DE-1M representative/component pass, query-conditioned routing
  replay, and independent evidence recomputation complete

## Question

Why does square-root-companded INT5 preserve the frozen R4 cascade while
uniform INT5 fails its quality gate? Does the measured error mechanism justify
opening a learned-codebook frontier, or is the fixed nonlinear codec already a
sufficient explanation and selection?

## Frozen protocol

This is an explanatory study, not another codec search. It reuses the same
2,666,557 FF32 representatives, frozen K8 top-1024 address shortlists, learned
R0 plus normalized max-cosine scorer, candidate boundary, and three internal
seeds from the preceding studies. Neither representative IDs nor scorer
weights are retrained.

The component pass covers all 1,023,957,888 representative components and
compares physical uniform INT5 with the selected power-`.5` INT5 codec. It
records normalized magnitude quantiles, code occupancy/entropy, and
reconstruction error in ten equal-frequency magnitude bins.

The query-conditioned replay covers 233,472 query/address pairs. It attributes
true `|q_i x_i|` contribution and `|q_i(x_i-xhat_i)|` error by component
magnitude decile, then measures representative argmax changes, learned address
score error, top-128 overlap, and accepted-candidate-set Jaccard. Argmax and
candidate-boundary changes are also stratified by the corresponding FP32
margin.

The learned-codebook frontier is licensed only if power-`.5` triggers at least
one preregistered mechanism signal:

- normalized code entropy at most `.75`;
- at least `.50` of query-weighted error in one magnitude decile;
- at least `.01` argmax disagreement when the FP32 winner margin exceeds
  `.01`;
- at least 20 queries with FP32 candidate-boundary margin above `.01`, of
  which at least `.05` have accepted-set Jaccard below `.80`.

Production selection is explicitly forbidden by this diagnostic protocol.

## Component distribution

The absolute component magnitude, normalized by each vector's maximum, is
concentrated below the vector extreme:

| Quantile | Normalized magnitude |
|---:|---:|
| p50 | .29907 |
| p90 | .60620 |
| p99 | .87354 |
| p99.9 | 1.00000 |

Both codecs use all 31 levels. Square-root companding does not expose a
low-entropy or collapsed codebook:

| Codec | Entropy, bits | Normalized entropy | `|code| <= 1` | `|code| <= 3` |
|---|---:|---:|---:|---:|
| Uniform INT5 | 4.52995 | .90599 | .15841 | .38502 |
| Power `.5` INT5 | 4.59220 | .91844 | .01563 | .08542 |

The nonlinear transform spreads small source magnitudes across substantially
more integer levels. It lowers reconstruction error most strongly in the
smallest deciles, but its aggregate component MAE/RMSE (`.002370/.002924`) is
slightly worse than uniform INT5 (`.002245/.002606`). Aggregate scalar error is
therefore not sufficient to predict routing quality.

## Query-conditioned mechanism

The largest source-magnitude decile contributes `.29283` of true absolute dot
mass, but no single decile dominates nonlinear reconstruction error. The
largest nonlinear error share is `.19863` in decile 10, far below the `.50`
license threshold.

| Metric | Uniform INT5 | Power `.5` INT5 |
|---|---:|---:|
| Representative argmax agreement | .89290 | .86615 |
| Argmax disagreement, FP32 margin `>.01` | .00023 | .00209 |
| Mean top-128 overlap | .93596 | .91889 |
| Mean accepted-set Jaccard | .88480 | .85496 |
| Stable-boundary query count | 3 | 3 |

Exact set identity is almost always broken because many learned address scores
are near the selection boundary, but overlap remains high. Only three of 228
seed/query cases have an FP32 accepted-boundary margin above `.01`, below the
minimum sample size of 20. One of those three nonlinear cases has Jaccard below
`.80`; that `.3333` fraction is diagnostic only and is not licensed with such a
small denominator.

The nonlinear codec also changes many within-address winners at tiny margins,
yet agreement rises to `.99791` for the 104,614 query/address pairs whose FP32
winner margin exceeds `.01`. The observed churn is therefore predominantly
near-tie reordering rather than stable-margin representative loss.

## Decision and interpretation

None of the four preregistered learned-codebook signals fires. The learned
codebook frontier is not licensed by this anatomy study, and no follow-up
codebook PR should be opened from these results.

The evidence supports a narrower mechanism: square-root companding uses the
five-bit alphabet more evenly and gives much finer resolution to the dense
small-magnitude region. Its scalar reconstruction error is redistributed, not
uniformly reduced. The frozen max-over-representatives router tolerates the
resulting near-tie winner and boundary churn, while the preceding held-out
cascade evaluation shows that final quality remains inside all gates.

Power-`.5` INT5 therefore remains the selected compact codec under the prior
conditional systems result: mixed INT5 for a memory-constrained working set,
homogeneous INT8 when the full store is resident. This PR explains that choice;
it does not supersede the earlier quality or layout decisions and does not
authorize a production merge.

## Limitations

- The corpus, FF32 basis, scorer, and internal queries remain the frozen DE-1M
  R4 protocol; this is not a cross-dataset codec claim.
- Magnitude deciles are query-independent component bins. They do not form a
  causal decomposition of every downstream score interaction.
- Only three queries have a stable FP32 candidate-boundary margin, so stable
  boundary behavior is explicitly treated as underpowered rather than good or
  bad.
- Code entropy measures occupancy, not hardware decode cost or vector-code
  mutual information.
- The study explains fixed-codec behavior and deliberately does not train or
  evaluate learned scalar/vector codebooks.

## Evidence

```text
result SHA-256:   6f6986d66e4159eb4e956d1a623bff74c7b92aace9d647fb11d201490870b39a
evidence SHA-256: 8036ddc09a65df00373fcb5e5067f61175bcf61d61c7b0f4668a3d8f2153267d
```

The evidence writer validates the component-distribution cache against the
frozen cardinality/codec contract and the recorded result, replays the
query-conditioned routing and boundary computation, and requires a
byte-identical regenerated result before emitting a passing evidence record.
