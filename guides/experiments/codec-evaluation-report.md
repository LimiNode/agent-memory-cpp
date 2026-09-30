# Codec evaluation report

This document is the consolidated navigation report for codec research. It
separates algorithmic quality, native compressed retrieval, full-corpus flat
serving, and persistent MDBX layout. A check mark means that the cited receipt
contains that measurement; `pending` means that no result is inferred from a
different representation.

## Status matrix

| Arm | Algorithmic quality | Native compressed cascade | Full 1M flat | MDBX persistence |
| --- | --- | --- | --- | --- |
| LSQ32 | measured | measured | pending | pending |
| LSQ48 | measured | measured | pending | pending |
| PLSQ8x6x8 | measured | pending | pending | pending |
| TQ1 | measured | pending | pending | pending |
| TQ1+PQ8 | measured/partial serving | pending | pending | pending |
| RSLM1 | measured | pending | pending | pending |
| INT8 reference | control | control | measured | measured |

The quality rows are the historical qrels study in
[`2026-09-22-faithful-binary-followups.md`](2026-09-22-faithful-binary-followups.md).
They are not interchangeable with latency rows: quality uses qrels and
candidate-overlap diagnostics, while serving rows require the same native
packed scorer and candidate topology.

## Algorithmic quality

| Arm | Side bytes | mean nDCG@10 | FP32 candidate overlap | Status |
| --- | ---: | ---: | ---: | --- |
| LSQ32 strong | 32 | 0.657264 | 0.890132 | measured |
| LSQ48 strong | 48 | 0.661515 | source note | measured |
| TQ1 | 52 | 0.659176 | source note | measured |
| RSLM1 cosine | 52 | 0.658220 | source note | measured |
| PLSQ8x6x8 | 52 | 0.656438 | source note | measured; native gate pending |
| PLSQ8x4x8 | 36 | 0.649133 | source note | measured; native gate pending |
| TQ+ direct | 56 | 0.657829 | source note | measured; native gate pending |

These values are historical research evidence, not acceptance thresholds or a
codec winner. See the source note for p05/worst-query and teacher-overlap
columns.

## Algorithmic research history (algorithm zoo)

The table below is an inventory of the investigated families, including arms
that were rejected or deferred. A dash means that the cited experiment did not
publish a comparable value; it is not a zero. Training cost and model bytes
are likewise left unreported when the source receipt did not bind them.

| Family / variant | Code bytes | Quality / overlap evidence | Training cost | Model / decode bytes | Status | Decision and evidence |
| --- | ---: | --- | --- | ---: | --- | --- |
| LSQ32 / LSQ48 | 32 / 48 | nDCG .657264 / .661515 | measured; budget-sensitive | — | FINALIST | native packed cascade exists; [LSQ note](2026-09-23-native-compressed-lsq-result.md) |
| PLSQ8x4x8 / PLSQ8x6x8 | 36 / 52 | nDCG .649133 / .656438 | measured | — | DEFERRED | native scorer still required; [quality follow-up](2026-09-22-faithful-binary-followups.md) |
| TQ1 / TQ2 | 52 / — | nDCG .659176 / — | measured / — | — | DEFERRED | packed scorer and parity gate pending; [TQ wave](2026-09-24-thq-tq1-residual-correction-next-wave.md) |
| TQ+ | 56 | nDCG .657829 | measured | — | DEFERRED | quality control only until native TQ+ scorer exists; [follow-up](2026-09-22-faithful-binary-followups.md) |
| RSLM1 / RSLM2 / RSLM3 / RSLM4 | 52 / 96 / 144 / 192 | RSLM1 nDCG .658220; later rows use separate persistable-gate metrics | measured / — | — | DEFERRED | faithful RSLM1 native scorer and matched replay pending; [RSLM gate](2026-09-19-thq-rslm-persistable-gate.md) |
| RQ32 / RQ48 | 32 / 48 | nDCG range .651069–.659922 across seeds | measured | shared codebooks; sizes bound in source | CONTROL | additive-quantizer control, not a native winner; [RQ replay](2026-09-20-thq-faiss-additive-acceleration.md) |
| RaBitQ references | 16–52 | candidate-overlap only in the cited local replay | measured | per-variant metadata | CONTROL | useful filter reference; not an official binary-compatible implementation; [family matrix](2026-09-04-binary-code-family-matrix.md) |
| BBQ / BBQ-like references | 32–80 | candidate-overlap only in the cited local replay | measured | per-variant metadata | CONTROL | explicitly BBQ-like, not an official replay; [family matrix](2026-09-04-binary-code-family-matrix.md) |
| OPQ/PQ4/PQ8 | 16–32 | nDCG and coverage reported for the full grid | measured | codebooks/rotation measured in source | CONTROL | second-stage quality baseline; no codec selection claim; [OPQ study](2026-07-31-multilingual-autoencoder-study.md) |
| QINCo2 | — | convergence/occupancy replay recorded separately | bounded replay | — | CONTROL | paper-faithful reference, not yet a native serving arm; [corrective replay](2026-09-26-qinco2-corrective-replay.md) |
| Joint-conditioned residuals | 32–128 | bounded shell metrics; no matched native latency | measured | — | NEGATIVE | did not displace direct INT8/RSLM controls on the tested shell; [joint gate](2026-09-19-thq-joint-conditional-gate.md) |
| Learned ADC variants | 32–240 | bounded score/overlap diagnostics | measured / budget-limited | — | DEFERRED | requires a genuine packed learned-ADC scorer, not another FP32 decoder; [ADC gate](2026-09-18-thq-learned-adc-gate.md) |
| Score-weighted PQ-like ADC | 32–240 | bounded reconstruction/score metrics | measured | — | NEGATIVE | diagnostic mechanism only; not promoted to a codec; [score codec gate](2026-09-18-thq-score-codec-gate.md) |
| INT8 / THQ controls | 384 / 96 | native parity and full 1M serving measured | low / fixed control | fixed control | CONTROL | establishes the production-shaped reference path; [native control](2026-09-30-native-mdbx-segment-sweep.md) |

