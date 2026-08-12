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

The evaluator records six separately warmed, isolated full-query measurements
over the fixed raw candidate unions captured from its first MIH pass:

1. the original direct indirect candidate-score path;
2. copying sparse candidate codes into a contiguous scratch buffer;
3. the batch Hamming loop over an already contiguous scratch buffer;
4. writing `(position, distance)` score-buffer entries from prepared distances;
5. the full gather -> contiguous batch Hamming -> score-buffer reconstruction;
6. `nth_element` plus sorting top-768 from prepared scores.

For the individual contiguous-loop and score-buffer measurements, prerequisite
preparation is deliberately outside the timer. They are component attribution,
not additive end-to-end measurements. Every component has one unrecorded warm
pass and seven recorded passes. The report requires `hardware_popcount` Hamming
and `avx2` exact-vector dispatch; its source provenance includes both dispatch
implementations.

## Result

| Component | Median ms/query |
| --- | ---: |
| Existing `full_hamming_on_candidates` stage | 0.1606 |
| Direct indirect candidate-score path | 0.1652 |
| Candidate-code gather to scratch | 0.1281 |
| Pure contiguous batch Hamming loop | 0.0529 |
| Score-buffer materialization only | 0.0255 |
| Gather -> contiguous Hamming -> score buffer | 0.2121 |
| Top-768 selection from prepared scores | 0.2193 |

The pure contiguous Hamming loop is about one third of the direct candidate
score path. Copying the sparse candidates into a scratch layout costs more than
the contiguous Hamming loop itself, and the reconstructed gather path is slower
than the existing direct path. Therefore a scratch gather is not an optimization
for this warm-RAM control.

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
asset `mih-hamming-cost-evidence-v1.zip`. Archive SHA-256:
`bdc0cd7d97aeb6dd0d17b933242ac34b41d74148b06597d6ad68d13d6bd26673`.
Internal bundle-root SHA-256:
`a8dd68f6ca45ee949d2791e3063ea398a3e3515335c453bda7e30a3ccef5b5ef`.

This is a local warm-memory component-cost experiment, not a storage-latency or
production-SLO measurement. It does not establish the best low-level layout for
a future lower-candidate MIH index; it only rules out treating the current
Hamming timing as the cost of POPCNT alone.
