# Random-projection THQ validation

Date: 2026-09-09. This is the follow-up gate for the Gaussian direction
result from the landmark-affinity study.

## Protocol

The frozen DE-1M fixture (1,000,000 normalized E5 documents, 152 queries,
exact E5 top-10 teacher IDs) was evaluated with per-coordinate quantile
thermometer codes. `THQ-L` means L levels and L-1 bits per projected
coordinate. All rows are exhaustive locality controls; Python timings are not
native serving claims.

The corrected runner now supports `gaussian` dense random directions and a
`orthogonal` 384x384 QR control:
`tools/agent-memory-bench/evaluate-landmark-affinity-thq.py`.

## Seed stability: Gaussian RP-THQ4-M256

Five independent projection seeds (20260908--20260912) produced:

| budget | mean survival | seed sd | min--max |
|---:|---:|---:|---:|
| 256 | .9355 | .0042 | .9309--.9401 |
| 1,000 | .9782 | .0038 | .9743--.9822 |
| 5,000 | .9967 | .0010 | .9954--.9980 |
| 10,000 | .9986 | .0009 | .9974--.9993 |

The result is stable and not attributable to one unusually favourable random
matrix. THQ3 and THQ5 have two complete seeds in this batch; their earlier
single-seed values remain diagnostic until the same replication is completed.

## Matched-byte frontier (seed 20260908)

| representation | bits | bytes/doc | @256 | @1k | @5k | @10k |
|---|---:|---:|---:|---:|---:|---:|
| random-hyperplane sign 512 | 512 | 64 | .750 | .864 | .946 | .968 |
| Gaussian RP-THQ3 M256 | 512 | 64 | .910 | .964 | .991 | .993 |
| Gaussian RP-THQ4 M170 | 510 | 64 | .874 | .938 | .989 | .995 |
| Gaussian RP-THQ5 M128 | 512 | 64 | .791 | .893 | .958 | .970 |
| Gaussian RP-THQ4 M256 | 768 | 96 | .939 | .976 | .997 | .997 |
| Gaussian RP-THQ5 M192 | 768 | 96 | .909 | .966 | .996 | .999 |

At 64 B/doc, retaining more projected coordinates (THQ3-M256) is better than
spending the same bits on more levels at fewer coordinates. At 96 B/doc,
THQ4-M256 is the strongest tested reduced-dimensional profile.

## Full-dimensional orientation control (superseded interpretation)

The original orthogonal replay reached `.9993 @256` and `1.000 @1k/@5k/@10k`;
the later matched-layout control showed that this was not an orientation-only
effect. On the same full-dimensional 384-coordinate layout, raw THQ4 is
`.999342 @256`, random orthogonal and randomized Hadamard are `.998026`, ITQ is
`.996711`, and PCA is `.984868`. The corrected conclusion is that full-
dimensional raw THQ4 already preserves this fixture's E5 locality; arbitrary
rotation is not required. These are exhaustive representation controls, not
physical-index results.

## Decision and next gate

The canonical name for the new family is `RP-THQ-L-M` (for example,
`Gaussian-RP-THQ4-256`). `RTHQ` is reserved for a true orthogonal rotation,
not a 384->256 projection. Gaussian RP-THQ is now a validated locality
candidate, not yet a product index: native exhaustive throughput and a
coordinate-aware ordinal index are still required. PCA/ITQ rotation controls
remain a separate follow-up because their training objectives differ from
retrieval locality.

Raw reports are intentionally outside Git:

* `tmp/rp-thq-seeds/result-m256-seed20260908.json` (and seeds 09--12);
* `tmp/rp-thq-matched-m128-l5.json`;
* `tmp/rp-thq-matched-m170-l4.json`;
* `tmp/rp-thq-matched-m192-l5.json`;
* `tmp/rp-thq-orthogonal-m384.json`.
