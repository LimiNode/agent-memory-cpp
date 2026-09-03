# Frozen 12-bit A@256 scale transfer

Date: 2026-08-27. Protocol PR; measurements are intentionally absent.

## Question

Does the current best learned route retain a useful cost-quality frontier when
the same German query set and nested corpus grow from 25k to 100k and 1M
documents, without changing model bytes, width, probe count, or candidate
ceiling?

This is not a continuation of v4 treatment selection. V4 selected no new
treatment, while frozen treatment A at 256 probes remains the best measured
serving baseline.

## Frozen matrix

Three German raw-Euclidean model artifacts trained at 25k are replayed at all
three nested scales. Every row uses 12 bits, 256 logit-guided probes, a hard
10% candidate ceiling, Hamming top-768, ADC top-64, and optional exact E5 over
that same top-64. The 64-vector pool is fixed by the preceding exact-rerank
ablation; it is not retuned here.

Two threshold policies are reported without selection:

- `per_scale_document_median` is the production-oriented primary policy. Model
  bytes remain frozen while unsupervised index centering is recalibrated for the
  current corpus.
- `frozen_de_25k_document_median` reuses the 25k index threshold at 100k/1M and
  diagnoses whether threshold calibration hides model-transfer failure.

The nested roots must have identical query IDs, query-vector bytes, ITQ query
codes, and ADC query projections. Document order is allowed to differ between
materializations, so nesting is checked as `25k subset 100k subset 1M` over
canonical document-ID sets rather than by an invalid ordered-prefix shortcut.
The evidence writer replays those checks instead of inferring nesting from
directory names.

## Measurements

Every scale/seed/policy row reports occupied buckets, mean/p95/max posting
length, posting skew, accepted probes, requested postings, unique candidates,
candidate fraction, raw/Hamming/ADC E5-oracle survival, ADC-only nDCG,
exact-E5@64 nDCG, full-E5 nDCG, and deterministic sequences.

The same routes are materialized into repository-pinned MDBX. Native warm-path
timing is split into address generation, lookup/decode, generation dedup and
ceiling, Hamming score materialization, optimized sparse Hamming distance,
top-768 partition, selected-prefix sort, result materialization, ADC, resident
exact-E5@64, and total. The Hamming path must use the library's runtime-selected
`hardware_popcount` backend; portable bit-at-a-time timing is rejected by the
evidence gate. Exact E5 reads the bound external FP32 matrix and therefore
measures warm resident access; cold-storage fetch remains outside scope. Index
bytes and FP32 hot bytes are explicit. The
evidence gate requires the exact `external/libmdbx` and
`external/mdbx-containers` commits frozen in the contract; an AUTO/system
dependency resolution is not admissible evidence.

## Decision

The primary per-scale-median path transfers only if all seeds keep candidate
fraction at most 10%, ADC64 E5-oracle survival at least .65, exact-E5@64 nDCG
retention against full exact E5 at least .85, and 1M native quality-mode p95 at
most 10 ms. Frozen-threshold rows are diagnostic and cannot replace the primary
policy post hoc.

A pass licenses a separate 12/14/16-bit `width x scale x native-budget` study.
A failure requires mechanism diagnosis before width tuning.

## Engineering correction before final timing

The first local draft timing used a benchmark-local byte/shift population-count
loop instead of the already available optimized library kernel. Its candidate,
Hamming shortlist, ADC, exact-E5, and quality sequences were correct, but its
Hamming and total latency were not representative of the intended native
backend. That timing is superseded and cannot license or block a width study.

The correction changes no treatment, threshold, candidate budget, shortlist
depth, or decision gate. It replaces only the defective distance kernel, adds
the component timers above, binds the selected Hamming backend in the native
report, and requires byte-identical deterministic sequences when the final
native measurement is replayed.

The superseded draft reported 19.4-19.9 ms p95 for Hamming and up to 25.955 ms
p95 total at 1M. Those values describe the defective bit-at-a-time kernel only;
they are retained here for provenance and are not benchmark evidence for the
native backend.

## Corrected measurement result

Date: 2026-08-28. Measured protocol/source commit: `a99456f`. Status:
completed positive quality and native serving-gate result.

The quality runner completed all 18 predeclared rows. The three corpora have
identical query-vector, query-code, and query-projection bytes; their canonical
document-ID sets satisfy `25k subset 100k subset 1M`. The independent evidence
replay reproduced the quality result and materialization byte for byte, then
replayed every native candidate, Hamming, ADC, and exact-E5 sequence. Native
storage provenance resolves authoritatively to the two submodule commits frozen
in the contract. The report records `hamming_backend = hardware_popcount`, and
the evaluator source manifest binds `BinarySignature.cpp` as well as the
benchmark driver.

