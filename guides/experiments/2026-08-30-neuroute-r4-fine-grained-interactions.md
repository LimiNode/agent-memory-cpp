# NeuRoute R4 fine-grained actual-document interactions

## Context

- Date: 2026-08-30
- PR: stacked on R4 actual-document representative materialization
- Status: full DE-1M measurement and independent model/result replay complete

## Question

Does exact full-384D query interaction with deterministic actual documents
inside each frozen 16-bit address recover the sparse ordering that R0, projected
K8 geometry, and fixed R3 distribution summaries could not recover?

## Frozen protocol

The 16-bit partition, current-K8 exact top-1024 shortlist, 8,141 training
queries, static exact-E5 gain-density ListNet teacher, optimizer family, three
route seeds, and downstream Hamming768 -> ADC64 -> exact-E5 cascade are fixed.
All treatments are evaluated at strict `.003`, `.004`, and `.005` unique
candidate fractions.

The matched ladder contains R0 plus exact query cosine against deterministic
actual-document K8, K16, and K32 prefixes. K32 is also evaluated with maximum,
top-two mean, smooth log-mean-exp, and learned sorted-top-eight pooling. All
actual-document interactions use authoritative 384D vectors without a random
projection. Teacher labels do not participate in representative selection.
The 21 models have 21,737--21,837 trainable parameters, a maximum/minimum ratio
below `1.005`.

All models are frozen before the 76-query configuration split is opened. That
split selects one architecture for the later teacher-trained representative
study; the 76-query internal split remains sealed until after selection.

## Results

Configuration selected `actual_k32_learned_top8`. Three-seed internal means at
the strict `.005` candidate-fraction frontier are:

| Treatment | Candidate fraction | Actionable gain | Exact nDCG@10 |
|---|---:|---:|---:|
| Prototype order | .004986 | .8114 | .6156 |
| R0 scalar | .004983 | .8329 | .6281 |
| Actual K8, learned top 8 | .004985 | .8502 | .6292 |
| Actual K16, learned top 8 | .004979 | .8818 | .6414 |
| Actual K32, max | .004979 | .9007 | .6507 |
| Actual K32, top-two mean | .004980 | .8943 | .6481 |
| Actual K32, log-mean-exp | .004980 | .8894 | .6501 |
| Actual K32, learned top 8 | .004979 | .9003 | .6501 |
| Privileged gain density | .004992 | .9133 | .6492 |

The actual-document ladder is strongly monotonic from K8 through K32. K32
raises mean actionable gain by about `.0674` over R0 at the same candidate
mass, and both simple maximum pooling and the configuration-selected learned
pooling reach about `.90`. This is direct evidence that rare full-resolution
documents hidden behind an address contain ordering information that R0 and
fixed R3 summaries lose.

The preregistered every-seed gate does not pass. For selected K32 learned
pooling, actionable gain is `.9018`, `.8954`, and `.9036`; seed 2 is below the
`.90` direct threshold and exact nDCG is `.0033` below frozen R3c. The selected
variant closes `.795`, `.633`, and `.810` of the R3c-to-privileged actionable
gap, but the progress gate also requires no nDCG regression on every seed.
This is a strong positive representation result, not yet a stable production
selection result.

R0 reproduces the frozen candidate frontier from the parent study exactly on
all three seeds. Result SHA-256 is
`697079a7e9dcdfa97ad0686c67bf438dab6ba58a182d0b28cac94118b65913a6`.
An independent replay rebuilt all interaction caches and all 21 model archives,
then reproduced the canonical result and every model archive byte for byte.
Evidence SHA-256 is
`6d69401beb50b9e74d071e6326d292da414fa64192b53fb26f7e4bae1a727ee9`.

## Interpretation

Full-resolution actual documents remove most of the remaining representation
bottleneck. The similarity of K32 maximum and learned pooling also suggests
that representative coverage is now at least as important as additional
pooling capacity. The next causal test should therefore keep the selected K32
interaction architecture fixed and vary only offline representative selection.

## Limitations

- Deterministic centroid-nearest plus farthest-first selection is teacher-blind
  and is not optimized for the training query distribution.
- Configuration selected learned pooling, while simple maximum has a slightly
  higher internal mean. Internal measurements cannot be used to revise that
  selection post hoc.
- Exact interaction construction is an offline research operation and carries
  no native latency or storage-layout claim.
- The study is frozen to DE-1M and does not license production selection or
  native confirmation.

## Next check

Using training queries only, accumulate teacher support for actual documents
inside positive cached addresses and materialize query-independent K32
representatives. Fill unsupported slots deterministically from the R4 baseline.
Freeze representative bytes before configuration/internal evaluation, retain
the selected `actual_k32_learned_top8` scorer architecture, and compare against
the deterministic K32, frozen R3c, and privileged frontiers at the same strict
candidate fractions.
