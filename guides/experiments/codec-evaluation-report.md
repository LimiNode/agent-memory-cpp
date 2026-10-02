# Codec evaluation report

This document is the consolidated navigation report for codec research. It
separates algorithmic quality, native compressed retrieval, full-corpus flat
serving, and persistent MDBX layout. A check mark means that the cited receipt
contains that measurement; `pending` means that no result is inferred from a
different representation.

The serving comparison has three canonical modes: full-flat packed 1M,
Prototype-IVF/balanced routed cascade, and Modern R4 cascade. Their exact
contracts and planned matched matrix are in
[`2026-10-02-serving-mode-bakeoff.md`](2026-10-02-serving-mode-bakeoff.md).
The fixed-top128 candidate-local scorer is diagnostic only and is not a fourth
production mode.

## Status matrix

| Arm | Algorithmic quality | Native compressed cascade | Full 1M flat | MDBX persistence |
| --- | --- | --- | --- | --- |
| LSQ32 | measured | measured | **measured (1M packed flat)** | pending |
| LSQ48 | measured | measured | **measured (1M packed flat)** | pending |
| PLSQ8x6x8 | measured | **measured (matched R4 packed)** | **measured (1M packed flat)** | pending |
| PLSQ8x4x8 | measured | **measured (matched R4 packed)** | **measured (1M packed flat)** | pending |
| TQ1 | measured | measured (candidate-local packed) | pending | pending |
| TQ1+PQ8 | measured/partial serving | measured (candidate-local packed) | pending | pending |
| RSLM1 | measured | **measured (matched R4 packed)** | pending | pending |
| INT8 reference | control | control | measured | measured |
| INT8 matched R4 control | control | **measured (matched R4)** | n/a | n/a |

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

### Three-mode status

| Codec | Full-flat packed 1M | Prototype-IVF / balanced | Modern R4 | Interpretation |
| --- | --- | --- | --- | --- |
| INT8 | measured control | pending matched packed route | measured control; unified refresh pending | control |
| LSQ32 | measured packed | pending | measured under older contract; refresh pending | finalist, not yet cross-mode matched |
| LSQ48 | measured packed | pending | measured under older contract; refresh pending | finalist, not yet cross-mode matched |
| TQ1 | pending packed payload | pending | measured under older contract; refresh pending | candidate-local/legacy rows cannot fill flat or balanced columns |
| TQ1+PQ8 | pending packed payload | pending | measured under older contract; refresh pending | same boundary as TQ1 |
| PLSQ8x6x8 | measured + independent replay | pending | measured packed; refresh pending | strongest current packed coverage |
| RSLM1 | pending packed payload | pending | measured packed; refresh pending | full-flat and balanced gates remain open |

No final speed ranking is inferred from this mixed-generation table. A valid
ranking requires one harness, one warmup/repeat contract and one audit across
the same mode.

### Matched R4 final-reranker corrective control

The corrective rows below (INT8, PLSQ8x6x8 and RSLM1) use the same frozen
candidate stream and THQ top-128 stage with one warmup and five measured
repeats. The values are end-to-end over this in-memory R4 shell; decode, MDBX
I/O and fresh qrels are outside scope. Older rows in the historical comparison
table below retain their original run contracts and are not silently promoted
to this refreshed methodology.

| Final reranker | Side bytes/doc | rerank p50 ms | total p50 ms | total p95/p99 ms | audit |
| --- | ---: | ---: | ---: | ---: | --- |
| INT8 control | 388 | 0.0609 | **0.1627** | 0.2030 / 0.2592 | canonical percentile replay PASS |
| PLSQ8x6x8 | 52 | 0.6996* | **0.6996** | 0.7810 / 0.8465 | ordered parity 152/152 |
| RSLM1 | 52 | 0.8891* | **0.8891** | 1.0032 / 1.0696 | raw structural replay PASS |

