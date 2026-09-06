# Multi-anchor THQ transition-cost oracle

Date: 2026-09-07. This is the final pre-index check for the current scalar
ordinal transition-cost idea. It tests whether taking the minimum cost over
multiple semantic anchors can recover THQ candidates that one anchor misses.

## Protocol

The frozen 454,322-prototype source and 152 DE queries are reused. For each
query, the exact teacher top four K8 prototypes are used as offline anchors.
For every anchor, the top 50,000 continuous directional projection candidates
are collected; the union is then ranked by the minimum signed ordinal
transition cost over the four anchors. The exhaustive THQ4 prototype top-256
is the reference. This is still an oracle and does not generate physical MIH
probes. Runner: `tools/agent-memory-bench/evaluate-thq-multianchor-transition-
cost-oracle.py`.

## Result

Aggregated over all 152 queries:

| ranked budget | THQ top-256 recall | teacher top-10 prototype survival |
|---:|---:|---:|
| 256 | .0679 | .3539 |
| 512 | .0800 | .3553 |
| 1,024 | .0911 | .3572 |
| 2,048 | .1009 | .3592 |
| 5,000 | .1105 | .3618 |
| 10,000 | .1156 | .3625 |

## Interpretation

Four anchors improve the one-anchor result, but not remotely enough for a
candidate generator: only 11.6% of THQ top-256 and 36.3% of teacher top-10
survive at a 10k budget. The current scalar transition cost therefore fails
for teacher, prototype, and multi-anchor variants. Building a best-first
transition index or directional MIH on this formulation is not justified.

This closes the present directional-MIH research line as a product candidate,
not the broader possibility of a learned joint-transition model. Reopening it
requires a materially different cost model and a new untouched evaluation
protocol with the same >= .995 quality gate used by other THQ generators.

## Limitations

Teacher anchors are unavailable at serving time, and the 50k projection
prefilters are dense scans. The experiment measures routing-ceiling recall only;
it supplies no serving latency, bytes-touched, random-read, or qrels result.
