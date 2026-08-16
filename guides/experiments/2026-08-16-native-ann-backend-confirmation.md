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
hardware, not a general declaration that one ANN family wins. It does establish
that the predeclared backend frontier changes with scale, and that a production
choice needs an explicit latency, memory and quality budget rather than a
single corpus-size rule.

## Limits And Follow-up

- The fresh evaluation has 305 queries and one host; p50/p95/p99 timing is a
  repeated warm-local measurement, not a portability claim.
- The comparison deliberately does not retune MIH bands/radii, HNSW search
  breadth, code width or cascade limits on fresh results.
- Candidate-generator, cascade and per-stage values must not be summed to
  construct a replacement latency.
- Package the three scale roots through the fail-closed evidence validator,
  retain the resulting evidence release, then review the frozen comparison
  before proposing any new backend policy or experiment.
