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
ceiling, Hamming, ADC, resident exact-E5@64, and total. Exact E5 reads the bound
external FP32 matrix and therefore measures warm resident access; cold-storage
fetch remains outside scope. Index bytes and FP32 hot bytes are explicit. The
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

## Measurement result

Date: 2026-08-27. Measured protocol/source commit: `e22717a`. Status:
completed negative serving-gate result with positive quality transfer.

The quality runner completed all 18 predeclared rows. The three corpora have
identical query-vector, query-code, and query-projection bytes; their canonical
document-ID sets satisfy `25k subset 100k subset 1M`. The independent evidence
replay reproduced the quality result and materialization byte for byte, then
replayed every native candidate, Hamming, ADC, and exact-E5 sequence. Native
storage provenance resolves authoritatively to the two submodule commits frozen
in the contract.

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
each scale changes candidate fraction only modestly and does not rescue native
latency; those diagnostic rows were not eligible to replace the primary policy.

### Native warm-path result

| Scale | Candidate range/query | Total p50 range | Maximum total p95 | Hamming p95 range | Exact-E5 p95 range |
| --- | ---: | ---: | ---: | ---: | ---: |
| 25k | 1,554-1,589 | 1.378-1.413 ms | 1.486 ms | .491-.507 ms | .039-.041 ms |
| 100k | 6,117-6,235 | 2.768-3.082 ms | 3.353 ms | 1.802-1.944 ms | .042-.046 ms |
| 1M | 60,799-62,141 | 22.351-23.108 ms | 25.955 ms | 19.416-19.874 ms | .049-.049 ms |

The overall decision is therefore `selected = null`. Quality transfer passes,
but the maximum 1M p95 is 25.955 ms, well above the frozen 10 ms gate, so the
general width-by-scale-by-budget study is not licensed by this protocol.

The failure mechanism is localized. A 12-bit address space has all 4,096
buckets occupied at 1M. Two hundred and fifty-six probes return about 61k
single-placement candidates and 238-243 KiB of postings per query. Hamming
selection over that pool alone costs about 19.4-19.9 ms p95, whereas resident
exact E5 over 64 vectors costs only about .049 ms p95. Exact reranking is not
the scale blocker; fixed-width coarse routing is.

The next protocol should first test the mechanism directly with frozen model
bytes: wider addresses and lower probe/candidate budgets at 1M, with the same
quality stages and no treatment learning. Only if that focused diagnostic finds
an admissible region should it expand into the full width x scale x budget
matrix. This is a protocol consequence, not a post-hoc selection from these
rows.

### Reproduction and retained artifacts

The raw artifacts remain uncommitted under
`tmp/neuroute-frozen-scale-transfer/` in the protocol worktree, as required by
the repository raw-artifact policy:

| Artifact | SHA-256 |
| --- | --- |
| `result.json` | `22a9b263a287fe4224c2181b23458fdfdeeb50704eee063748b290c52b48de3b` |
| `materialized/manifest.json` | `33e726274d02d799eae180d80f15c997951a8e9da80bf43565d3e270a8e9d04f` |
| `native-result.json` | `70c4e82ccf623cee69c42856b83b038b48af49700965c01d47a10ec9fe15e937` |
| `evidence.json` | `8738ae7a6aeec9e3ada6c9a552694843f84431244d3766e6a67634540b65925d` |

Timing is directional: one Windows AMD64 host, MinGW Makefiles, GCC 15.2.0,
Release C++17, two warm-up passes, and nine measured passes. Cold FP32 fetch,
query embedding, model inference, and process RSS are outside this native
lower-bound measurement.