`*` The PLSQ/RSLM runner reports a combined matched stage in this corrective
receipt; the separate codec-only scorer timings remain in their dedicated
candidate-local receipts. The complete source/hash binding is in
[`2026-10-01-native-matched-r4-final-reranker-control.result.json`](2026-10-01-native-matched-r4-final-reranker-control.result.json).

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
| PLSQ8x6x8 | 52 B | matched THQ top-128 | packed | **0.6996** | 152/152 | frozen R4 candidate stream → native packed scorer; [wave note](2026-10-03-native-flat-and-r4-finalist-wave.md) |
| PLSQ8x4x8 | 36 B | matched THQ top-128 | packed | **3.3612** | 152/152 | frozen R4 candidate stream → native packed scorer; [wave note](2026-10-03-native-flat-and-r4-finalist-wave.md) |
| RSLM1 | 52 B | matched THQ top-128 | packed | **0.8891** | structural audit | canonical R4 candidate stream → native packed scorer; packed reference parity remains separate; [wave note](2026-10-03-native-flat-and-r4-finalist-wave.md) |

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
| LSQ32 | **178.670** | 1M packed LSQ32 scan; p95 188.975, p99 209.586; one warmup + five repeats; independent ordered top-10 replay **152/152** |
| LSQ48 | **252.526** | 1M packed LSQ48 scan; p95 264.469, p99 269.872 |
| PLSQ8x6x8 | **177.5215** | 1M packed PLSQ scan; p95 199.7548, p99 218.1127; one warmup + five measured repeats; independent ordered top-10 replay **152/152** |
| PLSQ8x4x8 | **148.6951** | optional 1M packed low-byte control; p95 162.0236, p99 178.5959; structural audit PASS, independent 8x4 reference replay pending |
| TQ1 | **238.600** | 1M packed TQ1 reconstructed-cosine scan; p95 246.370, p99 258.653; one warmup + five repeats; independent ordered top-10 replay **152/152** |
| TQ1+PQ8 | **289.122** | 1M packed TQ1+PQ8 scan; p95 302.801, p99 323.720; 64 B/doc persisted final norm; one warmup + five repeats; independent ordered top-10 replay **152/152** |
| RSLM1 | **245.586** | 1M faithful RSLM1 packed scan; p95 261.116, p99 272.168; 56 B/doc including final norm sidecar; one warmup + five repeats; independent ordered top-10 replay **152/152** |

The production receipt explicitly remains normalized in-memory kernel evidence;
it does not select a codec or establish an MDBX serving winner.

The refreshed PLSQ8x6x8 row is bound to the raw/audit receipt
[`2026-10-02-plsq8x6-flat-replay.result.json`](2026-10-02-plsq8x6-flat-replay.result.json);
its independent packed replay reports ordered top-10 parity for 152/152
queries. Other rows retain their documented provenance and are not silently
reinterpreted as this newer contract.

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

The evidence supports a research conclusion, not a product selection. The
remaining work is ordered around one comparable three-mode bake-off, rather
than promoting mixed-generation rows into a ranking:

1. complete and unify **all three serving modes** (full-flat packed 1M,
   Prototype-IVF/balanced cascade, and Modern R4) for the frozen finalist set;
2. freeze payloads, routes, scorer implementations, timing configuration and
   provenance manifests, then run independent audits over the raw evidence;
3. execute fresh untouched qrels against the frozen configurations, reporting
   exact-oracle Recall@K, qrels-based nDCG@10/MRR and quality decomposition for
   each routed mode;
4. build the quality/latency/footprint/rebuild Pareto frontier and select only
   2–3 survivors for persistent MDBX layout, publication, cold/restart and
   recovery gates;
5. make no default or winner recommendation until those gates are complete.

Candidate-local top-128 scorer measurements remain diagnostic components, not a
fourth serving mode. Any arm that cannot produce a faithful packed payload or
independent parity receipt remains explicitly pending or blocked rather than
being filled with decoded-FP32 or legacy-contract numbers.
