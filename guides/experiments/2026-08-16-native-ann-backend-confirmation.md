# Frozen Native ANN Backend Confirmation

Date: 2026-08-16
PR context: #150 draft, `agent/ann-backend-confirmation`
Contract: `tools/agent-memory-bench/native-ann-confirmation-scale.example.json`

## Question

Do the three backend representatives selected only on the disjoint RU
calibration set transfer to a previously unused German MIRACL corpus as the
document scale grows from 25k to 1M? This is a confirmation comparison, not a
fresh backend-selection or per-scale tuning exercise.

## Frozen Setup

The contract binds corrected calibration selection
`7022e5927f26c0c3ef36e31edc5ca9dab1722de0`, selection SHA-256
`368d3dce5c8fc566c234c033f9f581e4df24800f65ee495238d55ae075c6d083`,
and calibration evidence ZIP SHA-256
`e7dcb9f0c56e10f5794f24a1f630b5fb5a699bbcc498e76040d9a43abd8be220`.

The fresh root uses pinned `miracl/miracl-corpus` and `miracl/miracl` revisions,
German development qrels, a stable-hash seed of 20260822, 25,000 training
documents, and 305 evaluation queries. Every scale proved that its document and
query identifiers were disjoint from the calibration root before comparison.

All treatments use multilingual-e5-small float vectors, ITQ-256 seed 52 with
50 iterations, Hamming top-768, binary ADC top-256 and exact E5 rerank top-256.
The frozen backends are BinaryFlat-256, fixed-r56 MIH `m19` (nine 14-bit and ten
13-bit bands, local radius two), and binary HNSW `M=16`/`efConstruction=200`/
`efSearch=768`/seed 20260815. No representation, band, HNSW, cascade or
per-scale parameter was selected from the fresh corpus.

Native timing uses one warmup and seven repeats over the same 305 queries on a
warm immutable in-memory backend. Candidate-generator and cascade totals are
separate measurements; query encoding, index build, cold I/O and the exhaustive
conformance scan are excluded. The raw roots are
`tmp/native-ann-confirmation-v1/de-{25k,100k,1m}/comparison`.

## Results

`Hamming coverage` is exact E5 top-10 coverage after Hamming top-768;
`ADC survival` is E5-oracle survival after the production ADC top-256 shortlist;
and nDCG is the final exact-E5 rerank result. These are means over the fresh
queries, not bootstrap selection gates.

| Scale | Backend | Hamming coverage | ADC survival | reranked nDCG@10 | full E5 nDCG@10 | candidate p50 ms | cascade p50 ms | backend bytes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 25k | MIH m19 | 0.9731 | 0.9731 | 0.7467 | 0.7488 | 0.5192 | 0.7209 | 2,921,884 |
| 25k | Flat | 0.9934 | 0.9934 | 0.7490 | 0.7488 | 0.4377 | 0.6444 | 0 |
| 25k | HNSW | 0.9934 | 0.9934 | 0.7490 | 0.7488 | 0.7765 | 0.9815 | 4,516,240 |
| 100k | MIH m19 | 0.9616 | 0.9607 | 0.7317 | 0.7356 | 1.4558 | 1.7063 | 9,080,444 |
| 100k | Flat | 0.9761 | 0.9744 | 0.7341 | 0.7356 | 1.7534 | 2.0040 | 0 |
| 100k | HNSW | 0.9725 | 0.9708 | 0.7349 | 0.7356 | 1.1737 | 1.4107 | 18,060,320 |
| 1M | MIH m19 | 0.9223 | 0.9197 | 0.6686 | 0.6724 | 20.6163 | 20.9219 | 77,798,748 |
| 1M | Flat | 0.9269 | 0.9239 | 0.6662 | 0.6724 | 16.1561 | 16.5198 | 0 |
| 1M | HNSW | 0.9066 | 0.9036 | 0.6666 | 0.6724 | 1.8370 | 2.0711 | 180,522,708 |

## Interpretation

The selected graph backend becomes the latency point at 100k and especially at
1M: its 1M candidate-generator p50 is 1.8370 ms, versus 16.1561 ms for Flat and
20.6163 ms for the frozen fixed-r56 MIH. That speed uses materially more
backend-specific memory and has the lowest Hamming/ADC survival at 1M. Flat
retains the best fresh Hamming/ADC survival at 1M and adds no backend-specific
index bytes, but its full scan is far slower at that scale. The frozen MIH arm
does not beat Flat on this fresh 1M latency/quality pair and adds about 74.2 MiB
of backend-specific logical storage.

