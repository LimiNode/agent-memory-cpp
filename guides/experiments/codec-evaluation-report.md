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
| LSQ32 | measured | **packed 3-mode matrix** | **measured (1M packed flat)** | pending |
| LSQ48 | measured | **packed 3-mode matrix** | **measured (1M packed flat)** | pending |
| PLSQ8x6x8 | measured | **packed 3-mode matrix** | **measured (1M packed flat)** | pending |
| PLSQ8x4x8 | measured | candidate-local packed diagnostic | **measured (1M packed flat)** | pending |
| TQ1 | measured | **packed 3-mode matrix (exact reconstructed norm)** | **measured (1M packed flat)** | pending |
| TQ1+PQ8 | measured/partial serving | **packed 3-mode matrix** | **measured (1M packed flat)** | pending |
| RSLM1 | measured | **packed 3-mode matrix** | **measured (1M packed flat)** | pending |
| INT8 reference | exact reconstructed-cosine control | exact reconstructed-cosine control | measured | measured |
| INT8 matched R4 control | control | **measured (matched R4)** | n/a | n/a |

The quality rows are the historical qrels study in
[`2026-09-22-faithful-binary-followups.md`](2026-09-22-faithful-binary-followups.md).
They are not interchangeable with latency rows: quality uses qrels and
candidate-overlap diagnostics, while serving rows require the same native
packed scorer and candidate topology.

The canonical three-mode downstream completion receipt is
[`2026-10-02-three-mode-bakeoff.completion.json`](2026-10-02-three-mode-bakeoff.completion.json);
it currently validates `21/21` packed serving rows, including mode/family/codec
identity, raw/audit hashes, independent scorer receipts, and the Modern-R4
semantic candidate identity. Prototype-IVF receipts are bound to the selected
calibrated route; this remains a checkpoint until fresh qrels and
finalist-specific MDBX persistence are complete.

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
| INT8 | measured control | **measured packed** | **measured packed** | control |
| LSQ32 | measured packed | **measured packed** | **measured packed** | finalist |
| LSQ48 | measured packed | **measured packed** | **measured packed** | finalist |
| TQ1 | measured reconstructed-cosine packed | **measured packed (exact TQ norm)** | **measured packed (exact TQ norm)** | fresh quality pending |
| TQ1+PQ8 | measured reconstructed-cosine packed | **measured packed** | **measured packed** | fresh quality pending |
| PLSQ8x6x8 | measured + independent replay | **measured packed** | **measured packed** | finalist |
| RSLM1 | measured faithful packed | **measured packed** | **measured packed** | finalist |

No final speed ranking is inferred from this mixed-generation table. A valid
ranking requires one harness, one warmup/repeat contract and one audit across
the same mode.

### Matched R4 final-reranker corrective control

The corrective rows below use the same frozen candidate stream and THQ
top-128 stage with one warmup and five measured repeats. The INT8 row is a
packed control; the PLSQ/RSLM rows are explicitly predecoded FP32 downstream
controls. Decode, MDBX I/O and fresh qrels are outside scope, and these
diagnostic rows are not promoted to the mandatory packed matrix.

| Final reranker | Side bytes/doc | rerank p50 ms | total p50 ms | total p95/p99 ms | audit |
| --- | ---: | ---: | ---: | ---: | --- |
| INT8 historical matched control | 388 | 0.0609 | 0.1627 | 0.2030 / 0.2592 | superseded shell; retained for provenance |
| PLSQ8x6x8 | 52 | 0.6996* | **0.6996** | 0.7810 / 0.8465 | predecoded FP32 control; not packed evidence |
| RSLM1 | 52 | 0.8891* | **0.8891** | 1.0032 / 1.0696 | predecoded FP32 control; not packed evidence |

`*` The PLSQ/RSLM runner reports a combined matched stage in this corrective
receipt; the separate codec-only scorer timings remain in their dedicated
candidate-local receipts. The complete source/hash binding is in
[`2026-10-01-native-matched-r4-final-reranker-control.result.json`](2026-10-01-native-matched-r4-final-reranker-control.result.json).

The measurements below separate the selected Prototype-IVF full-cascade
topology from candidate-local packed decode-and-score fixtures. Candidate-local
rows do not include THQ-routing latency and must not be compared as if they
were the LSQ cascade. The canonical Modern-R4 rows are in the three-mode
serving table.

| Arm | Payload | THQ p50 ms | codec p50 ms | total p50 ms | parity | Status |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| LSQ32 | 36 B | 1.224 | 2.160 | **3.386** | 152/152 | sparse-LUT selected; measured |
| LSQ48 | 52 B | 1.243 | 3.239 | **4.482** | 152/152 | sparse-LUT selected; measured |
| TQ1 | 68 | 2.272 | 0.075 | 2.775 | 152/152 | packed THQ→TQ intermediate gate, 68 B layout; [TQ gate](2026-09-26-packed-tq1-pq8-serving-closure.md) |
| TQ1+PQ8 | 64/68 | 2.251 | 0.072 | 2.813 | 152/152 | packed THQ→TQ1+PQ8 gate, 64 B final-only layout; [TQ gate](2026-09-26-packed-tq1-pq8-serving-closure.md) |
| PLSQ8x6x8 | 52 B | matched THQ top-128 | packed | **0.6996** | 152/152 | frozen R4 candidate stream → native packed scorer; [wave note](2026-10-03-native-flat-and-r4-finalist-wave.md) |
| PLSQ8x4x8 | 36 B | matched THQ top-128 | packed | **3.3612** | 152/152 | frozen R4 candidate stream → native packed scorer; [wave note](2026-10-03-native-flat-and-r4-finalist-wave.md) |
| RSLM1 | 52 B | matched THQ top-128 | packed | **0.8891** | structural audit | canonical R4 candidate stream → native packed scorer; packed reference parity remains separate; [wave note](2026-10-03-native-flat-and-r4-finalist-wave.md) |

