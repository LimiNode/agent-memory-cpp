# NeuRoute decoupled relevance and candidate-cost policies

## Context

- Date: 2026-08-30
- PR: stacked on the frozen feasible candidate-work frontier
- Status: full DE-1M training, sealed evaluation, and independent replay complete

## Question

Can the R3c address representation recover the privileged sparse frontier when
relevance supervision is separated from posting cost and the latter is applied
only by a hard-budget query policy?

## Protocol

The DE-1M 16-bit partition, K8 exact top-1024 shortlist, R3c input
representation, 8,141 pseudo-supervised training queries, 76 configuration
queries, 76 internal queries, three route seeds, and Hamming768 -> ADC64 ->
exact-E5 cascade remain frozen. Five models per seed use identical initial
weights, optimizer schedule, four epochs, and parameter count:

- gain-density ListNet control;
- unweighted BCE for `P(cascade-useful address)`;
- expected actionable gain;
- graded exact top-100/top-10/cascade membership;
- Lambda-style hard-negative loss at the 5k-candidate boundary.

The exact top-100 teacher is materialized once over the full DE-1M corpus.
External pseudo queries receive the same validated teacher-blind query
projection used by the prior objective ablation; native German query cascade
inputs remain authoritative. Posting size is deliberately absent from BCE
importance weights.

Every frozen model is calibrated only on configuration queries. Configuration
also chooses among predicted gain, predicted gain/cost, gain minus lambda cost,
and useful logit minus lambda log-cost. The latter two use a seven-value lambda
grid. Policies greedily skip non-fitting addresses and report the unique
candidate union under a hard `.005` fraction. Internal qrels are opened only
after all 15 model archives and all configuration choices are fixed.

## Decision rule

A direct pass requires every seed to reach actionable gain at least `.90` at
candidate fraction at most `.005`. The progress alternative requires every
seed to close at least half of the feasible R0-to-privileged gap without exact
nDCG regression against R0. No result licenses native or production activation.

## Results

Three-seed internal means for the configuration-selected policy of each target
are:

| Target | Candidate fraction | Actionable gain | Exact nDCG@10 |
|---|---:|---:|---:|
| Gain-density ListNet | .004999 | .8628 | .6397 |
| Cascade-useful probability | .004999 | .8516 | .6345 |
| Expected actionable gain | .004999 | .8561 | .6314 |
| Graded top100/top10/cascade | .004999 | .8597 | .6363 |
| Lambda candidate boundary | .004999 | .8448 | .6364 |

The old gain-density control remains best. It is only `.0033` above the frozen
R3c strict-prefix mean from the parent (`.8595`) and essentially tied on exact
nDCG (`.6397` versus `.6401`). Its per-seed gap closures are `.414`, `.502`,
and `.180`, so even the best target misses the every-seed progress gate badly.
The four new targets also fail both gates.

Configuration policy choices are unstable across seeds and targets. Most
select the calibrated useful-logit family, but lambda ranges from `0` to `3`;
the third-seed control instead chooses gain-minus-zero-cost. This is evidence
against a single missing scalar cost penalty. The probability target improves
some individual seed/query rankings but does not generalize as a global sparse
ordering. The explicit boundary loss is the weakest actionable treatment.

The narrow conclusion is:

> Separating relevance labels from posting cost is methodologically cleaner,
> but none of the tested relevance targets and configuration-only hard-budget
> policies converts frozen R3c into the privileged sparse scheduler.

This does not negate the representation gain established by R3c. It shows that
the remaining teacher gap is not closed by multi-label calibration, raw
expected gain, a graded top-100 target, or the tested boundary-focused pairwise
loss.

Result SHA-256 is
`e54c50d9d0817a351aa912cdec4c15c475d34fda76caebb76516d1cf4c6524a9`.
Independent replay regenerated all 15 archives with identical SHA-256 values
and reproduced the 7.3 MB result byte for byte. Evidence SHA-256 is
`343376f4dea675ba480156fd8140ceb2ef60cfacaccc305bea5e28735b31a3f7`.

## Limitations

- Calibration is rank-preserving affine/probability scaling; it is not a new
  high-capacity calibration network.
- The Lambda treatment uses hard negatives around the current predicted
  5k-candidate boundary, not an exhaustive differentiable sort.
- The external pseudo-query cascade projection is validated against native
  German inputs but remains derived rather than authoritative.
- Exact K8/top-1024 retrieval and R3 interaction construction remain offline
  research operations.
- Configuration-selected lambda variation indicates policy instability; no
  individual lambda is promoted as an architectural default.

## Next check

Run the independently approved replication-topology diagnostic without a
learned reranker first. Rebuild K8/prototype order for single assignment,
nearest-semantic secondary assignment, SOAR-style complementary assignment,
and a training-fitted global complementary assignment. Keep per-query
privileged replication strictly diagnostic and report unique candidate unions
plus physical storage replication.
