# Fresh routed quality and persistence follow-up (2026-10-03)

## Question

Can the frozen `256/32/5000` Prototype-IVF route be replayed against the
untouched 305-query bundle, and can the persistent layout comparison be made
source-bound without promoting a prototype into a product result?

## Inputs and provenance

The fresh bundle is `native-ann-confirmation-v1/de-1m/e5` outside Git.  The
document, query, qrels and training-vector hashes are respectively
`d4f67ebe91faa159eaaeb7884281ad0d0057c27cdb67c4007f260c6442636007`,
`fa6c467e01bbe8a8e725d75fd5ac84c90008235be921c4a4d360d756d0d0d7b2`,
`5b3d22a491558ae0955a7a968c4fec9974ddc463cb5f83fc67334cbfc87ab071` and
`1f581860cff679989f0661fb27623c650bc130e8ed4593b0abe08e5474c00b80`.

The route uses spherical k-means with `nlist=256`, `nprobe=32`, 25,000
training rows, seed `20261002`, score-descending/numeric-ID tie breaking and a
5,000-document downstream budget.  The candidate stream is external raw
evidence; only compact receipts are committed.

## Fresh exact and Prototype-IVF results

| stage | mean nDCG@10 | mean MRR | p05 nDCG@10 | route recall@128 |
| --- | ---: | ---: | ---: | ---: |
| exact FP32 oracle | 0.6723946081 | 0.6938856135 | 0.000000 | 1.000000 |
| Prototype-IVF candidates, exact FP32 rerank | 0.6597463669 | recorded in receipt | 0.000000 | 0.862807 |

The route contains 5,000 unique IDs per query (305/305 rows).  The raw
candidate stream SHA is `abbfb94b5aa9d20d2e7ef82b1322e7d447102f9bc8492f51d660af7229163cf6`.
The candidate result is a real fresh route-quality measurement, but it is not
packed-codec evidence: the final rerank in this fresh pass is the independent
FP32 oracle scorer.

## Modern R4 status

A fresh three-seed IVF fusion was regenerated for all 305 queries as an
explicit prototype route (`nlist=256`, `nprobe=8`, 5,000-ID fusion budget).
With exact FP32 rerank it reports mean route Recall@128 `0.812628`, mean
nDCG@10 `0.645591` and mean MRR `0.686523`.  This is useful fresh route
diagnostic evidence, but it is **not** the historical NeuRoute Modern-R4 model:
the canonical R4 layout/order/model bundle and packed THQ scorer are still
absent, so the receipt remains `PROTOTYPE_EXECUTED_CANONICAL_PENDING`.
The historical 152-query fused stream is not reused.

## Persistent finalist layouts

`tmp/fresh-routed/finalist-layouts.result.json` records a deterministic page
model for INT8, LSQ32/48, TQ1, TQ1+PQ8, PLSQ8x6x8 and RSLM1.  It separates
logical payload bytes from row-KV and 4,096-row segment page occupancy.  RSLM1
has no verified full-corpus packed source in the available workspaces and is
marked `EXTERNAL_NOT_FOUND`; no synthetic payload is substituted.  These are
layout diagnostics only: no MDBX latency, cold-cache, recovery or concurrent
publication claim is made.

## Historical INT8 discrepancy

The old `106.384 ms` direct control is the 2026-09-30 Release `/O2 /arch:AVX2`
register-accumulating float-query AVX2 kernel with bounded top-10, two warmups
and ten repetitions on the 152-query historical fixture.  Its timing scope is
the normalized in-memory direct control and it reports `score + top-k`; it is
not the current exact-cosine full-flat contract.  The current `407.166 ms`
run is a one-warmup/five-repeat packed exact reconstructed-cosine scan with a
392-byte logical row (codes plus inverse norm) and the same ordered-top10
oracle contract.  The current A/B receipt shows exact-cosine dense and fused
paths agree 760/760, while the old scaled-dot control agrees only 435/760.
Therefore the historical gap is a scope/implementation comparison, not a
correctness failure; the old 106 ms number must not be used as the canonical
cosine latency without a matched replay.

## Follow-up

Regenerate the canonical fresh Modern-R4 route from its source-bound model and
THQ inputs, then rerun packed final scorers and quality decomposition.  Only
after that gate should finalist-specific MDBX be promoted beyond this physical
layout prototype.
