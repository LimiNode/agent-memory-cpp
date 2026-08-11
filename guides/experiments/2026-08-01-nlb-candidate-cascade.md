# NLB candidate cascade

## 2026-08-01: Hamming -> asymmetric LUT -> exact rerank

### Question

The current 128-bit median-calibrated NLB artifact preserves substantially more
E5 neighbourhood information under continuous-query asymmetric scoring than
under symmetric Hamming distance. This experiment asks whether Hamming can remain
the cheap full-corpus stage while asymmetric scoring is applied only to a bounded
shortlist before exact float reranking.

The expected result was a useful latency/quality frontier rather than uniform
improvement: larger Hamming K1 should raise the ceiling available to asymmetric
K2, while larger K2 should recover quality at the cost of more LUT lookups and
float reranking.

### Setup

- draft PR context: `#103`;
- held-out MIRACL RU materialization: 22,607 documents, 1,252 queries, and
  13,100 qrels;
- every evaluation query has at least one positive judgment in the materialized
  corpus;
- document/query embeddings: frozen normalized multilingual E5 float32 vectors;
- artifact: `nlb_median_threshold_v1`, 128 bits, trained and median-calibrated
  only from its document training split;
- artifact SHA-256:
  `3e8c5194c74164163a7ea2dfd47d24af3b174ccd4abcd9ba91594fe94fe9516d`;
- materialization manifest SHA-256:
  `cd1987fdef63f5f6b4fd595d312648ea58f85aa502ed982958ebf02e99290e86`;
- evaluator source manifest SHA-256:
  `5bff9f215c08c305751ee06d92500cf9edc8528a9ce8bc44088d12ca659b4705`;
- build environment SHA-256:
  `8619fad1466eeed5b815e055bdb7b5556ace53b9accc06b088251d1035b19041`;
- local build: Windows AMD64, GNU 15.2.0, C++17 without extensions, Release,
  MinGW Makefiles;
- Hamming backend: runtime-selected hardware POPCNT;
- asymmetric backend: one query-specific 256-entry lookup table per packed
  byte;
- exact reference/rerank scorer: scalar cosine with stable document-ID ties;
- one untimed warm-up and one timed execution per query.

The document codes are materialized once. Hamming scans a contiguous row-major
packed-code buffer through one batch-dispatched kernel and uses `partial_sort`
only through K1. The asymmetric stage also uses `partial_sort` only through K2.
The final stage scores those K2 documents with the same float cosine semantics
as the oracle. Full-corpus oracle construction and qrels accounting are outside
the candidate-pipeline timers.

Representative invocation:

```text
agent-memory-autoencoder-cascade-eval \
  tmp/miracl-ru-25k-current-e5 \
  tmp/miracl-ru-25k-nlb-median-128-methodology/artifact.json \
  tmp/pr103-cascade-k1024-k256.json \
  1024 256 1 1
```

The sixteen raw JSON reports remain under `tmp/pr103-cascade-k*-k*.json` and
are not committed.

### Results

Full-corpus E5 reaches nDCG@10 `0.80145`. `Dense cov.` is the fraction of its
top 10 retained by the candidate stage. `Qrels cov.` is the macro mean fraction
of all positively judged documents retained for each query. The total time is
the measured projection + Hamming K1 + LUT construction + asymmetric K2 +
exact-rerank pipeline.

