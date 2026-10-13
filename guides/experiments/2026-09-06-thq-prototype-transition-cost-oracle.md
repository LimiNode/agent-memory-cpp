# Prototype-direction THQ transition-cost oracle

Date: 2026-09-06. This is the second directional-MIH ceiling check. It asks
whether an available K8 prototype can replace the unavailable teacher document
as the direction anchor for ordinal THQ transition costs.

## Protocol

The frozen real prototype source contains 454,322 K8 prototypes and the same
152 DE queries. For each query, the exact teacher top-1 K8 prototype is used
only as an offline anchor: `direction = prototype - query`. THQ4 codes for all
prototypes are materialized once from the frozen quantile thresholds. An
exhaustive prototype THQ top-256 is the reference; a 50,000-prototype
continuous directional prefilter is ranked by signed ordinal transition cost.
No physical index or probe enumeration is involved. Runners are
`tools/agent-memory-bench/materialize-thq-prototype-codes.py` and
`tools/agent-memory-bench/evaluate-thq-prototype-transition-cost-oracle.py`.

## Result

Aggregated over 152 queries:

| ranked budget | THQ top-256 recall | teacher top-10 prototype survival |
|---:|---:|---:|
| 256 | .0213 | .1026 |
| 512 | .0247 | .1026 |
| 1,024 | .0280 | .1053 |
| 2,048 | .0320 | .1086 |
| 5,000 | .0363 | .1092 |
| 10,000 | .0379 | .1099 |

The packed prototype code cache and chunked raw reports remain under `tmp/`.

## Interpretation

Prototype direction performs slightly better than the teacher-direction scalar
cost on THQ-top256 recall at large budgets, but it remains decisively unusable:
only 3.8% of the THQ top-256 is retained at a 10k budget. The teacher top-10
survival is about 11%. This closes the one-anchor scalar transition-cost
formulation for both an oracle teacher direction and a usable prototype
direction. Directional geometry therefore cannot be promoted to a physical
MIH/index from this score alone.

The result does not close multi-anchor or joint transition-path models. Those
must be measured as separate oracle hypotheses with a predeclared quality and
work gate before any best-first probe implementation.

## Limitations

The prototype anchor is selected from exact teacher targets and is not
available at query time. The 50k continuous prefilter is itself a dense scan,
and the scalar cost ignores correlations between simultaneous ordinal
transitions. These numbers are a routing ceiling diagnostic, not end-to-end
retrieval latency or qrels quality.
