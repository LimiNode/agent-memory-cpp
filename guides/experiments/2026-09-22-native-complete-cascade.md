# Native THQ + predecoded-rerank gate

Status: `EXECUTED` / `AUDITED` (2026-09-23, v3)

**BBQ correction note:** the `Elastic BBQ direct` row below uses the corrected
v4 centered unit-cosine payload without the optional block-PCA preconditioner.
The superseded pre-correction `.635520` row is no longer used by this gate.

This gate is the native correctness/orchestration bridge after the source-bound
LSQ, TurboQuant, BBQ, joint2 and faithful RSLM replays. The matched protocol is:

```text
frozen R4 candidate stream (5k/query)
  -> native THQ4 interval-squared byte-LUT top128
  -> one persisted finalist payload (joint2, LSQ, TurboQuant, BBQ or RSLM)
  -> native cosine top10 rerank
```

The native executable uses the same deterministic document-ID tie break as the
Python references. The predecoded cosine scorer uses scalar binary64
accumulation to match the source replay's numerical contract. It reports
per-query p50/p95/p99, THQ pages, and logical codec pages touched by the
128-document rerank.

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
| THQ-joint2 | 32 | 0.655113 | 1.000 | 1.000 | 1.515 | 1.627 | 1.791 | 112.91 |
| LSQ32 | 32 | 0.657264 | 1.000 | 1.000 | 1.470 | 1.581 | 1.619 | 112.91 |
| LSQ48 | 48 | 0.661515 | 1.000 | 1.000 | 1.474 | 1.580 | 1.645 | 114.34 |
| TurboQuant 1-bit | 52 | 0.659176 | 1.000 | 1.000 | 1.487 | 1.650 | 2.152 | 114.88 |
| TurboQuant 2-bit | 100 | 0.656254 | 1.000 | 1.000 | 1.472 | 1.586 | 1.683 | 116.80 |
| Elastic BBQ direct | 62 logical / 64 aligned | 0.573386 | 1.000 | 1.000 | 1.465 | 1.630 | 1.817 | 115.28 |
| faithful RSLM3 | 146 cosine payload | 0.656721 | 1.000 | 1.000 | 1.511 | 1.697 | 2.107 | 118.71 |
| faithful RSLM4 | 194 cosine payload | 0.659201 | 1.000 | 1.000 | 1.543 | 1.672 | 1.987 | 120.64 |

The THQ stage touches a mean `4,452.85` logical 4-KiB pages in the frozen
candidate-local layout. The `23,438` pages of the full 96 MB table belong to
the separate full-corpus scan control and are not charged to this 5k-candidate
gate. Native and source-bound THQ retained sets are equal; the small
ordered-top128 difference on two queries is a floating-point tie ordering
effect, not a retained-document loss.

The original v2 artifact had one LSQ48 ordered difference on query 118. It was
not an exact tie: the binary32 native accumulator ranked document `350318`
(`0.8586982488632202`) above `848182` (`0.8586980104446411`), a gap of four
binary32 ULPs, while the Python binary64 reference ranked `848182`
(`0.8586983037580791`) above `350318` (`0.8586978093543775`), an absolute
reference gap of `4.944037016452185e-7`. The v3 rerun uses binary64 scalar
accumulation and achieves required ordered top-10 parity on all 152 queries.

## Evidence

The v3 audit is fail-closed over source result hashes, qrels hashes, the frozen
candidate flat/raw files, 152 rows per arm, THQ page accounting,
top-128/top-10 parity, and nDCG replay. It independently recomputes first and
last 4-KiB pages for every candidate and rerank record, including 96-byte THQ
records that straddle a page boundary. It
reports `status: PASS` and includes a source-bound joint2 comparison. The
native runner also records hashes for the joint2 packed symbols and codebook.

External artifacts:

* result: `E:/_repoz/research-wave-2026-09-23-native-predecoded-gate-v3/native-predecoded-rerank.result.json`
* audit: `E:/_repoz/research-wave-2026-09-23-native-predecoded-gate-v3/native-predecoded-gate.audit.json`

The v3 run confirms the same limitation as v1: the timings are dominated by a
shared native THQ scan plus cosine rerank over predecoded FP32 rows. They are
not compressed decode or codec-specific serving timings.

The final compressed native shortlist is deliberately smaller: THQ-joint2,
LSQ32, LSQ48, TurboQuant1, and faithful RSLM4. That gate must execute each
compressed scorer rather than consume FP32 reconstructions, and must report
p50/p95/p99 scorer time, warm and cold runs, bytes actually fetched, global
model pages versus per-document pages, query-precompute cost, and exact top-10
parity. Until that separate gate exists, the timings above are a shared
orchestration/correctness control, not codec latency and not a production
architecture decision.