| K1 | K2 | Hamming dense cov. | Asym. dense cov. | Hamming qrels cov. | Asym. qrels cov. | Rerank nDCG@10 | E5 retention | Total ms/query |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 512 | 64 | 0.8886 | 0.8199 | 0.9339 | 0.9043 | 0.7745 | 96.64% | 0.701 |
| 512 | 128 | 0.8886 | 0.8614 | 0.9339 | 0.9214 | 0.7798 | 97.30% | 0.733 |
| 512 | 256 | 0.8886 | 0.8824 | 0.9339 | 0.9314 | 0.7822 | 97.60% | 0.774 |
| 512 | 512 | 0.8886 | 0.8886 | 0.9339 | 0.9339 | 0.7826 | 97.65% | 0.856 |
| 1,024 | 64 | 0.9351 | 0.8302 | 0.9629 | 0.9128 | 0.7795 | 97.27% | 1.049 |
| 1,024 | 128 | 0.9351 | 0.8799 | 0.9629 | 0.9381 | 0.7887 | 98.41% | 1.078 |
| 1,024 | 256 | 0.9351 | 0.9133 | 0.9629 | 0.9552 | 0.7924 | 98.87% | 1.152 |
| 1,024 | 512 | 0.9351 | 0.9307 | 0.9629 | 0.9616 | 0.7936 | 99.02% | 1.224 |
| 2,048 | 64 | 0.9670 | 0.8322 | 0.9826 | 0.9146 | 0.7801 | 97.34% | 1.659 |
| 2,048 | 128 | 0.9670 | 0.8855 | 0.9826 | 0.9420 | 0.7904 | 98.62% | 1.657 |
| 2,048 | 256 | 0.9670 | 0.9252 | 0.9826 | 0.9641 | 0.7948 | 99.17% | 1.736 |
| 2,048 | 512 | 0.9670 | 0.9505 | 0.9826 | 0.9751 | 0.7967 | 99.40% | 1.777 |
| 4,096 | 64 | 0.9883 | 0.8327 | 0.9933 | 0.9151 | 0.7804 | 97.37% | 2.856 |
| 4,096 | 128 | 0.9883 | 0.8868 | 0.9933 | 0.9436 | 0.7917 | 98.78% | 2.751 |
| 4,096 | 256 | 0.9883 | 0.9293 | 0.9933 | 0.9667 | 0.7960 | 99.32% | 2.886 |
| 4,096 | 512 | 0.9883 | 0.9577 | 0.9933 | 0.9806 | 0.7982 | 99.60% | 2.921 |

The local directional stage means were:

| K1 | K2 | Projection | Hamming | LUT build | Asymmetric | Float rerank | Total |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 512 | 128 | 0.049 | 0.563 | 0.032 | 0.060 | 0.029 | 0.733 |
| 1,024 | 256 | 0.051 | 0.879 | 0.034 | 0.125 | 0.063 | 1.152 |
| 2,048 | 256 | 0.051 | 1.372 | 0.034 | 0.214 | 0.065 | 1.736 |
| 4,096 | 512 | 0.051 | 2.269 | 0.033 | 0.431 | 0.138 | 2.921 |

### Interpretation

The cascade is viable, but asymmetric scoring cannot recover documents already
discarded by Hamming. At K1=512, using asymmetric K2 below 512 only reduces the
candidate set and therefore loses quality. At K1>=1,024, asymmetric scoring can
discard many Hamming candidates while retaining most of the useful E5 signal.

Two practical directional operating points emerge:

- K1=1,024, K2=256: nDCG@10 `0.7924` (98.87% of full E5) at about
  `1.15 ms/query` for the measured candidate pipeline;
- K1=2,048, K2=512: nDCG@10 `0.7967` (99.40% retention) at about
  `1.78 ms/query`.

Hamming remains the dominant measured stage. Replacing per-document dispatch
and full-corpus sorting with contiguous batch distance calculation and partial
top-K reduced the K1=512 Hamming stage from the preliminary `~3.95 ms` to
`~0.56 ms` without changing any quality value. The discarded preliminary
timings are therefore evidence that hot-path layout and selection policy must be
fixed before comparing index families.

Qrels-positive coverage is consistently higher than dense-oracle top-10
coverage. This explains why the final nDCG degradation is smaller than the
dense-neighbour miss rate alone suggests, and justifies retaining both metrics.

### Limitations and threats to validity

- This is one 128-bit artifact, one language, one training seed, and one held-out
  fixture.
- Timings are one local sequential Release run. They are directional, not a
  stable cross-machine benchmark claim; thermal/cache order effects remain.
- The evaluator retains both object-form signatures and a packed row-major copy
  to exercise the reusable asymmetric scorer. Reported payload bytes describe
  one logical packed code per document, not evaluator process RSS.
- Full E5 oracle latency is deliberately outside the cascade timer and was not
  measured by this harness. The table does not claim a speedup against the
  optimized contiguous exact-vector baseline.
- The Hamming stage still scans the entire in-memory code corpus. No band,
  multi-index hashing, MDBX, or cold-storage I/O is involved.
- The asymmetric score is fixed by the existing artifact; no query or qrels data
  was used to select weights or thresholds.

### Follow-up checks

1. Repeat selected frontier points across NLB seeds and query bootstrap samples.
2. Compare this cascade with the optimized contiguous exact-vector baseline
   under one repeated timing harness.
3. Measure code entropy, correlation, Hamming margins, and band occupancy before
   designing a sub-linear band/MDBX stage.
4. Only after the current artifact is fully characterized, test training
   improvements independently: median-initialized learnable bias, decorrelation,
   document-only neighbour/listwise distillation, and ITQ warm start.
5. Keep quantized/LUT weighted-Hamming and future band layouts separate from
   qrels-driven training changes so each gain has an attributable cause.
