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

## 2026-08-13 — held-out result

### Actual result

The full predeclared matrix completed: five ordinary ITQ controls and four
document-only training treatments for each of ITQ seeds 52--56. The output has
25 reports, 25 per-query contribution files, 20 same-seed paired bootstraps
(10,000 replicates each), and a matrix manifest that binds every report,
contribution, and learned artifact by SHA-256.

All learned treatments failed the minimum frontier gate. The training-path
control itself moved the system in the wrong direction, and the MIH-work
weights made only a tiny additional change relative to that control:

| Treatment, delta from same-seed ITQ | Mean candidates/query | Mean posting visits/query | Raw E5-oracle survival | Final ADC survival |
| --- | ---: | ---: | ---: | ---: |
| training path, work weight 0.00 | +5,181.56 | +37,294.95 | +0.001310 | -0.006486 |
| MIH-work 0.02 | +5,180.42 | +37,275.72 | +0.001310 | -0.006629 |
| MIH-work 0.05 | +5,177.87 | +37,230.74 | +0.001310 | -0.006502 |
| MIH-work 0.10 | +5,173.36 | +37,145.84 | +0.001310 | -0.006629 |

The ordinary ITQ controls average about 16,139 candidates/query. The learned
rows instead average about 21,316--21,321 candidates/query, well above the
predeclared `<=12,000` minimum-interesting threshold. Every seed's paired
candidate-union bootstrap interval is strictly positive for every learned
treatment. The observed tiny raw-union survival gain does not rescue the
result: it is purchased with substantially more work, then loses roughly
0.0065 final E5-oracle survival after Hamming and ADC.

The zero-work training-path control is crucial here. It shows that the failure
is not evidence that the small MIH-work coefficient alone found a bad optimum:
the first document-pair semantic/quantization refinement objective already
degrades the local-radius-one code geometry relative to ordinary ITQ. Raising
the MIH-work weight from zero to 0.10 only recovers about eight candidates/query
of a roughly 5,180-candidate regression, while its calibrated soft collision
surrogate changes only in the sixth decimal place.

### Interpretation

This first MIH-aware ITQ objective is a **no-go** under the declared gate. It
does not establish that MIH-aware code learning is impossible; it establishes
that this document-pair soft radius-one collision surrogate, combined with the
current semantic proxy and checkpoint rule, does not preserve the ITQ geometry
that makes the existing `32 x 8` local-radius-one cascade viable.

The next representation-learning proposal must be a new predeclared algorithm,
not a held-out retuning of `0.02/0.05/0.10`. In particular, it should explicitly
anchor to the initial ITQ projection and optimize a closer proxy for local
Hamming-neighbour preservation/candidate union, with document-only calibration
diagnostics proving any work reduction before another held-out frontier run.

### Evidence

The validated archive is `mih-aware-itq-frontier-evidence-v1.zip`: SHA-256
`4f7700154d17faf48da954b7415f4ff120aab6707709cd0f63c6c5f49e8c6ef9`,
internal bundle-root SHA-256
`69a3dc3afbb4f019f0e87aedbf6d93a0766cd939e3408f2dc9988520dc3a74a5`.
It is staged for the draft evidence release and is not public until the PR is
reviewed and the release is published.
