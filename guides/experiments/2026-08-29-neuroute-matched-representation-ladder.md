# NeuRoute matched R0/R1/R2 representation ladder

## Context

- Date: 2026-08-29
- PR: stacked on the R0 ambiguity diagnostic
- Status: full DE-1M measurement and independent model/result replay complete

## Question

Does exposing projected raw K8 prototype geometry to the scorer recover the
privileged sparse address ordering that remains unavailable from the current
22 scalar query-address features?

## Frozen protocol

The comparison keeps the DE-1M 16-bit partition, K8 prototype construction,
exact top-1024 shortlist, all 8,141 pseudo-supervised training queries, static
exact-E5 gain-density teacher, optimizer, four epochs, three seeds, address
budgets 128/256/512, and Hamming768 -> ADC64 -> exact-E5 cascade unchanged.
All nine models are serialized before configuration qrels are opened. Internal
qrels remain closed until configuration replay completes.

The three representations are:

- `R0`: the existing 22 scalar K8 cosine/cost/rank features;
- `R1`: all K8 raw 384D prototypes pass through a shared teacher-blind 64D
  orthonormal projection, followed by permutation-invariant mean/max/std
  pooling and the same logistical features;
- `R2`: the same projected K8 values, plus learned diagonal query-conditioned
  attention before pooling.

Every variant retains the full 384D query path. Query-wise mean/max context and
ListNet supervision are shared. Model-specific final hidden widths keep the
trainable parameter counts tightly matched:

| Variant | Trainable parameters |
|---|---:|
| R0 scalar | 21,805 |
| R1 invariant raw K8 | 21,833 |
| R2 query-gated raw K8 | 21,897 |

The maximum/minimum ratio is `1.0042`. The common 384x64 teacher-blind
projection is frozen and excluded from every variant's trainable budget.

## Decision rule

At budget 256, a direct pass requires every seed to reach actionable gain at
least `.90` and candidate fraction at most `.005`. The alternative progress
gate requires every seed to close at least half of the prototype-to-privileged
gap without exceeding `1.05x` prototype candidate mass. No result in this PR
licenses native or production activation.

## Results

Three-seed internal means at the headline budget are:

| Treatment | Candidate fraction | Actionable gain | nDCG@10 |
|---|---:|---:|---:|
| Prototype order | .005415 | .8213 | .6219 |
| R0 scalar | .005115 | .8368 | .6309 |
| R1 invariant raw K8 | .004556 | .8088 | .6120 |
| R2 query-gated raw K8 | .004558 | .8114 | .6101 |
| Privileged teacher | .002263 | .9197 | .6494 |

R0 reproduces the earlier learned range and improves over prototype order, but
still closes only `.08-.21` of the teacher gap per seed. R1 and R2 buy about
16% less candidate mass than prototype order, but lose relevance: their
teacher-gap closure is negative on two or three seeds. Neither the direct nor
the progress gate passes.

The narrow conclusion is that **this matched 64D projected-K8 representation
does not solve the ordering problem**. Simply retaining more prototype geometry
through invariant pooling or light query gating is not enough under the frozen
teacher and training pool. Together with the R0 ambiguity diagnostic, this
licenses the predeclared R3 experiment with deterministic document-level bucket
summaries. It does not yet license a stateful policy.

Result SHA-256 is
`cb0ad8e6d528fdeca7ee0f6a473b5425be20cb5380c2058304a411652d293e00`.
Independent replay regenerated all nine model archives and reproduced the
complete result byte for byte; evidence SHA-256 is
`38a27cd77f8380ff7d4599b4dbaafd978767c89a9131af213e5d5053d2d83366`.

## Limitations

- The common 64D projection is teacher-blind and frozen. This result does not
  prove that a teacher-trained projection or full 384D cross-attention cannot
  help; those would be materially different parameter/compute studies.
- Matching parameter counts required different final hidden widths. The
  teacher, data, optimizer, query path, context operation, and listwise loss are
  matched, but the variants are not algebraically identical networks.
- R1/R2 summarize prototypes, not actual documents within a bucket. The
  privileged teacher can depend on a rare document mode that K8 prototypes do
  not preserve.
- Additional ES/FR/RU topics remain E5 pseudo-supervision only; their qrels are
  not used.
- Exact prototype retrieval is intentionally unoptimized and provides no
  latency claim.

## Next check

Freeze the same route, shortlist, teacher, budgets, and cascade, then add R3
only: a small deterministic document-level summary per occupied address. If R3
also remains near the current `.81-.84` band, revisit either a teacher-trained
projection/full-resolution interaction study or, only then, a genuinely
stateful policy.
