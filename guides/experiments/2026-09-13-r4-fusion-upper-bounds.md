# R4 fusion upper bounds and tail scheduling (2026-09-13)

## Question

Is the residual gap in the R4 route-fusion gate caused by missing topology, by
the within-route prefix order, or only by an inefficient non-leaking scheduler?

## Protocol

The replay uses the frozen DE-1M fixture and three materialized R4 seeds.  It
reports two teacher-leaking upper bounds (prefix-allocation DP and arbitrary
posting selection) and a non-leaking deep-prefix/tail scheduler.  The latter
uses seed 2702, seed 2703, and the deep-8192 route: it consumes at most a
configured number of posting entries from the deep route's first 1,024
model-ranked addresses, skips the rest of that prefix, and resumes at the
regenerated tail.  Teacher IDs are used only for evaluation/oracle metrics.
Prefix and runtime scheduler gates use one global unique-candidate budget.
The arbitrary-posting row is a teacher-leaking route-visible support oracle;
its posting-entry cost is reported separately.  All work and duplication are
logical measures, not MDBX or OS page traffic.

## Results

The corrected replay produced the following mean teacher recall (minimum over
queries is shown in parentheses):

| Scheduler | 20k | 50k | 100k |
| --- | ---: | ---: | ---: |
| Prefix-allocation oracle (unique-candidate budget) | .9796 (.70) | .9868 (.70) | .9921 (.70) |
| Route-visible arbitrary-posting oracle | .9961 (.70) | .9961 (.70) | .9961 (.70) |
| Jump cap 0 | .9658 (.50) | .9704 (.70) | .9770 (.70) |
| Jump cap 2,500 | .9796 (.70) | .9836 (.70) | .9908 (.70) |
| Jump cap 5,000 | .9796 (.70) | .9842 (.70) | .9914 (.70) |
| Jump cap 10,000 | .9796 (.70) | .9829 (.70) | .9921 (.70) |

The prefix oracle is bounded below `.99 @ 50k`, while route-visible arbitrary
posting selection reaches the topology membership ceiling `.9961` at about 115
posting entries on average.  The
non-leaking jump scheduling modestly improves the previous `.9796 @ 50k`
frontier to `.9842`; at 100k it approaches `.9921`, with duplication ratios roughly
1.08--1.20 depending on cap.

Residual diagnostics remain 31 query/teacher pairs missed by all shallow routes;
their deep address ranks and cumulative posting costs are retained in the
compact receipt.

## Interpretation and decision gate

R4 topology contains nearly all teacher documents within the route-visible
address sets (arbitrary-posting oracle), but the available within-route
ordering is the limiting factor: even a privileged prefix allocation remains
below the `.99 @ 50k` target.  The simple tail jump does not solve this, so the
result is not evidence for production activation.
The next meaningful comparison is a properly trained/deep ranker or
multi-anchor route; a THQ-ADC cascade should remain gated until one of those
routes clears the target under a non-leaking budget.

## Limitations

These are frozen-fixture logical posting-work experiments.  They do not measure
physical pages, MDBX latency, payload reranking, or cold/warm I/O, and the two
oracle rows are explicitly teacher-leaking upper bounds.  The deep tail is a
regenerated coarse order rather than a learned model-ranked tail.

See `2026-09-13-r4-fusion-upper-bounds-result.json` and the external raw bundle
`r4-fusion-upper-bounds-raw.json`.
