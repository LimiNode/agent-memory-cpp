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

## Results

Measured on 2026-08-23 in the predeclared Spanish 25k calibration scope. The
fresh full-code ITQ-256 `m19` candidate-generator comparator was `0.3491 ms`
at p50. The generator-latency stop, rather than the 25% candidate cap, ended
every width's nested sequence. Thus the rows labelled exhausted below are
boundary observations, not selected approximate configurations.

| routing bits | r4 prefix bands | candidates | generator p50 / p95 (ms) | Flat Hamming@768 recall | E5 survival after ADC | reranked nDCG@10 | status |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 64 | 0 | 4.784% | 0.2258 / 0.2782 | 20.21% | 58.18% | 0.6087 | within budget |
| 64 | 1 | 7.761% | 0.3214 / 0.3792 | 28.36% | 67.11% | 0.6610 | within budget |
| 64 | 2 | 10.558% | 0.4145 / 0.4862 | 35.64% | 74.34% | 0.7029 | exhausted: p50 |
| 80 | 0 | 6.092% | 0.2576 / 0.3018 | 25.35% | 64.86% | 0.6467 | within budget |
| 80 | 1 | 8.911% | 0.3593 / 0.4210 | 32.67% | 72.45% | 0.6902 | exhausted: p50 |
| 96 | 0 | 6.996% | 0.2950 / 0.3488 | 28.36% | 70.00% | 0.6836 | within budget |
| 96 | 1 | 9.775% | 0.3922 / 0.4647 | 35.39% | 76.39% | 0.7174 | exhausted: p50 |
| 112 | 0 | 8.115% | 0.3516 / 0.4171 | 32.62% | 76.10% | 0.7228 | exhausted: p50 |
| 128 | 0 | 9.178% | 0.3981 / 0.4733 | 36.64% | 80.31% | 0.7373 | exhausted: p50 |

The first radius-four expansion is productive in retrieval terms. The 64-bit
locator can afford one radius-four band within the fresh `m19` budget, but its
67.11% E5 survival remains below the 96-bit r3 point. For 80 and 96 bits, the
first radius-four expansion exceeds the latency budget; 112 and 128 bits
already exceed it at r3. Larger static codes enrich routing substantially
above a random candidate sample, yet the tested random-subset family does not
meet the intended strict quality frontier inside that budget. This is
calibration evidence only: it neither selects a production locator nor
evaluates French confirmation data.

The fail-closed evidence archive replayed the native source/config bindings,
Flat recall, E5 survival, and reranked nDCG from per-query contributions. Two
independently written deterministic archives had SHA-256
`87cd9b647b93e3849426dafc67616c42c42ef96e4e2a7ec16f32e7047727cd5c`.

## Follow-up

The next locator study remains a task-aware non-neural selector trained and
selected on disjoint calibration partitions. It must compare against this
random frontier without reusing these evaluation queries for selection. A
learned locator is a later, separately pinned protocol, not a conclusion of
this calibration result.