This is evidence about these three frozen configurations on this corpus and
hardware, not a general declaration that one ANN family wins. It establishes
that the frontier among the three frozen calibration-selected representatives
changes with scale, and that a production choice needs an explicit latency,
memory and quality budget rather than a single corpus-size rule.

It does **not** estimate a scale-optimal MIH frontier. The MIH substring count
and exact fixed-r56 radius schedule were intentionally frozen while corpus size
grew 40x. In particular, `m19` retains nine 14-bit and ten 13-bit bands at all
three scales. A fixed-width sparse directory naturally accumulates larger
postings as `N` grows, so this confirmation should be read as a failed transfer
of the RU-calibration-selected `m19/r56` configuration, not as a conclusion
that the MIH family intrinsically loses to Flat or HNSW at 1M. Its failure to
beat Flat already at the fresh 25k scale is separately useful evidence that
this configuration is not corpus-geometry-robust even near its original scale.

The standard MIH starting heuristic is substring width `s ≈ log2(N)`, or
equivalently `m ≈ 256 / log2(N)`. It suggests approximately `m=17–18` at 25k,
`m=15` at 100k, and `m=13` at 1M. This is a uniform-code/equal-cost starting
point, not a production selection rule: code geometry, radius schedule, bit
assignment, directory implementation and hardware costs still require
calibration-only measurement.

## Post-hoc Diagnostics

The evidence package adds descriptive paired bootstrap intervals over the same
305 frozen fresh queries. They are effect-size diagnostics, not a new selection
gate. Each delta below is `right - MIH`; the intervals are percentile 95% CIs
from 10,000 paired resamples.

| Scale | Right backend | Δ ADC survival [95% CI] | Δ nDCG@10 [95% CI] |
| --- | --- | ---: | ---: |
| 25k | Flat | +0.0203 [+0.0148, +0.0262] | +0.0023 [-0.0009, +0.0060] |
| 25k | HNSW | +0.0203 [+0.0148, +0.0262] | +0.0023 [-0.0009, +0.0060] |
| 100k | Flat | +0.0138 [+0.0089, +0.0193] | +0.0024 [-0.0005, +0.0057] |
| 100k | HNSW | +0.0102 [+0.0043, +0.0167] | +0.0032 [-0.0002, +0.0071] |
| 1M | Flat | +0.0043 [+0.0010, +0.0075] | -0.0024 [-0.0064, +0.0008] |
| 1M | HNSW | -0.0161 [-0.0269, -0.0056] | -0.0020 [-0.0117, +0.0059] |

The intervals support ADC-survival differences but do not support an nDCG rank
among the three backends at any scale. The raw fixed-MIH work diagnostics make
the scale mismatch concrete: mean unique candidates remain about 34% of the
corpus (8,605 / 25k; 33,977 / 100k; 338,820 / 1M), while mean postings touched
grow from 10,712 to 42,062 to 418,819 per query. The 1M candidate-generator
time is consequently dominated by full Hamming scoring (p50 13.4310 ms), with
generation deduplication (3.4808 ms) and top-K selection (2.9259 ms) also
material. This is evidence about the frozen `m19` work profile, not a measured
profile of a scale-adaptive MIH configuration.

## Limits And Follow-up

- The fresh evaluation has 305 queries and one host; p50/p95/p99 timing is a
  repeated warm-local measurement, not a portability claim.
- The comparison deliberately does not retune MIH bands/radii, HNSW search
  breadth, code width or cascade limits on fresh results.
- Candidate-generator, cascade and per-stage values must not be summed to
  construct a replacement latency.
- Evidence finalization independently replays the exhaustive E5 oracle from the
  stored vectors and rejects any cache whose exact top positions or full-E5
  nDCG values differ. It also packages the exact preparer, materializer and
  requirements-lock source snapshots named by each scale's manifests; the
  25k/100k materializer is therefore intentionally a historical snapshot,
  rather than an unverified copy of the later 1M producer.
- The post-hoc bootstrap intervals describe only these 305 queries and do not
  license new ranking or configuration selection.
- The next experiment requires a new calibration-only corpus/split with a
  separate untouched corpus/language for confirmation. Give MIH a per-scale
  grid (initially 25k `m=16…20`, 100k `m=13…18`, 1M `m=10…15`, retaining exact
  r56 before adding radius variation) and HNSW an equal per-scale calibration
  budget for `M` and `efSearch`.
- At the same time, compare the present sorted-`lower_bound` directory with a
  flat/open-address directory, especially around `m≈13`, and measure streaming
  generation deduplication in place of the current visited-list then dedup pass.
  Freeze the selected scale-specific representatives before one new untouched
  confirmation. Do not add a coarse locator before this full ITQ-256 MIH
  baseline is resolved.
