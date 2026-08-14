# Progressive exact Hamming top-K

## Historical v1 — local-depth bound diagnostic

The original v1 compared a full-code Hamming threshold with the next local
substring depth. That is a valid but deliberately weak bound; its zero
early-stop outcome must not be interpreted as a test of canonical progressive
MIH stopping.

## 2026-08-14 — global pigeonhole-bound K sweep (v2)

**Question.** Does the canonical MIH lower bound permit exact early stopping
on the m=16 canonical r56 schedule, and how does it scale with top-K?

**Protocol.** Fixed ITQ-256/50 seeds 52--56, 25k training vectors, untouched
22,607 documents / 1,252 queries. The schedule is nine 16-bit radius-3 and
seven 16-bit radius-2 bands. Postings are processed by completed `(band,depth)`
groups. If `t_b` is the largest fully processed local radius in a band, every
undiscovered document has full Hamming distance at least:

`L_unseen = sum_b (t_b + 1)`.

A result is accepted only when the current Kth full-code Hamming distance is
strictly smaller than `L_unseen`; strictness preserves stable document-ID tie
ordering. First-discovery generation-array dedup computes each discovered
document's full Hamming distance once. The predeclared K values are 10, 64,
128, 256, 512 and 768.

**Result.** All 6,260 query/seed rows exactly reproduce the full-union stable
Hamming top-K at every K. The global proof nonetheless reaches no early stop
for any K in this dataset: every row reaches all 7,232 probes, the complete
3,356.3 posting visits/query and 3,061.0 Hamming computations/query before
the final lower bound of 57.

| K | Probes at proof | Posting visits at proof | Candidates/Hamming at proof | Early-proof fraction |
|---:|---:|---:|---:|---:|
| 10 | 7,232 | 3,356.3 | 3,061.0 | 0.0 |
| 64 | 7,232 | 3,356.3 | 3,061.0 | 0.0 |
| 128 | 7,232 | 3,356.3 | 3,061.0 | 0.0 |
| 256 | 7,232 | 3,356.3 | 3,061.0 | 0.0 |
| 512 | 7,232 | 3,356.3 | 3,061.0 | 0.0 |
| 768 | 7,232 | 3,356.3 | 3,061.0 | 0.0 |

**Interpretation.** This is now a real negative result for this exact global
proof, dataset and schedule, rather than a rejection based on the former local
bound. Generation-array first discovery remains a correct full-union baseline;
the global stopping proof itself does not reduce work here, even for top-10.

**Terminology.** The 6,260 observations are `6,260/6,260 exact
full-r56-union Hamming top-K matches`, not corpus-wide exact-kNN matches. The
fallback ranks the complete candidate union produced through radius 56. An
exact-kNN MIH algorithm could instead continue expanding the global Hamming
radius after 56; that different policy is outside this experiment. What this
result closes is the early global-proof branch for the present ITQ-256 corpus
and `m=16/r56` schedule.

### Evidence replay hardening (v3)

The original v2 draft evidence records the result but is historical only. The
v3 replay retains, for every seed and query, both the progressive and canonical
full-union top-768 document-position sequences, their cutoff Hamming distances,
and all work/proof arrays. Document positions are bound to the evaluation
materialization manifest. The evidence packager independently checks sequence
equality for every predeclared K prefix and recomputes every report aggregate
from the NPZ contribution; it does not trust the report's summary or a runner
boolean. Resume now performs the same fail-closed validation before accepting
an existing row.

The historical v3 archive is superseded by published
[evidence v4](https://github.com/LimiNode/agent-memory-cpp/releases/tag/evidence/mih-progressive-exact-hamming-top-k-v4).

The v4 verifier additionally binds the supplied contract bytes to the measured
source snapshot and requires both materialization manifest hashes to equal the
frozen contract. This is necessary because the preserved top-K values are
document positions whose meaning is defined by the exact evaluation root.

**Limitations.** This is a Python conformance experiment, not a native latency
benchmark. It does not rule out other admissible bounds, alternate partitions,
or a different corpus/scale regime.

**Next checks.** Combine the matched m frontier with native lookup/posting
costs before choosing a storage layout. Treat other global bounds or index
structures as separate predeclared work.