Source: [`2026-09-23-native-compressed-lsq-result.md`](2026-09-23-native-compressed-lsq-result.md).

The LSQ routed rows use the selected sparse-LUT production scorer. Gather and
full-LUT parity implementations run only outside the timed interval; the
scorer-equity receipt is [`artifacts/lsq-scorer-bakeoff.json`](../../artifacts/lsq-scorer-bakeoff.json),
and the implementation-path contract is
[`artifacts/three-mode-performance-paths.json`](../../artifacts/three-mode-performance-paths.json).

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

The accepted normalized 1M receipt is an exact reconstructed-cosine INT8
control and THQ→INT8 cascade;
its payload is not a product winner or a fresh quality result. The former
scaled-dot timing remains a control-only row.

| Path | p50 ms | Interpretation |
| --- | ---: | --- |
| Direct INT8 flat exact reconstructed cosine | **407.166** | canonical 1+5 packed run; p95 417.831, p99 428.745; independent ordered top-10 replay 152/152 |
| THQ → INT8 | 83.672 | native THQ routing plus INT8 rerank |
| LSQ32 | **178.670** | 1M packed LSQ32 scan; p95 188.975, p99 209.586; one warmup + five repeats; independent ordered top-10 replay **152/152** |
| LSQ48 | **228.908** | 1M packed LSQ48 scan; p95 241.341, p99 254.029; one warmup + five repeats; independent ordered top-10 replay **152/152** |
| LSQ48 (directional historical row) | **252.526** | retained historical row; canonical receipt is the 3-mode matrix |
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

### INT8 metric correction and optimization equity

The canonical INT8 representation is packed signed codes plus scale and an
inverse code-norm sidecar (392 bytes per document including the sidecar). Its
metric is exact reconstructed cosine, not scaled dot:

`dot(code, query) / (||code|| * ||query||)`.

The sidecar norm distribution is min 0.998447, max 1.001533, mean 1.000018,
with p95 absolute deviation 0.000601 from unit norm. Exact dense and fused
native paths have ordered parity 760/760; the old scaled-dot control has only
435/760 parity against exact cosine and is therefore not used as a canonical
row. The evidence is recorded in
[`artifacts/int8-cosine-equity.audit.json`](../../artifacts/int8-cosine-equity.audit.json)
and the strict validator requires `metric=reconstructed_cosine_exact` for all
three INT8 modes.

## Persistent MDBX layout

### Corrective scorer and identity evidence (2026-10-02)

The TQ1 and TQ1+PQ8 routed rows were regenerated in separate native
invocations. TQ1 uses the persisted analytical reconstructed norm; TQ1+PQ8 uses
its own PQ-corrected final norm. Both Prototype-IVF and Modern-R4 audits report
ordered top-10 parity 152/152. RSLM1 now has an independent transform-domain
replay with the same parity. The Modern-R4 fused physical stream is bound to
the canonical candidate identity by
`artifacts/modern-r4-packed/candidate-identity.audit.json` (152/152 set and
ordered identity). These receipts are packed scorer evidence, not fresh qrels
or MDBX persistence evidence.

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

### Fresh quality oracle and packed MDBX prototype (2026-10-03)

The untouched canonical DE-1M qrels are now opened after the serving freeze.
The exact FP32 reference covers all 305 queries and reports mean nDCG@10
`0.6723946081` and mean MRR `0.6938813429`; this is the quality ceiling, not a
codec result. Routed Prototype-IVF and Modern-R4 candidates still have to be
regenerated on this query set. See
[`2026-10-03-fresh-qrels-exact.result.json`](2026-10-03-fresh-qrels-exact.result.json).

A separate prototype uses exact-oracle top-128 candidates to isolate MDBX
physical reads for a deterministic per-document INT8 proxy. Row KV measured
`0.2166 / 0.6259 / 0.8796 ms` p50/p95/p99 at 512 MiB; 4,096-row segment blobs
measured `138.6463 / 162.5034 / 172.5282 ms` at 384 MiB, with ordered parity
305/305 for both. These are not finalist-specific persistence or routed
latency evidence. See
[`2026-10-03-mdbx-packed-int8-prototype.result.json`](2026-10-03-mdbx-packed-int8-prototype.result.json).

## Interpretation and next gates

The evidence supports a research conclusion, not a product selection. The
remaining work is ordered around one comparable three-mode bake-off, rather
than promoting mixed-generation rows into a ranking:

1. complete and unify **all three serving modes** (full-flat packed 1M,
   Prototype-IVF/balanced cascade, and Modern R4) for the frozen finalist set;
2. freeze payloads, routes, scorer implementations, timing configuration and
   provenance manifests, then run independent audits over the raw evidence;
3. regenerate routed fresh-qrels candidates against the established exact
   oracle, reporting Recall@K, qrels-based nDCG@10/MRR and quality decomposition
   for each routed mode;
4. build the quality/latency/footprint/rebuild Pareto frontier and select only
   2–3 survivors for persistent MDBX layout, publication, cold/restart and
   recovery gates;
5. make no default or winner recommendation until those gates are complete.

Candidate-local top-128 scorer measurements remain diagnostic components, not a
fourth serving mode. Any arm that cannot produce a faithful packed payload or
independent parity receipt remains explicitly pending or blocked rather than
being filled with decoded-FP32 or legacy-contract numbers.
