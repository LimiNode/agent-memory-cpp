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
`sum_b sum_(d=0..r_b) C(width_b, d)`. Break ties by the reverse-lexicographic
radius vector after bands are ordered by descending width. This is a
width-only, deterministic minimum-probe schedule; it is not tuned on the
evaluation queries. A later, separately predeclared calibration-only study may
compare it with a minimum-posting schedule.

**Measurements.** For every fixed ITQ seed and every `m`, preserve per-query
bucket probes, posting visits, unique candidates, raw E5-oracle survival,
Hamming-shortlist survival, ADC@256 survival, and nDCG@10. Report seed rows,
paired bootstrap comparisons with `m=16`, index bytes, and exact source and
materialization identities. Do not infer native latency from the Python
reference work counters.

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
