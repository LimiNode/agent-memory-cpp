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
