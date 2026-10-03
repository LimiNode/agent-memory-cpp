# Final Pareto profile screen (2026-10-03)

The canonical serving matrix is now merged in #463. This screen labels three
useful engineering profiles without collapsing quality, latency and footprint
into a weighted score.

| Profile | Representative | Payload | Flat p50 | Prototype-IVF p50 | Modern R4 p50 | Evidence interpretation |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| compact | LSQ32 | 36 B/doc | 178.670 ms | 3.386 ms | 3.479 ms | smallest verified finalist payload |
| balanced | TQ1 | 52 B/doc | 238.600 ms | 2.564 ms | 2.511 ms | compact payload with best historical compromise |
| speed | INT8 | 392 B/doc | 407.166 ms | 0.7485 ms | 0.7927 ms | fastest downstream reranker |

The latency columns are the canonical matched serving receipts from the merged
three-mode study. Fresh quality is kept separate. On the untouched 305-query
bundle, the exact FP32 ceiling is nDCG@10 `0.672395` / MRR `0.693886`;
Prototype-IVF with exact FP32 rerank is nDCG@10 `0.659746` / MRR `0.692147`
at route Recall@128 `0.862807`. The fresh three-seed R4-shaped prototype is
nDCG@10 `0.645591` / MRR `0.686523` at Recall@128 `0.812628`. Neither fresh
row is a packed-codec quality result.

The persistent figures remain a page model, not MDBX measurements. For
example, the model predicts 400,003,072 row-KV bytes for INT8 and 44,003,328
for LSQ32 over one million rows, while segment-blob models are 393,003,008 and
37,003,264 bytes respectively. Real MDBX B-tree overhead, cold/reopen,
recovery and concurrent publication are deliberately not inferred.

These labels are therefore research profiles, not a product selection:

- `compact` minimizes verified payload;
- `balanced` preserves a small payload while improving routed latency;
- `speed` minimizes downstream rerank time at a much larger footprint.

The next product-shaped gate is fresh packed THQ+codec decomposition for the
canonical Prototype-IVF and Modern R4 routes, followed by real finalist MDBX
layout runs.
