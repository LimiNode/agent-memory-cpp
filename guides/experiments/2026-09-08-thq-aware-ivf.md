# THQ-aware IVF comparison

Date: 2026-09-08.

## Question

THQ has excellent document ranking but weak local Hamming topology.  This
replay tests whether a data-adaptive partition can recover the advantage of
IVF without assuming that relevant documents form a Hamming sphere around the
query:

```text
E5-IVF or THQ-native IVF -> selected postings -> local THQ4 Hamming@256
                         -> exact FP32 top-10
```

## Setup

The frozen 1M-document, 152-query E5 materialization and qrels were reused.
THQ4 is the existing three-bit thermometer code (144 bytes/document).  Both
variants used `nlist=4096`, 100,000 document vectors for 15-iteration CPU
k-means training, deterministic stable list ordering, and candidate targets
of 20k/50k/100k documents.  The E5 variant clusters normalized FP32 vectors
with inner-product assignment.  The THQ-native variant decodes thermometer
levels and clusters the 384-dimensional ordinal level vectors with L2
assignment.  In both cases runtime list ranking is followed by local THQ4
Hamming and exact FP32 reranking.

## Results

| route | target | mean candidates | mean survival | mean nDCG@10 | p95 Python query ms |
|---|---:|---:|---:|---:|---:|
| E5-IVF → THQ4 | 20k | 20.3k | .836 | .640 | 24.2 |
| E5-IVF → THQ4 | 50k | 50.3k | .914 | .646 | 52.6 |
| E5-IVF → THQ4 | 100k | 100.3k | .957 | .650 | 102.0 |
| THQ-native-IVF → THQ4 | 20k | 20.3k | .803 | .592 | 26.8 |
| THQ-native-IVF → THQ4 | 50k | 50.3k | .899 | .645 | 54.8 |
| THQ-native-IVF → THQ4 | 100k | 100.3k | .946 | .650 | 104.9 |

The flat THQ4 reference on the same materialization reports mean nDCG
`.6540`, 144 MB sequential THQ payload read, and native p95 total around
39 ms at K=256.  The IVF numbers above are Python directional timings (they
include list materialization and NumPy scoring, not a fused native kernel),
so they are not a native serving claim.

## Interpretation

Data-adaptive partitioning works as expected: E5-IVF is materially better than
THQ-native IVF at the 20k budget and approaches the flat THQ quality curve as
the pool grows.  The THQ-native partition is not a useful replacement for E5
coarse geometry at small pools; its local ranking score is strong, but its
ordinal clustering does not preserve the semantic modes well enough.  At
100k candidates both routes are close to the flat nDCG ceiling while losing
most of the intended bandwidth advantage.  This supports `E5-IVF → local
THQ` as a balanced candidate for native implementation, while keeping
THQ-native IVF as an archival/research control rather than a product default.

## Limitations and follow-up

The partition was trained once on a document prefix and the replay used one
frozen 1M corpus.  Native fused list traversal, MDBX page behavior, cold
latency, and update cost remain to be measured.  The next comparison should
place this route beside the improved prototype-IVF/K8/K32/R0 cascade and flat
THQ in one native replay, with the same candidate budgets and final INT10/12
options.

Runner: `tools/agent-memory-bench/run-thq-ivf-comparison.py`.
