# NeuRoute sequential actionable-gain oracle diagnostic

Date: 2026-08-29. Frozen implementation `5ad4687`; measurement complete.

## Question

#222 and #223 showed that richer static query-to-address scorers can improve
semantic quality without improving cumulative posting work. Is there measurable
headroom specifically from state-dependent marginal address utility, or is a
static cost-aware oracle already sufficient?

## Independence from #224

#224 correctly closed the conditional sequential branch of the earlier batch.
This study is a new contract motivated by those completed results. It binds the
exact #223 result/evidence and #224 closure bytes but does not change or reopen
their frozen decisions.

## Diagnostic

For each of the 76 German configuration queries, three scales, and three frozen
16-bit seeds, the privileged action pool contains only occupied addresses that
hold at least one exact-E5 top-10 document. The actionable objective is the
discounted fraction of those ten documents surviving Hamming768 and ADC64.

The study compares parent static schedulers, privileged static target-gain and
gain-density orders, and a one-address-at-a-time cascade-aware marginal-gain
density oracle. Candidate mass needed to reach 50%, 75%, 90%, and 95% actionable
gain is right-censored at 10% of the corpus.

Deterministic work counters report address scores, sequential rounds, cascade
evaluations, Hamming distance evaluations, and ADC distance evaluations. Wall
clock timing is intentionally excluded from the byte-replayable claim.

## Gate

At 75% actionable gain, the sequential oracle must reach at least 90% of
queries and, on every scale and seed, reduce censored candidate mass by at least
10% versus the privileged static gain-density oracle and 25% versus
`occupied_logit`. A teacher-forced student is licensed only if every check
passes. Production selection remains forbidden.

The separate German internal-evaluation partition is forbidden in this
diagnostic and remains reserved for a licensed student follow-up.

## Results

The complete 54-row matrix replayed byte for byte from authoritative qrels and
the frozen parent artifacts.

```text
result SHA-256:   d8478ed6556934a7e305d45a0c6aef3518fd0cf00d11052f76c554843f3cf80f
evidence SHA-256: 6857b3bbcab0ec8815e4ec14fc171757315c88667c0315cbcecac0b7552cb74c
```

Mean right-censored candidate fractions at 75% actionable gain, averaged over
the three frozen seeds, were:

| Scale | Treatment | Reach rate | Candidate fraction | Address scores / query |
|---|---|---:|---:|---:|
| DE-25k | `occupied_logit` | .8465 | .039282 | 18,985 |
| DE-25k | `static_target_gain_density` | 1.0000 | .000368 | 9.72 |
| DE-25k | `cascade_marginal_gain_density` | 1.0000 | .000368 | 52.36 |
| DE-100k | `occupied_logit` | .8772 | .036522 | 43,576 |
| DE-100k | `static_target_gain_density` | 1.0000 | .000175 | 9.72 |
| DE-100k | `cascade_marginal_gain_density` | 1.0000 | .000175 | 52.32 |
| DE-1M | `occupied_logit` | .8026 | .038991 | 65,113 |
| DE-1M | `static_target_gain_density` | 1.0000 | .000118 | 9.72 |
| DE-1M | `cascade_marginal_gain_density` | 1.0000 | .000118 | 52.34 |

The privileged target-density oracle reduces candidate mass by 99.0--99.7%
relative to `occupied_logit`, proving that the frozen document partition still
contains enormous cheap-routing headroom. State-dependent cascade greedy does
not realize additional headroom: its per-seed mass change versus static density
ranges from a 0.30% regression to a 0.29% improvement, with exact equality in
six of nine scale/seed checks.

The sequential oracle also evaluates roughly 52 candidate actions and complete
cascade states per query, versus about 9.7 target-density scores. Thus it is
strictly more expensive in deterministic work while producing the same mass
frontier under the frozen actionable-gain teacher.

## Decision and interpretation

All nine student-activation checks failed the required 10% mass reduction
versus privileged static density. `student_followup_licensed` and
`production_selection_licensed` are false.

This result changes the diagnosis suggested after #223. The large oracle gap is
not evidence that address utility must be sequential for discounted exact-E5
top-10 survival after Hamming+ADC. Because each document belongs to one address,
the useful target mass is almost additive; posting-cost normalization explains
the cheap oracle order. The remaining research problem is to predict
query-specific target gain per posting cost without privileged exact-E5 target
addresses.

The result does not exclude sequential benefit for a different genuinely
non-additive objective, such as explicit aspect coverage. It does reject the
predeclared claim that state-dependent cascade marginal gain is needed for this
actionable-retention target. Training the proposed block-sequential student now
would be an unlicensed post-hoc experiment, so the stacked follow-up records a
fail-closed activation closure instead.
