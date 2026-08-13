# MIH-aware ITQ with a held-out frontier gate

## 2026-08-13 — pre-execution contract

### Question

Can document-only MIH-aware code learning reduce the candidate work of the
current ITQ-256, `32 x 8`, local-radius-one cascade while retaining its
held-out E5-oracle top-10 survival?

This is deliberately a representation-learning experiment. It does not retry
static width profiles or frozen-bit permutations, which #131 already found to
have no stable held-out headroom.

### Frozen protocol

The trainer reads only the frozen 25,000-vector calibration train
materialization. It makes a stable hash-selected document-only training and
validation split. It cannot accept an evaluation root, query vectors, qrels, or
held-out metrics. Checkpoint selection is the minimum document-only validation
loss; no held-out result can select a weight, seed, or epoch.

All code remains 256 bits in the current contiguous `32 x 8` layout. Every
held-out row uses local radius one (288 bucket probes), Hamming `K1=768`, binary
ADC `K2=256`, and the E5 oracle `K=10` over the frozen MIRACL-RU evaluation
corpus (22,607 documents and 1,252 queries).

For ITQ seeds 52--56, the predeclared matrix contains:

| Treatment | MIH-work weight |
| --- | ---: |
| ordinary ITQ control | not trained |
| training-path control | 0.00 |
| MIH-aware | 0.02 |
| MIH-aware | 0.05 |
| MIH-aware | 0.10 |

The MIH-work term is a differentiable, document-pair surrogate for the expected
radius-one `8`-bit-band collision/posting work. It is not asserted to equal the
unique candidate union: direct calibration work diagnostics and the held-out
candidate union remain separate measurements. Semantic pairwise similarity,
hard-code quantization compatibility, orthogonality, and bit-balance terms
prevent a trivial collision-reducing collapse.

The result is a paired held-out frontier, not a single selected treatment:

```text
x = mean unique raw-union candidates / posting visits
y = E5 oracle top-10 raw-union survival
```

Every treatment is compared with its same-seed ordinary ITQ control using
10,000 paired bootstrap replicates. We do not choose a winner from held-out
data.

### Predeclared decision gate

With the current union near 16,000 candidates/query:

| Classification | Mean candidate union | Survival requirement |
| --- | ---: | --- |
| not interesting | above 12,000 | — |
| minimum interesting | 12,000 or fewer | not materially below same-seed ITQ control |
| strong | 8,000 or fewer | same |
| very strong | 4,000--6,000 | same |

The first pass is a strict held-out gate, not permission to tune repeatedly
against the evaluation set. If no predeclared treatment crosses the minimum
gate, this objective is a no-go for the tested regime; any follow-up must add a
new predeclared algorithmic idea rather than silently retune these weights.

### Expected result and limitations

Balanced near-independent eight-bit subcodes naturally have similar expected
bucket loads, so the work surrogate may have little usable freedom once
semantic and code-health constraints are retained. A negative result would be
useful: it would distinguish a representational limitation from the already
closed static-layout branch. The surrogate does not prove optimality over
query-adaptive probing, supervised retrieval learning, other binary encoders,
or a different index family.

Raw reports, per-query contributions, paired bootstraps, source snapshots, and
runtime provenance will be retained in a GitHub Evidence release only after a
fail-closed archive validator succeeds.