This inventory is deliberately broader than the serving matrix. `FINALIST`
means that both quality and native packed timing are available; `CONTROL`
means a comparison/reference arm; `NEGATIVE` records a bounded rejection on
the stated shell; `DEFERRED` means that the next required experiment is still
explicit. No row promotes an algorithm solely because its decoded FP32 path
was fast.

## Native compressed retrieval

The matched R4 cascade is `candidate shortlist → THQ top-128 → packed codec
scorer → ordered top-10`, with no reconstructed FP32 vector in the timed codec
stage for the two completed arms.

| Arm | Payload | THQ p50 ms | codec p50 ms | total p50 ms | parity | Status |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| LSQ32 | 36 B | 1.371 | 2.184 | **3.551** | 152/152 | measured |
| LSQ48 | 52 B | 1.414 | 3.479 | **4.904** | 152/152 | measured |
| PLSQ8x6 | — | — | — | — | — | native scorer pending |
| TQ1 | — | — | — | — | — | native scorer pending |
| TQ1+PQ8 | — | — | — | — | — | native scorer pending |
| RSLM1 | — | — | — | — | — | native scorer pending |

Source: [`2026-09-23-native-compressed-lsq-result.md`](2026-09-23-native-compressed-lsq-result.md).

## Full 1M flat serving

The accepted normalized 1M receipt is an INT8 control and THQ→INT8 cascade;
its payload is not a matrix of native codec finalists.

| Path | p50 ms | Interpretation |
| --- | ---: | --- |
| Direct INT8 flat | 106.384 | native INT8 control |
| THQ → INT8 | 83.672 | native THQ routing plus INT8 rerank |
| LSQ32/LSQ48/PLSQ/TQ/RSLM1 | — | not measured on the full 1M flat path |

The production receipt explicitly remains normalized in-memory kernel evidence;
it does not select a codec or establish an MDBX serving winner.

## Persistent MDBX layout

The completed segment sweep uses a split final-code projection (INT8 plus scale,
388 bytes per logical row), fixed bounded top-k workspaces, raw timing samples,
and independent percentile/checksum replay. The row and segment profiles now
share this compact final-code representation. The sweep varies segment size;
the bounded durable-batch sweep separately varies commit granularity.

| Gate | Result |
| --- | --- |
| Segment-size sweep | measured, 16–4096 rows, 152/152 parity |
| Durable batch sweep | measured, 1,024–1,000,000 documents per batch |
| Concurrent generation publication | pending |
| Cold OS-cache/restart/recovery | pending |
| Finalist-specific MDBX layouts | pending until native scorers exist |

See [`2026-09-30-native-mdbx-segment-sweep.md`](2026-09-30-native-mdbx-segment-sweep.md)
and [`2026-10-01-native-mdbx-batch-sweep.md`](2026-10-01-native-mdbx-batch-sweep.md).

## Interpretation and next gates

The evidence supports a research conclusion, not a product selection: LSQ32
and LSQ48 are the only finalists with both quality evidence and native packed
cascade timing. The remaining arms require native compressed scorers before
their latency can be compared. The ordered next gates are:

1. implement and audit native PLSQ8x6, TQ1/TQ1+PQ8 and RSLM1 scorers;
2. fill the matched retrieval table with identical R4 topology and parity;
3. run a full 1M flat matrix using packed payloads;
4. materialize finalist-specific compact MDBX projections;
5. run concurrent publication, cold/restart/recovery, and fresh untouched-qrels
   gates before any Pareto or default recommendation.
