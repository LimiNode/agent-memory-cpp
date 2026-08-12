# 2026-08-12 Native MIH Hamming candidate-cost decomposition

## Question

Does the native `full_hamming_on_candidates` timing from the warm-RAM 32x8 MIH
control primarily measure four 64-bit XOR/POPCNT operations, or does it include
candidate access and score-buffer work that should direct the next optimization?

## Setup

This replay uses the same MIRACL Russian 25k E5 materialization and the same
radius-one 32x8 MIH control as the native rerank-cost experiment: 1,252 queries,
seven measured warm full-query passes, Hamming K1=768, and a mean 16,119 raw
MIH-union candidates per query.

The evaluator records eight separately warmed, isolated full-query measurements
over the fixed raw candidate unions captured from its first MIH pass:

1. the original direct indirect candidate-score path;
2. copying sparse candidate codes into a contiguous scratch buffer;
3. the batch Hamming loop over an already contiguous scratch buffer;
4. writing `(position, distance)` score-buffer entries from prepared distances;
5. the full gather -> contiguous batch Hamming -> score-buffer reconstruction;
6. cloning an immutable prepared score buffer into reusable scratch storage;
7. in-place `nth_element` plus sorting top-768 from already mutable scratch;
8. clone plus in-place selection as the immutable-buffer lifecycle total.

For the individual contiguous-loop and score-buffer measurements, prerequisite
preparation is deliberately outside the timer. They are component attribution,
not additive end-to-end measurements. Every component has one unrecorded warm
pass and seven recorded passes. The report requires `hardware_popcount` Hamming
and `avx2` exact-vector dispatch; its source provenance includes both dispatch
implementations.

## Result

| Component | Median ms/query |
| --- | ---: |
| Existing `full_hamming_on_candidates` stage | 0.1610 |
| Direct indirect candidate-score path | 0.1560 |
| Candidate-code gather to scratch | 0.1284 |
| Pure contiguous batch Hamming loop | 0.0488 |
| Score-buffer materialization only | 0.0240 |
| Gather -> contiguous Hamming -> score buffer | 0.2023 |
| Score-buffer clone to reusable scratch | 0.0255 |
| Top-768 in-place selection from prepared scores | 0.1750 |
| Clone + top-768 selection total | 0.1956 |

The pure contiguous Hamming loop is about one third of the direct candidate
score path. Copying the sparse candidates into a scratch layout costs more than
the contiguous Hamming loop itself, and the reconstructed gather path is slower
than the existing direct path. Therefore a scratch gather is not an optimization
for this warm-RAM control.

The former single prepared-score top-K figure included a temporary vector
allocation and copy. The corrected replay preallocates reusable scratch storage,
separates its clone cost, and measures `nth_element` plus final sort on a
mutable buffer. The 0.1750 ms figure is the algorithmic selection cost; 0.1956
ms is the relevant total only when an immutable input score buffer must first be
cloned. The production `score_positions() -> top_k(std::move(scored))` path
does not require that extra clone.

## Interpretation

The answer is not that POPCNT is unexpectedly slow. The original Hamming stage
bundles indirect code access and score-buffer materialization around the Hamming
operation, while the subsequent top-K and generation-array deduplication are
separate, similarly material costs. The immediate optimization target remains
reducing the raw MIH candidate union, rather than replacing the 256-bit Hamming
kernel or adding a gather-copy stage.

This supports proceeding to true variable-width MIH. A later production-focused
implementation pass can revisit score-buffer layout and selection once a lower
candidate fraction establishes that those costs remain significant.

## Evidence and limits

The replayable draft evidence release is `evidence/mih-hamming-cost-v1`, with
asset `mih-hamming-cost-evidence-v3.zip`. Archive SHA-256:
`096dc8628ae8223bf7131dd8e088080e2c0e0c6df2eab1e39cb0bb50596f74af`.
Internal bundle-root SHA-256:
`148b94037581a563415d8d266892f4b25c5bc2b015e59830b7ea4703cff71224`.

This is a local warm-memory component-cost experiment, not a storage-latency or
production-SLO measurement. It does not establish the best low-level layout for
a future lower-candidate MIH index; it only rules out treating the current
Hamming timing as the cost of POPCNT alone.
