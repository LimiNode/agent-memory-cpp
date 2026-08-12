# 2026-08-12 Native MIH rerank cost decomposition

## Question

Does reducing the exact E5 rerank budget from K2=256 to K2=64 or 128 save
enough native serving work to justify a new compact residual scorer?

## Setup

The native hot-path harness materializes the same 256-bit ITQ codes, 384D E5
float32 vectors, ITQ query projections, and calibration-conditioned binary ADC
centroids from the MIRACL Russian 25k E5 root. It runs 32 equal 8-bit MIH bands
with local radius one, Hamming K1=768, all 1,252 queries, and seven measured
warm passes.

MIH-to-Hamming components are measured in seven full-query passes. For each
K2, the harness runs one unrecorded full-query warm pass followed by seven
separate full-query ADC-to-exact passes over the fixed Hamming K1 shortlist.
Exact E5 rerank consumes the actual ADC-selected K2 positions, rather than the
first K2 Hamming positions. This avoids the old nested `64 -> 128 -> 256`
per-query cache order and makes every reported stage carry seven samples.
Timing excludes query encoding, cold storage I/O, and the full-corpus oracle.

The radius-one control visits a mean 16,119 candidates per query (71.30% of
22,607 documents), so it is a native component-cost control rather than a
replacement for the budgeted-confidence quality contract in #127.

## Result

On the local warm AVX2 run:

| Component | Median ms/query |
| --- | ---: |
| MIH through Hamming K1 selection | 0.5226 |
| binary ADC on Hamming K1, K2=64 | 0.1185 |
| binary ADC on Hamming K1, K2=128 | 0.1249 |
| binary ADC on Hamming K1, K2=256 | 0.1357 |
| exact E5 rerank after ADC, K2=64 | 0.0148 |
| exact E5 rerank after ADC, K2=128 | 0.0285 |
| exact E5 rerank after ADC, K2=256 | 0.0589 |

The paired `K2=256 - K2=64` exact-rerank delta has median `0.0442 ms/query`
(range `0.0366..0.0474` across the seven matched passes). That saving is smaller
than the existing binary ADC stage and only a small fraction of the
MIH-through-Hamming path. The result does not support prioritizing a compact
residual scorer solely to reduce exact rerank work in the current RAM-resident
cascade.

The replayable archive is staged as the draft evidence release
`evidence/mih-rerank-cost-v1`. Its replacement asset contains the actual
full-run config, input-manifest and source/build provenance, all seven
per-stage samples, the paired delta, and portable ZIP member validation. The
archive SHA-256 is `04b376b4f11c2e2fce658e83eb21499a36e0c45606a5a525cdaf7551336930cf`;
its internal bundle-root SHA-256 is
`1c50296751830f4146056f10c5a168d104552771ec92c46b9806ac22511cf7bf`.

## Interpretation and limits

This is a directional native cost measurement, not a storage-latency or
production SLO claim. Cold vector fetches, cache pressure at a larger corpus,
and a different vector backend could change the economic decision. The quality
result in #127 still leaves a residual-scorer opportunity at tight K2=64/128,
but this measurement makes true variable-width MIH the next main research
line. A residual branch should be reopened only if a future storage benchmark
shows that exact-vector retrieval dominates end-to-end latency.