### Primary-policy quality

| Scale | Seed | Candidates | ADC64 E5 survival | ADC nDCG@10 | Exact64 nDCG@10 | Full E5 nDCG@10 | Exact retention |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 25k | 2026082701 | 6.215% | .7658 | .5881 | .6092 | .7120 | .8557 |
| 25k | 2026082702 | 6.358% | .7816 | .6278 | .6525 | .7120 | .9165 |
| 25k | 2026082703 | 6.309% | .7618 | .5833 | .6179 | .7120 | .8678 |
| 100k | 2026082701 | 6.117% | .7789 | .5673 | .6093 | .7048 | .8645 |
| 100k | 2026082702 | 6.235% | .7737 | .6060 | .6390 | .7048 | .9067 |
| 100k | 2026082703 | 6.225% | .7671 | .5724 | .6137 | .7048 | .8708 |
| 1M | 2026082701 | 6.080% | .7316 | .4870 | .5549 | .6471 | .8575 |
| 1M | 2026082702 | 6.214% | .7632 | .5085 | .6017 | .6471 | .9300 |
| 1M | 2026082703 | 6.194% | .7329 | .4832 | .5790 | .6471 | .8948 |

All nine primary rows pass the predeclared candidate, survival, and exact-E5
retention gates. Reusing the frozen 25k medians instead of recalibrating them at
each scale changes candidate fraction only modestly; those diagnostic rows were
not eligible to replace the primary policy.

### Native warm-path result

| Scale | Candidate range/query | Total p50 range | Maximum total p95 | Hamming p95 range | Exact-E5 p95 range |
| --- | ---: | ---: | ---: | ---: | ---: |
| 25k | 1,554-1,589 | .953-.987 ms | 1.038 ms | .099-.100 ms | .037-.038 ms |
| 100k | 6,117-6,235 | 1.278-1.353 ms | 1.482 ms | .219-.232 ms | .040-.042 ms |
| 1M | 60,799-62,141 | 6.885-7.371 ms | 8.247 ms | 2.755-2.803 ms | .048-.050 ms |

At 1M, Hamming p95 decomposes as follows across the three primary seeds:

| Component | p95 range |
| --- | ---: |
| score materialization | .376-.461 ms |
| optimized sparse distance | 1.434-1.579 ms |
| top-768 partition | .822-.874 ms |
| selected-prefix sort | .047-.049 ms |
| result materialization | .003-.004 ms |

The corrected overall decision is `selected = frozen_A_12bit_256`. Quality
transfer passes and the maximum primary 1M p95 is 8.247 ms, below the frozen
10 ms gate. The separate `width x scale x native-budget` study is therefore
licensed.

The remaining scale cost is also clearer. At 1M, generation-array dedup and the
candidate ceiling cost 3.876-4.237 ms p95, optimized Hamming plus top-K costs
2.755-2.803 ms, and resident exact E5 over 64 vectors costs only .048-.050 ms.
Exact reranking is not the scale blocker in this warm-resident setup. Wider
addresses and smaller candidate budgets remain worth testing because the
12-bit route still admits roughly 61k candidates per query, but they are now an
optimization study rather than a rescue from a failed serving gate.

### Reproduction and retained artifacts

The raw artifacts remain uncommitted under
`tmp/neuroute-frozen-scale-transfer/` in the protocol worktree, as required by
the repository raw-artifact policy:

| Artifact | SHA-256 |
| --- | --- |
| `result.json` | `7cf6f48e8167bbe9ce27b935e0b67369365070e541e525ec9e61b2e301c15316` |
| `materialized/manifest.json` | `39119ab6fe95b3b470bed383790f5cc177b35965ba40928766de7b219cefd57d` |
| `native-result.json` | `3218ca48d1ba151f2831042d17e5f0954bc60cf458b91e1bb5ae83430aaf5de6` |
| `evidence.json` | `82bb1d46e6a0da4b55cfeeb2a9df85f03b549045453fbcf90ccbaa4793dafa98` |

Timing is directional: one Windows AMD64 host, MinGW Makefiles, GCC 15.2.0,
Release C++17, two warm-up passes, and nine measured passes. Cold FP32 fetch,
query embedding, model inference, and process RSS are outside this native
lower-bound measurement.
