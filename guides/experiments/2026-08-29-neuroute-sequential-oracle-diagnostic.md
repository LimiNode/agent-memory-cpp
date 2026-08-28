# NeuRoute sequential actionable-gain oracle diagnostic

Date: 2026-08-29. Independently frozen protocol; measurement pending.

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
