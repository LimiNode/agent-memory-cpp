# FP32-free codec frontier v2 (2026-09-15)

## Scope

This is an exhaustive oracle on the corrected three-seed whole-posting R4
candidate stream (152 queries, 5,000–5,099 candidates/query). It reuses the
frozen DE-1M vectors, qrels and teacher IDs, but does not claim native latency,
MDBX page behavior, or production activation. Candidate-local FP32 top-10 is
the ranking reference; full-corpus teacher overlap and qrels nDCG@10 are
reported separately.

The THQ arm sweeps 3/4/5/8 ordinal levels (the result calls these
`ordinal_levels` to avoid conflating level count with bit count), ordinal-L1,
interval-L1 and interval-squared ADC, and top-64/128/256/512 shortlists. The
final scalar controls are FP16 and INT4/5/6/7/8/9/10/12, with linear and
power-.5 companders. Every scalar score is recomputed from the per-document
quantized code; no parity is asserted by copying a score array.

The fail-closed audit passed:

```text
semantic_fp32_free_codec_frontier_v2_audit_v1
stage rows: 7,296
final cascade rows: 124,032
direct scalar rows: 2,736
```

## Main result

The candidate stream itself retains `0.992763` mean teacher top-10 survival.
Direct scalar controls reproduce the historical quality ordering:

| final representation | logical bytes/doc | candidate-FP32 top-10 overlap | teacher top-10 recall | qrels nDCG@10 |
| --- | ---: | ---: | ---: | ---: |
| INT6 linear | 292 | .9632 | .9572 | .6624 |
| INT7 linear | 436 | .9829 | .9763 | .6567 |
| INT8 linear | 388 | .9941 | .9868 | .6562 |
| INT9 power-.5 | 532 | .9961 | .9888 | .6571 |
| INT10 power-.5 | 580 | .9987 | .9914 | .6569 |
| INT12 power-.5 | 676 | 1.0000 | .9928 | .6558 |

For the composed path, the cheapest strong point is an ordinal-levels-3
96-byte THQ shortlist followed by INT8 linear:

| cascade | total logical bytes/doc | candidate-FP32 top-10 overlap | teacher top-10 recall | qrels nDCG@10 |
| --- | ---: | ---: | ---: | ---: |
| levels-3 → top-256 → INT8 linear | 484 | .9941 | .9868 | .6562 |
| levels-3 → top-256 → INT9 power-.5 | 532 | .9961 | .9888 | .6571 |
| levels-3 → top-256 → INT10 power-.5 | 580 | .9987 | .9914 | .6569 |
| levels-3 → top-256 → INT12 power-.5 | 676 | 1.0000 | .9928 | .6558 |

The same scalar result appears for several THQ stage variants because the
shortlist already contains the candidate-local FP32 top-10. That is a useful
negative result: on this corrected R4 stream, interval-L1 and interval-squared
do not buy measurable final quality over the ordinal control at the tested
shortlist sizes. They may still affect work/latency, which this oracle does
not measure.

## Interpretation and next gate

The matrix supports a narrow architecture hypothesis, not a production
decision. A 484-byte `THQ → INT8` logical cascade is a credible finalist and
matches the historical #291/#292 direction, while INT7 is below the `.99`
candidate-local gate. Before selecting it, the next PR must materialize the
actual packed THQ and INT8 records, decode them in the native scorer, and
measure candidate bytes/pages and cold/warm latency. The optional
`.625/.75/.875` and mu-law companders for widths 5–9 remain a detached
follow-up; they cannot overturn the current INT8/INT9/INT10 shortlist without
an independently audited replay.

Raw receipt: `frontier-v2.receipt.json`; raw SHA is bound by that receipt.
