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
power-.5/.625/.75/.875 companders. Every scalar score is recomputed from the per-document
quantized code; no parity is asserted by copying a score array.

The corrected replay regenerated the receipt and raw SHA with per-coordinate
squared ADC. The fail-closed audit passed:

```text
semantic_fp32_free_codec_frontier_v2_audit_v1
stage rows: 7,296
final cascade rows: 299,136
direct scalar rows: 6,384
```

## Main result

The candidate stream itself retains `0.992763` mean teacher top-10 survival.
Direct scalar controls reproduce the historical quality ordering:

| final representation | logical bytes/doc | candidate-FP32 top-10 overlap | teacher top-10 recall | qrels nDCG@10 |
| --- | ---: | ---: | ---: | ---: |
| INT6 power-.5 | 292 | .9566 | .9513 | .6549 |
| INT7 linear | 340 | .9829 | .9763 | .6567 |
| INT8 linear | 388 | .9941 | .9868 | .6562 |
| INT8 power-.625 | 388 | .9888 | .9829 | .6585 |
| INT9 power-.5 | 532 | .9961 | .9888 | .6571 |
| INT9 power-.625 | 532 | .9954 | .9888 | .6592 |
| INT10 power-.5 | 580 | .9987 | .9914 | .6569 |
| INT12 power-.5 | 676 | 1.0000 | .9928 | .6558 |

Packed-ordinal accounting changes the stage frontier: levels-3 and levels-4
both cost 96 bytes/document. With proper interval-squared ADC, levels-4 reaches
candidate-FP32 overlap `1.0` at top-128, while levels-3 needs top-256. The
quality-equivalent composed points are therefore:

| cascade | total logical bytes/doc | candidate-FP32 top-10 overlap | teacher top-10 recall | qrels nDCG@10 |
| --- | ---: | ---: | ---: | ---: |
| levels-3 → top-256 → INT8 linear | 484 | .9941 | .9868 | .6562 |
| levels-4 → top-128 → INT8 linear | 484 | .9941 | .9868 | .6562 |
| levels-3 → top-256 → INT9 power-.5 | 532 | .9961 | .9888 | .6571 |
| levels-3 → top-256 → INT10 power-.5 | 580 | .9987 | .9914 | .6569 |
| levels-3 → top-256 → INT12 power-.5 | 676 | 1.0000 | .9928 | .6558 |

The same scalar result appears for several THQ stage variants because the
shortlist already contains the candidate-local FP32 top-10. With corrected
per-coordinate interval-squared ADC, stage-only quality at levels=3 and
shortlist=256 is ordinal-L1 `.59722`, interval-L1 `.61333`, and interval²
`.61550` nDCG; all three have identical candidate-local FP32 final-rerank
quality. They may still affect work/latency, which this oracle does not
measure.

## Paired qrels diagnostics

Mean qrels alone is not a safe scalar-codec gate. Relative to candidate-local
FP32, INT6 linear has mean delta `+.00818` but a worst loss of `.36907` and a
paired-bootstrap 95% CI of `[-.00094, +.01784]`. INT8 linear has mean delta
`+.00198`, the same single-query worst loss, and CI `[-.00548, +.00971]`.
INT8 power-.625 has mean delta `+.00432`, worst loss `.01096`, and CI
`[+.00022, +.01073]`, but its candidate-FP32 overlap is only `.9888`.

Thus qrels nDCG is the product metric, while FP32 overlap remains an explicit
safety guardrail rather than the optimization objective. The final guardrail
must be chosen before selecting between INT8 linear and power-.625.

## Interpretation and next gate

The matrix supports a narrow architecture hypothesis, not a production
decision. The native gate has two storage alternatives: direct shared INT8 at
388 bytes/document, scoring roughly 5k candidates, versus shared levels-4 THQ
plus INT8 at 484 bytes/document, scoring roughly 5k THQ records and 128 INT8
records. Both have the same frozen quality here; THQ is justified only by a
matched native bandwidth/latency win. INT8 linear and power-.625 must both be
retained because they expose a real fidelity-versus-qrels-tail tradeoff.

Raw receipt: `frontier-v2.receipt.json`; raw SHA is bound by that receipt.
Packed ordinal accounting is `ceil(dimension * ceil(log2(levels)) / 8)`;
thermometer storage is a separate diagnostic representation.
