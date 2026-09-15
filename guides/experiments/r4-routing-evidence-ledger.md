# R4 routing evidence ledger

This ledger is the canonical archive for the 2026-08--2026-09 R4 routing
experiments.  The old PRs were developed as a stacked research tree; they are
not merged here as implementation history.  Their corrected receipts and raw
artifacts remain the authoritative evidence, while this file records the
causal conclusions that should not be rediscovered by another agent.

## How to read this ledger

Each entry states the question, the result, and the architectural consequence.
The cited PR is the historical source.  A result is not a production claim
unless its receipt/audit is explicitly marked as executed and PASS.  The
152-query workload is useful for controlled comparisons, but held-out/native
confirmation is still required before a production decision.

## Causal results

### Topology membership and seed diversity — #385, #386, #389, #410

*Question.* Is the R4 topology itself unable to contain the teacher documents,
or is the loss caused by route ordering and budget allocation?

*Result.* The corrected membership ceiling for the shallow/deep route union is
about `.9961`; the three-seed K1→K16 frontier reaches approximately `.993` at
`A=8192` and `.9974` at `A=16384` under the 5k whole-posting budget.  The
best two-seed arm is about `.9901` at `A=16384`.  These are quality frontiers,
not latency Pareto points.

*Interpretation.* The topology is not the limiting factor at the target
quality.  Seed diversity is the dominant low-budget lever.  The canonical
replacement for #410 is the clean `r4-k1-seed-pareto` experiment; the old
stacked branch is superseded by that replacement.

### Scheduler and prefix-order limitation — #387, #388

*Question.* Does a better allocation of the existing route prefixes recover
the missing teachers?

*Result.* Prefix-order and fusion controls show that naive equal-consumed-entry
fusion leaves quality below the membership ceiling.  Teacher-leaking upper
bounds can approach the ceiling, but they are diagnostic only and cannot be
used as a serving policy.

*Interpretation.* The remaining gap is primarily route ranking/scheduling, not
raw posting membership.  Do not present the oracle rows as deployable recall.

### Corrected held-out/ranker controls — #391

The held-out and null controls confirm that the observed R4 gain is not an
artifact of a single reused teacher order.  The result is archived as a
corrective control, not a production router.

### Raw and representative geometry — #392, #393, #394

*Question.* Can a compact raw-coordinate or representative-distance surrogate
replace the learned/full route?

*Result.* Raw-coordinate and representative/SPANN-like ordinal geometry do not
provide a deployable ranking.  Representative-max is a useful quality ceiling
(`.999342`) only by evaluating roughly 2.67M representative vectors per query.

*Interpretation.* This is an upper bound and a diagnostic of geometry, not an
acceptable candidate generator.

### K8/K16/K32 and native arithmetic — #395, #397

The K sweep and native arithmetic controls identify K16 as the practical
quality/footprint point.  K8 is a useful lower-cost control; K32 adds work
without a proportional routing benefit.  Native measurements, rather than
Python estimates, are required for the final gate.

### R4 cascade and page proxy — #396, #398, #408

The early K1→THQ cascade and logical page proxy are historical implementation
experiments.  They established the candidate/page accounting vocabulary but
did not constitute a native full-corpus production replay.  Do not merge the
stacked implementation branches as the canonical architecture.

### Exact representative oracle — #399

The exact representative top-R oracle provides a topology upper bound and a
diagnostic for how much quality is available if address selection were perfect.
It does not measure a realizable router and must not be used as serving latency.

### HNSW control — #400

The HNSW control is retained as a negative architecture reference (about
`836 ms/query` and roughly `7.2 GB` for the tested graph).  It is useful to
prevent repeating the mutable graph-store direction, not as a product choice.

### Intermediate native transitions — #401--#404

These PRs document the transition from K1 through K16 and the native coarse
stage.  Their final numbers are retained for lineage; the intermediate
runners are superseded by the corrected native gates.

### Native layout and page amplification — #405, #406, #407

AoSoA32 native scoring is materially faster than the scalar control (about
`7.88/8.38 ms` versus `28.73/32.77 ms` in the measured controls).  Sparse
gather/page amplification remains a storage risk.  The corrected full cascade
reaches approximately `.9974 @ A=16384`; this is the strongest current R4
quality evidence, but it is still a candidate-quality result rather than a
1M native serving benchmark.

## Disposition

The historical PRs above are superseded after this ledger and the clean #410
replacement merge.  Their branches may be archived, but raw receipts and
large source payloads must remain available at the artifact locations recorded
by their original notes.  #407's corrected result is specifically retained as
the frozen input for the #410 replacement and the next native wave.

## Next-wave gate

Only after this ledger and the clean #410 replacement are merged should the
project start the native/full-corpus wave: 1M direct-ID tables, the four native
INT8/THQ arms, shared document-major K16, full-corpus THQ Flow, asymmetric R4
budgets, persistent posting layouts, held-out confirmation, and optional
independently decodable compression controls.  The protocol is recorded in
`2026-09-15-next-native-research-plan.md`.
