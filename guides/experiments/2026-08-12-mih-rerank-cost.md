# 2026-08-12 Native MIH rerank cost decomposition

## Question

Does reducing the exact E5 rerank budget from K2=256 to K2=64 or 128 save
enough native serving work to justify a new compact residual scorer?

## Setup

The native hot-path harness materializes the same 256-bit ITQ codes, 384D E5
float32 vectors, ITQ query projections, and calibration-conditioned binary ADC
centroids from the MIRACL Russian 25k E5 root. It runs 32 equal 8-bit MIH bands
with local radius one, Hamming K1=768, all 1,252 queries, and seven warm
repeats. Timing excludes query encoding, cold storage I/O, and the full-corpus
oracle.

The radius-one control visits a mean 16,119 candidates per query (71.30% of
22,607 documents), so it is a native component-cost control rather than a
replacement for the budgeted-confidence quality contract in #127.

## Result

On the local warm AVX2 run:

| Component | Median ms/query |
| --- | ---: |
| MIH through Hamming K1 selection | 0.5883 |
| binary ADC on Hamming K1, K2=256 | 0.1363 |
| exact E5 rerank, K2=64 | 0.0171 |
| exact E5 rerank, K2=128 | 0.0243 |
| exact E5 rerank, K2=256 | 0.0505 |

Reducing exact rerank from 256 to 64 saves 0.0334 ms/query in this warm
in-memory setting. That saving is smaller than the existing binary ADC stage
and only a small fraction of the MIH-through-Hamming path. The result does not
support prioritizing a compact residual scorer solely to reduce exact rerank
work in the current RAM-resident cascade.

## Interpretation and limits

This is a directional native cost measurement, not a storage-latency or
production SLO claim. Cold vector fetches, cache pressure at a larger corpus,
and a different vector backend could change the economic decision. The quality
result in #127 still leaves a residual-scorer opportunity at tight K2=64/128,
but this measurement makes true variable-width MIH the next main research
line. A residual branch should be reopened only if a future storage benchmark
shows that exact-vector retrieval dominates end-to-end latency.
