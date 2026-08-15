# Canonical arbitrary-m MIH frontier

## 2026-08-15 - exploratory protocol

**Question.** Across near-equal partitions of the fixed ITQ-256 code, which
canonical `m=15..21` schedules form the practical work/quality frontier at
Hamming radius 56?

**Status.** This is exploratory. The 1,252-query evaluation set has already
been observed for the `m=16` and `m=19` reference points, so this sweep must
not select a production configuration and describe it as a fresh confirmatory
held-out result.

**Architecture.** Each arm uses immutable per-band directories, bounded local
key enumeration, posting traversal, first-discovery generation-array
deduplication, full 256-bit Hamming ranking, ADC@256, and exact E5 reranking.
It changes the partition and exact local schedule only; the corpus, ITQ
rotations, query identity, Hamming shortlist, ADC configuration, and E5 oracle
remain fixed.

**Schedule rule.** For each near-equal width layout, enumerate local radii that
satisfy the exact pigeonhole condition:

`sum_b (r_b + 1) >= 57`.

Select the schedule with the fewest enumerated local keys
`sum_b sum_(d=0..r_b) C(width_b, d)`. Break ties by the
lexicographically maximum radius vector after bands are ordered by descending
width. This is a width-only, deterministic minimum-probe schedule; it is not
tuned on the evaluation queries. The measured contract's former
"reverse-lexicographic" label is retained as immutable historical provenance,
but was an inaccurate description of the already-executed comparator; this
correction changes neither schedule nor matrix. A later, separately
predeclared calibration-only study may compare it with a minimum-posting
schedule.

**Measurements.** For every fixed ITQ seed and every `m`, preserve per-query
bucket probes, posting visits, unique candidates, raw E5-oracle survival,
Hamming-shortlist survival, ADC@256 survival, and nDCG@10. Report seed rows,
paired bootstrap comparisons with `m=16`, and exact source and materialization
identities. Index bytes are deferred to the native sparse arbitrary-m
benchmark, whose immutable directory representation makes them meaningful. Do
not infer native latency from the Python reference work counters.

**Decision sequence.**

1. Complete this exploratory reference frontier.
2. Implement the best few layouts in a native sparse arbitrary-m index and
   separately time key enumeration, lookup, postings, deduplication, Hamming,
   top-K, ADC, and index bytes.
3. Choose one layout/schedule from calibration data under a frozen budget.
4. Confirm it once on a new untouched corpus or split, ideally at additional
   corpus scales such as 100k and 1M documents.
5. Only then evaluate a separate coarse locator code or renewed learned-code
   objective.

**Interpretation guardrails.** The sweep can establish local Pareto points and
invalidate simplistic rules based on candidate count or table count. It cannot
establish a universal `m`-to-corpus-size recommendation, production latency,
or cross-dataset generalization.

## Result

The complete five-seed matrix contains 35 validated report/contribution pairs.
The `m=15` layout required 18-bit substring keys, so the reference evaluator's
former artificial 16-bit variable-band limit was lifted with a regression test
before this final matrix was run. That change makes the intended arbitrary-m
domain representable; it is not a latency optimization.

| m | Local keys/query | Posting visits/query | Candidates/query | Raw survival | ADC survival | nDCG@10 |
|---:|---:|---:|---:|---:|---:|---:|
| 15 | 10,488 | 2,495.0 | 2,316.4 | 0.8636 | 0.8634 | 0.7731 |
| 16 | 7,232 | 3,356.3 | 3,061.0 | 0.8923 | 0.8917 | 0.7819 |
| 17 | 4,803 | 4,250.0 | 3,809.7 | 0.9127 | 0.9118 | 0.7861 |
| 18 | 3,060 | 4,996.0 | 4,413.9 | 0.9251 | 0.9238 | 0.7895 |
| 19 | 1,874 | 4,943.8 | 4,349.4 | 0.9262 | 0.9249 | 0.7889 |
| 20 | 1,554 | 6,476.8 | 5,520.3 | 0.9460 | 0.9444 | 0.7923 |
| 21 | 1,267 | 8,475.7 | 6,941.2 | 0.9633 | 0.9609 | 0.7966 |

