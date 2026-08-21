# Static ITQ locator budget-frontier protocol

Date: 2026-08-22. Context: planned draft follow-up stacked on draft PR #159.
This is a predeclared calibration-only protocol. It contains no new native
measurement and no selection, confirmation, or learned-locator result.

## Question

Does the apparent negative result for the very selective 96-bit random
locator at local radius three persist after spending the predeclared candidate
and latency budget on larger static codes and progressively deeper probing?

## Scope

The frozen Spanish 25k input and E5 evaluation materialization remain fixed.
The ranking representation stays frozen ITQ-256; only a random, deterministic
subset of its bits is used for routing. The widths are 64, 80, 96, 112, and
128 bits, divided into 16-bit bands. There is deliberately one subset policy:
the previous static screen found no evidence that document-bit decorrelation
improves on the random baseline.

For each width, the first row probes every band at radius three. Subsequent
rows change a prefix of the sorted locator bands to radius four:

```text
r3 r3 r3 ... r3
r4 r3 r3 ... r3
r4 r4 r3 ... r3
...
```

The schedule is nested, so each later row contains the candidate region of its
predecessor. Under independent uniform 16-bit keys, radius three covers
`697 / 65536` keys per band and radius four covers `2517 / 65536`. The planner
records the corresponding union estimate for every proposed row. It excludes a
row when that estimate already exceeds the 25% candidate budget. Consequently,
the 128-bit schedule ends at seven radius-four bands: all eight would have an
estimated 26.9% background candidate fraction.

## Fresh comparator and stopping

Before any locator row, measure a fresh full-code ITQ-256 `m19` uniform-radius
two candidate-generator baseline with the same frozen input, query sample,
warmup, repeat count, machine, and executable. Do not reuse its historical
latency as the comparator.

For each width, stop the nested sequence after the first observed row that
either exceeds 25% unique candidates or has candidate-generator p50 greater
than the fresh full-code baseline. The triggering row is retained and marked
as budget-exhausted; it is not an approximate success. Every non-exhausted
row receives the existing full ITQ-256 Hamming, ADC, and E5 cascade evaluation.

The protocol publishes candidate fraction, p50/p95/p99 generator latency,
probes, posting visits, Hamming top-768 recall against Flat ITQ-256,
E5-oracle survival after ADC, and reranked nDCG@10. Timing components remain
diagnostic; candidate-generator total is the budgeted latency.

## Interpretation boundary

This protocol may establish the static budget frontier only. It cannot select
a production locator, touch the French confirmation split, or authorize a
learned locator. If its best static point remains inadequate and true exact
MIH is also uncompetitive, the next separately predeclared work is a
task-aware non-neural selector: train bit/band selection on one calibration
partition, select on another, and retain a fresh untouched confirmation for a
later frozen choice. A learned routing code, if justified, starts only after
that comparison and uses a new training and evidence contract.

## Prepared artifacts

`static-itq-locator-budget-frontier.example.json` pins the scope, baseline,
and stop rules. `plan-static-itq-locator-budget-frontier.py` validates and
prints the 34 planned nested rows without invoking the native executable.
