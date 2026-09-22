# Native predecoded complete-cascade control

Status: `EXECUTED` / `AUDITED` (2026-09-22, v2)

**BBQ correction note:** the `Elastic BBQ OSQ` row below was produced from the
pre-correction payload that is now marked `SUPERSEDED` by the v4 centered
unit-cosine replay in `2026-09-22-faithful-binary-followups.md`. It remains a
historical native control only; the corrected BBQ v4 artifacts have not yet
been rerun through this native predecoded gate, so no native latency claim is
carried over to them.

This gate is the native correctness/orchestration bridge after the source-bound
LSQ, TurboQuant, BBQ, joint2 and faithful RSLM replays. The matched protocol is:

```text
frozen R4 candidate stream (5k/query)
  -> native THQ4 interval-squared byte-LUT top128
  -> one persisted finalist payload (joint2, LSQ, TurboQuant, BBQ or RSLM)
  -> native cosine top10 rerank
```

The native executable uses the same deterministic document-ID tie break as the
Python references. It reports per-query p50/p95/p99, THQ pages, and logical
codec pages touched by the 128-document rerank.

## Important scope boundary

The payload rows passed to the C++ hot loop are predecoded FP32 rows generated
from the persisted source-bound artifacts. Therefore this is a **native THQ
plus native cosine-rerank serving control**, not a compressed-codec decode or
SIMD claim. Decode correctness remains covered by the independent LSQ,
TurboQuant, BBQ, joint2 and RSLM audits. The gate is candidate-local: it does not scan
the 1M corpus for every codec arm and it does not select a production layout.

## Results

| arm | side bytes | mean nDCG@10 | ordered top-10 parity | set parity | p50 total ms | p95 total ms | p99 total ms | mean codec pages |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| THQ-joint2 | 32 | 0.655113 | 1.000 | 1.000 | 1.411 | 1.510 | 1.777 | 112.91 |
| LSQ32 | 32 | 0.657264 | 1.000 | 1.000 | 1.389 | 1.522 | 1.651 | 112.91 |
| LSQ48 | 48 | 0.661515 | 0.993 | 1.000 | 1.368 | 1.498 | 1.654 | 114.34 |
| TurboQuant 1-bit | 52 | 0.659176 | 1.000 | 1.000 | 1.378 | 1.527 | 1.726 | 114.88 |
| TurboQuant 2-bit | 100 | 0.656254 | 1.000 | 1.000 | 1.392 | 1.497 | 1.634 | 116.80 |
| Elastic BBQ OSQ | 62 logical / 64 aligned | 0.635520 | 1.000 | 1.000 | 1.395 | 1.511 | 1.753 | 115.28 |
| faithful RSLM3 | 146 cosine payload | 0.656721 | 1.000 | 1.000 | 1.421 | 1.577 | 1.781 | 118.71 |
| faithful RSLM4 | 194 cosine payload | 0.659201 | 1.000 | 1.000 | 1.407 | 1.521 | 1.705 | 120.64 |

The THQ stage touches a mean `4,452.85` logical 4-KiB pages in the frozen
candidate-local layout. The `23,438` pages of the full 96 MB table belong to
the separate full-corpus scan control and are not charged to this 5k-candidate
gate. Native and source-bound THQ retained sets are equal; the small
ordered-top128 difference on two queries is a floating-point tie ordering
effect, not a retained-document loss. LSQ48 has one ordered top-10 tie-order
difference while retaining the same top-10 set on all 152 queries.

## Evidence

The v2 audit is fail-closed over source result hashes, qrels hashes, 152 rows
per arm, THQ page accounting, top-128/top-10 parity, and nDCG replay. It
reports `status: PASS` and includes a source-bound joint2 comparison. The
native runner also records hashes for the joint2 packed symbols and codebook.

External artifacts:

* result: `E:/_repoz/research-wave-2026-09-22-native-complete-cascade-v2/native-complete-cascade.result.json`
* audit: `E:/_repoz/research-wave-2026-09-22-native-complete-cascade-v2/native-complete-cascade.audit.json`

The v2 run confirms the same limitation as v1: the timings are dominated by a
shared native THQ scan plus cosine rerank over predecoded FP32 rows. They are
not compressed decode or codec-specific serving timings.

The next native step is codec-specific compressed decode (LSQ
codebook gathers, RSLM symbol unpack/FWHT, TurboQuant query estimator, and
Lucene BBQ query-side correction). Until that separate gate exists, these
numbers are a lower-bound hot-path comparison, not a production architecture
decision.