All six challenger-vs-`m=16` comparisons have per-seed paired bootstraps with
10,000 replicates. The raw-union and ADC-survival intervals have one direction
for every seed: `m=15` is lower by about 0.0286/0.0284, while `m=17..21` are
higher by 0.0204..0.0711 / 0.0201..0.0692 respectively. The nDCG difference
is not uniformly separated from zero for `m=15..20`; `m=21` is positive for
all five seed-level intervals. This evidence supports a structured work/quality
frontier, not selection of a production point.

**Interpretation.** `m=19` is not a hidden optimum. It is one attractive
middle point: compared with `m=16`, it removes 5,358 key lookups while adding
about 1,588 posting visits and 1,288 candidates. `m=20` and `m=21` continue
the same exchange rather than revealing a discontinuity. Native sparse-index
timing and index-byte measurements are now required to compare these work
units on the target CPU.

### Post-hoc m=18 versus m=19 diagnostic

This five-seed, 10,000-replicate paired bootstrap is explicitly exploratory:
it clarifies a local feature seen after the frontier matrix and is not a
selection rule. The mean `m=19 - m=18` deltas are `-52.2` posting visits,
`-64.5` candidates, `+0.00102` raw survival, `+0.00110` ADC survival, and
`-0.00068` nDCG@10 per query. Every seed-level quality interval includes zero;
work direction also differs in one seed. Thus the apparent m=19/m=18 local
advantage is not established as dominance. The result reinforces the term
**structured work/quality frontier**, rather than a smooth monotone curve.

**Provenance.** The matrix is rooted at source commit
`65f1e54ef90c74b2923f4056bc2dd4c6d79d3e36`, with matrix source-bundle SHA-256
`ad2c7f469fa88f2389c320f9bd0c8963765493d603770552f5c827cfb9b05239` and
contract SHA-256
`14fc5023b7797334997b1b0f62a23fbcd603c744798009e55df4bd9238e55f85`.
The fail-closed evidence packager revalidates all 35 report/contribution rows,
all 30 predeclared `m`-versus-`m=16` paired bootstraps, and all five post-hoc
`m=19`-versus-`m=18` diagnostic replays before writing a deterministic ZIP.
Bootstrap v2 records the base RNG seed and the exact independently derived
seed for every metric; this corrects the earlier v1 report-level seed field,
which did not describe the metric-specific RNG streams. The matrix was not
replayed because this correction changes bootstrap metadata/provenance only.
The resulting archive is staged in the corresponding draft evidence release;
the raw matrix and bootstrap files remain outside Git.

## 2026-08-15 - post-merge evidence verifier hardening

**Context.** The published v2 evidence correctly bound the 35 historical
report/contribution pairs, source snapshots, materialization manifests, and
all bootstrap replays. A post-merge audit found that the row verifier
recomputed only a subset of the report's NPZ-derived aggregates.

**Change.** The verifier now recomputes every headline aggregate represented
by a contribution array: quality, work, E5-oracle survival, depth means, and
stop-reason fractions. It also checks the exact ordered query IDs and the
historical fixed-radius diagnostics: zero depth-accounting arrays and the
`fixed-radius` stop reason for every query. The latter is the actual semantic
contract of this uniform fixed-radius evaluator; it is not an omitted
per-depth probe count.

**Result.** A `--resume` verification accepted the unchanged 35 historical
report/NPZ pairs. No evaluator rows or bootstrap results were recomputed, and
the frontier interpretation is unchanged. Evidence v3 supersedes v2 as the
portable archive for this experiment because its verifier rejects a mutation
of any headline metric derived from a saved per-query contribution.
