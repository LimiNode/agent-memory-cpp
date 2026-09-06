# Teacher-direction THQ transition-cost oracle

Date: 2026-09-06. This experiment follows the directional-MIH review and
tests the strongest offline ceiling before implementing an index: can a scalar
ordinal transition cost, given the true teacher direction, recover the frozen
THQ4 top-256?

## Protocol

The run uses the DE-1M frozen THQ4 manifest and 152 queries. For each query,
the first exact-teacher top-10 document is used as the semantic anchor and
`direction = teacher_top1 - query`. Documents are first restricted to the top
50,000 of the continuous projection on that direction; this is an explicit
diagnostic prefilter, not a serving step. Within that prefilter, each THQ4
coordinate contributes a signed ordinal transition cost. Aligned transitions
are cheaper than transitions against the teacher direction, and the resulting
scalar cost orders documents. The reference set is the exhaustive THQ4
top-256. Runner: `tools/agent-memory-bench/evaluate-thq-transition-cost-oracle.py`.

## Result

Aggregated over all 152 queries:

| ranked budget | THQ top-256 recall | teacher top-10 survival |
|---:|---:|---:|
| 256 | .0133 | .1342 |
| 512 | .0152 | .1362 |
| 1,024 | .0172 | .1382 |
| 2,048 | .0197 | .1408 |
| 5,000 | .0227 | .1414 |
| 10,000 | .0250 | .1434 |

The raw chunk reports remain under `tmp/` and are not part of the source tree.

## Interpretation

This scalar cost function fails decisively as a retrieval ordering, even with
the teacher's true top-1 direction and a 50k continuous prefilter. It retains
only 2.5% of the THQ top-256 at a 10k ranked budget. Therefore a directional
transition index cannot be justified by coordinate-level direction capture
alone, and the current cost formula is not a useful candidate generator.

This does **not** prove that all directional or ordinal indexing is impossible.
The earlier geometry result measured transition mass, not retrieval survival;
this run closes one teacher-direction scalar-cost formulation only. Any next
variant must beat this result with a predeclared quality/work gate and must be
tested first as an oracle, before implementing physical probes. The next
diagnostics are prototype direction and multi-anchor transition paths. Only a
strong oracle result can justify a best-first transition index or directional
MIH implementation.

## Limitations

The teacher anchor is unavailable at serving time, the continuous 50k
prefilter is itself a dense scan, and the cost has no learned joint-transition
model. No latency, random-read, bytes-touched or final qrels claim follows
from this experiment.
