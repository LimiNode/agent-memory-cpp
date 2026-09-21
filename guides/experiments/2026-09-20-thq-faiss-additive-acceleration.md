# Faiss additive fitting acceleration spike

Date: 2026-09-20  
Parent: `2026-09-20-thq-additive-upper-bounds`

## Question

Can the additive 4/6/8/32/48-byte capacity diagnostic be made executable by
replacing the pure-NumPy per-stage Lloyd loop with Faiss's native
`ResidualQuantizer`, while preserving one shared 48-stage sequence and exact
prefix arms?

## Protocol

The runner now accepts `--fit-backend numpy|faiss`.  The Faiss path uses
`ResidualQuantizer(d=384, M=48, nbits=8)` and persists the Faiss version,
iterations, and beam width.  Its cumulative stage tables are unpacked into
the same 256-centre-per-stage representation used by the transparent runner;
arms at 4/6/8/32/48 bytes are exact prefixes of this one fit.  The independent
audit checks backend-specific provenance and the shared-prefix invariant.

This is an acceleration/control experiment.  It is not a reproduction of
AVQ, AAQ, QINCo, and it does not license a production choice.

## Current status

The environment provides Faiss `1.15.0` with `ResidualQuantizer` and
`LocalSearchQuantizer`.  A small smoke fit successfully trained and unpacked a
48-stage sequence.  The first 152-query runner attempt was stopped before an
artifact: the full-corpus THQ scan and Python candidate execution are separate
costs from fitting and must not be reported as a fit speedup.  A full replay
still requires a native/chunked scan path and the same independent audit.  Any
bounded subsampled run remains a bounded diagnostic and cannot be promoted to
a full-training upper bound.

Implementation: `tools/agent-memory-bench/run-thq-additive-upper-bounds.py`.


## Full canonical RQ32/RQ48 replay (2026-09-21)

Status: `EXECUTED`, independently decoded and source-replayed.

The follow-up used all 25,000 canonical document-only training vectors. One
Faiss `ResidualQuantizer(d=384, M=48, nbits=8)` was trained with
`Train_default`, eight iterations, fit beam one, encode beam eight and eight
OpenMP threads. The clustering seed is explicit. RQ32 and RQ48 share the first
32 codebook stages, but their candidate-local codes are assigned by separate
prefix quantizers. They are shared-model capacity controls, not a requirement
that an RQ32 code equal the first 32 symbols of an independently optimized
RQ48 code.

Both arms use the same production-shaped quality boundary:

```text
frozen R4 candidates
  -> canonical THQ4 interval-squared top128
  -> THQ centroid + persisted Faiss RQ residual reconstruction
  -> cosine top10
```

No qrels or query vectors enter fitting. Each saved code artifact has shapes
`(152, 128, 32)` and `(152, 128, 48)`; none is a one-million-row
materialization.

### Seed stability

Three predeclared/documented seeds were replayed under the same thread count
and protocol. Seed `20260921` is the canonical arm; the better seed `1234`
was not selected post hoc.

| seed | RQ32 nDCG@10 | RQ32 candidate overlap | RQ48 nDCG@10 | RQ48 candidate overlap | RQ48 - RQ32 nDCG |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1234 | `.659922` | `.882237` | `.648379` | `.892105` | `-.011543` |
| 20260921 | `.656683` | `.885526` | `.651655` | `.902632` | `-.005028` |
| 20260922 | `.651069` | `.880263` | `.651338` | `.901974` | `+.000269` |
| three-seed mean | `.655891` | `.882675` | `.650457` | `.898904` | `-.005434` |

RQ32 spans `.651069-.659922`; RQ48 spans `.648379-.651655`.
The sign of the per-seed quality difference is not invariant. The canonical
seed's paired bootstrap interval for `RQ48 - RQ32` is
`[-.015779, +.005302]`, so that run does not establish a qrels difference.

Across the three runs, RQ48 consistently improves the geometric proxies:
mean residual MSE is `4.05e-5` versus `5.48e-5`, mean cosine-score MAE is
`.003152` versus `.003737`, and candidate/teacher overlaps are higher.
Those proxies do not guarantee better sparse-qrels nDCG at the top-10
boundary. Conversely, the repeatedly studied 152-query shell does not
establish that RQ32 generalizes better.

RQ32 remains the more interesting rate/quality candidate at 128 B total, but
the single-seed `.659922` value is not a stable frontier claim. The
three-seed mean is close to THQ-joint2 and direct INT8 and below the existing
local RSLM4-like result; faithful paper-level RSLM and native latency remain
open. Any future model selection must be fixed before qrels evaluation rather
than choosing the best seed by reported nDCG.

Logical one-million-row accounting is 140,582,912 B for RQ32 and 162,874,368
B for RQ48, including 12,582,912 B and 18,874,368 B global codebooks. Shared
THQ thresholds and centroids are excluded. These are extrapolations from
candidate-local codes, not physical materializations.

The three committed audits named
`2026-09-21-thq-faiss-rq-seed*.audit.json` bind each raw result, source,
model and code archive. They independently rebuild THQ centroids and top-128
sets, decode every persisted side code as an explicit sum of codebook vectors
without `faiss.decode`, and recompute all 304 top-10 rows and metrics. They
record `faiss_assignment_replay: false`: Faiss training and beam assignment
are hash-bound evidence, not an independently implemented assignment oracle.

External evidence hashes are retained in the audit files. The canonical
seed-`20260921` hashes are:

- raw result: `a5aa6a6b8cdb587d17d3176dd543107104a79faf2652757752f25e42d5a8a372`;
- model archive: `bbea47623c4bba63e26609332947d6e250419f2de74cc5690db35cd6803664c3`;
- candidate-code archive: `28ef76ea9e499a2d0fe7dfe50c55c1e80d6638a241fc34d71345c2e090142ad6`.

The next decision gate is faithful RSLM reproduction. After that, canonical
RQ32, faithful RSLM, THQ-joint2 and INT8 can enter the same native end-to-end
cascade comparison.
