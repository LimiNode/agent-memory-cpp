# Progressive exact Hamming top-K

## 2026-08-14 — strict lower-bound termination on the m=16 radius-56 union

**Context.** Research branch `agent/mih-progressive-exact-hamming-top-k`.
This is deliberately an execution diagnostic, separate from arbitrary-m
partition selection.

**Question.** Can the current `collect union -> deduplicate -> Hamming ->
top-768` stage stop early while preserving exactly the same stable Hamming
top-768 IDs as the complete radius-56 MIH union?

**Protocol.** For the predeclared five ITQ-256 seeds (52--56), stream the
existing 16 x 16 radius-56 pigeonhole schedule in increasing local Hamming
depth. A generation array accepts each document only at first discovery, and
full 256-bit Hamming is computed exactly once for it. The run may stop only
when the worst current top-768 distance is strictly below the minimum local
Hamming distance of every unvisited posting. Every final ID sequence is
compared against the old full-union stable Hamming order.

**Result.** The strict gate did not terminate early for any query in any seed.
All 1,252 queries in all five seeds consumed all 7,232 scheduled probes and
all corresponding posting visits before proof; the final IDs matched the
complete stable Hamming top-768 exactly in every case. Across seeds, the
reference union contained 3,061.0 unique candidates/query and 3,356.3 posting
visits/query, so exactly one full Hamming computation was performed per unique
candidate.

**Interpretation.** First-discovery generation-array dedup is a correct
full-union implementation baseline. The particular local-depth lower bound is
not a useful early-termination mechanism for this schedule: after every depth
group the worst top-768 distance remains above the minimum distance possible
for still-unseen postings. This rejects a claimed latency improvement without
weakening the exactness contract.

**Limitations.** The Python implementation validates algorithmic conformance,
not native hot-path latency. A negative result would not reject progressive
execution in general; it rejects this particular lower bound for the current
MIH schedule and K.

**Next checks.** If the strict gate reaches proof only after all probes, retain
the generation-array first-discovery implementation as a clean full-union
baseline and look for a stronger admissible global bound or a different index
structure before making production claims.
