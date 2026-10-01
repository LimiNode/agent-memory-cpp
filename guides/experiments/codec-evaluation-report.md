# Codec evaluation report

This document is the consolidated navigation report for codec research. It
separates algorithmic quality, native compressed retrieval, full-corpus flat
serving, and persistent MDBX layout. A check mark means that the cited receipt
contains that measurement; `pending` means that no result is inferred from a
different representation.

## Status matrix

| Arm | Algorithmic quality | Native compressed cascade | Full 1M flat | MDBX persistence |
| --- | --- | --- | --- | --- |
| LSQ32 | measured | measured | **measured (1M packed flat)** | pending |
| LSQ48 | measured | measured | **measured (1M packed flat)** | pending |
| PLSQ8x6x8 | measured | **measured (matched R4 packed)** | **measured (1M packed flat)** | pending |
| TQ1 | measured | measured (candidate-local packed) | pending | pending |
| TQ1+PQ8 | measured/partial serving | measured (candidate-local packed) | pending | pending |
| RSLM1 | measured | **measured (matched R4 packed)** | pending | pending |
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
| PLSQ8x6x8 | 52 | 0.656438 | source note | measured; packed native candidate gate |
| PLSQ8x4x8 | 36 | 0.649133 | source note | measured; packed native candidate gate |
| TQ+ direct | 56 | 0.657829 | source note | measured; native TQ/PQ gate |

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
| PLSQ8x4x8 / PLSQ8x6x8 | 36 / 52 | nDCG .649133 / .656438 | measured | — | CONTROL | packed candidate-local scorer measured; [packed PLSQ gate](2026-10-02-native-plsq-packed-cascade.md) |
| TQ1 | 52 | nDCG .659176 | measured | — | CONTROL | packed TQ1/PQ8 scorer and parity gate measured; [TQ wave](2026-09-26-packed-tq1-pq8-serving-closure.md) |
| TQ2 | — | — | — | — | DEFERRED | no native packed scorer or matched quality row; [TQ wave](2026-09-24-thq-tq1-residual-correction-next-wave.md) |
| TQ+ | 56 | nDCG .657829 | measured | — | DEFERRED | quality control only until native TQ+ scorer exists; [follow-up](2026-09-22-faithful-binary-followups.md) |
| RSLM1 / RSLM2 / RSLM3 / RSLM4 | 52 / 96 / 144 / 192 | RSLM1 nDCG .658220; later rows use separate persistable-gate metrics | measured / — | — | CONTROL | faithful RSLM1 packed candidate scorer measured; [packed RSLM1 gate](2026-10-02-native-rslm1-packed-cascade.md) |
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

The measurements below separate matched full-cascade topology from
candidate-local packed decode-and-score fixtures. Candidate-local rows do not
include THQ-routing latency and must not be compared as if they were the LSQ
cascade.

| Arm | Payload | THQ p50 ms | codec p50 ms | total p50 ms | parity | Status |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| LSQ32 | 36 B | 1.371 | 2.184 | **3.551** | 152/152 | measured |
| LSQ48 | 52 B | 1.414 | 3.479 | **4.904** | 152/152 | measured |
| TQ1 | 68 | 2.272 | 0.075 | 2.775 | 152/152 | packed THQ→TQ intermediate gate, 68 B layout; [TQ gate](2026-09-26-packed-tq1-pq8-serving-closure.md) |
| TQ1+PQ8 | 64/68 | 2.251 | 0.072 | 2.813 | 152/152 | packed THQ→TQ1+PQ8 gate, 64 B final-only layout; [TQ gate](2026-09-26-packed-tq1-pq8-serving-closure.md) |
| PLSQ8x6x8 | 52 B | matched THQ top-128 | packed | **3.5738** | 152/152 | frozen R4 candidate stream → native packed scorer; [wave note](2026-10-03-native-flat-and-r4-finalist-wave.md) |
| RSLM1 | 52 B | matched THQ top-128 | packed | **3.6574** | 152/152 | canonical R4 candidate stream → native packed scorer; [wave note](2026-10-03-native-flat-and-r4-finalist-wave.md) |

Source: [`2026-09-23-native-compressed-lsq-result.md`](2026-09-23-native-compressed-lsq-result.md).

The candidate-local packed decode-and-score gates measure the codec stage
without persisted predecoded FP32 payloads. They are not full-cascade rows:

| Arm | Payload | codec-only p50 (ms) | p95 (ms) | p99 (ms) | ordered parity | Scope |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| PLSQ8x4x8 | 36 B | 0.3801 | 0.4538 | 0.5166 | 152/152 | direct packed dot, candidate-local shell |
| PLSQ8x6x8 | 52 B | 0.5377 | 0.6136 | 0.7252 | 152/152 | direct packed dot, candidate-local shell |
| RSLM1 | 52 B | 0.8042 | 1.1402 | 1.2950 | 152/152 | direct transform-domain score, candidate-local shell |

These are candidate-local packed decode-and-score timings: candidate IDs are
already fixed by the frozen shell, so they are not end-to-end THQ-routing
latency or a direct-compressed production scorer claim. See
[`2026-10-02-native-plsq-packed-cascade.result.json`](2026-10-02-native-plsq-packed-cascade.result.json).

## Full 1M flat serving

The accepted normalized 1M receipt is an INT8 control and THQ→INT8 cascade;
its payload is not a matrix of native codec finalists.

| Path | p50 ms | Interpretation |
| --- | ---: | --- |
| Direct INT8 flat | 106.384 | native INT8 control |
| THQ → INT8 | 83.672 | native THQ routing plus INT8 rerank |
| LSQ32 | **223.032** | 1M packed LSQ32 scan; p95 238.494, p99 244.840 |
| LSQ48 | **252.526** | 1M packed LSQ48 scan; p95 264.469, p99 269.872 |
| PLSQ8x6x8 | **188.6500** | 1M packed PLSQ scan; p95 199.5890, p99 207.6412 |
| TQ1/TQ1+PQ8/RSLM1 | — | no full 1M packed payload; decoded/candidate-local values excluded |

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

The evidence supports a research conclusion, not a product selection: LSQ32,
LSQ48 and PLSQ8x6x8 now have native full-flat packed evidence, while PLSQ8x6x8
and RSLM1 also have matched R4 packed evidence. TQ1/TQ1+PQ8/RSLM1 full-flat
and all MDBX rows remain separate gates. The ordered next gates are:

1. add native packed TQ/RSLM1 corpus payloads before filling their flat rows;
2. extend the matched R4 runner to TQ1/TQ1+PQ8 and preserve packed parity;
3. materialize finalist-specific compact MDBX projections;
4. run concurrent publication, cold/restart/recovery, and fresh untouched-qrels
   gates before any Pareto or default recommendation.
