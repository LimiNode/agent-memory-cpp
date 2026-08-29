# NeuRoute nonlinear listwise prototype-shortlist reranker

Date: 2026-08-29. Frozen protocol; measurement pending.

## Question

#229 established a strong fixed frontier inside the exact K8 prototype
top-1024 shortlist, but its 153-query pairwise ridge model failed to recover
the privileged address order. This study isolates whether the remaining gap is
caused by scorer capacity, training-query density, or both.

## Frozen protocol

The document side is unchanged:

```text
DE-1M frozen 16-bit partition
-> eight deterministic prototypes per occupied address
-> exact maximum-prototype top-1024 address shortlist
-> learned address order
-> MDBX candidate union
-> Hamming-768 -> ADC-64 -> exact-E5/INT5 evaluation
```

The three document-route seeds, document addresses, K8 construction, exact
prototype retrieval, 1024-address shortlist, candidate-mass cap, cascade and
address budgets are inherited without tuning. Prototype ANN/HNSW and native
latency are outside this study.

Training appends the already materialized 7,988 MIRACL Spanish, French and
Russian train topics after the 153 German training queries. The nested sizes
are `153/512/2048/4096/8141`. The additional topics have no German qrels, so
their supervision is declared pseudo-supervision: exact frozen E5 top-10 over
the DE-1M corpus, converted to discounted address gain per posting entry. They
do not enter the configuration or evaluation metrics.

For every route seed the study compares:

- the current pairwise ridge feature control;
- a small query-conditioned pointwise MLP trained with query-wise ListNet;
- an equivariant DeepSets-style model that also sees pooled shortlist context.

All neural fits are deterministic Torch CPU runs with one frozen capacity and
optimizer recipe. A query whose exact-E5 top-10 contributes no address to its
frozen top-1024 shortlist remains in the reported nested pool but is excluded
from the loss and counted explicitly; ridge uses the same zero-positive policy.
The only selection axes are nested training count and the predeclared
ridge-alpha controls. The 76 German configuration queries select one model per
seed and variant. The separate 76 German internal queries remain closed until
all selections are fixed.

## Decision rule

Headline evaluation is at 256 selected addresses, with 128 and 512 retained as
supporting budgets. A treatment directly passes only when every DE-1M seed has
actionable gain at least `.90` and candidate fraction at most `.005`.

The directional progress gate requires every seed to close at least half of
its frozen prototype-order to privileged-teacher actionable-gain gap while
using no more than `1.05x` the prototype-order candidate mass. Production
selection and native confirmation remain forbidden in this PR.

The direct and directional progress gates are alternative decisions: direct
success is the absolute production-quality diagnostic, while progress can
license objective work even when the absolute gate is missed.

The stacked teacher/objective ablation is predeclared as part of the approved
batch and runs after this PR freezes its selected model. Its execution does not
depend on observing a positive or negative internal result here.

## Evidence requirements

The result must bind the #229 result/evidence bytes, the multilingual query
bundle, the German split, the width materialization, every model archive and
the authoritative-qrels parent chain. The evidence writer must regenerate all
shortlists, pseudo-teachers, models, configuration rows and internal rows in a
temporary directory and reproduce the result byte for byte.

## Limitations

The additional supervision measures multilingual semantic transfer into the
German document corpus rather than additional independently judged German
queries. Exact prototype enumeration is a diagnostic implementation. A
positive quality result licenses a later native shortlist-retrieval study; it
does not provide native latency evidence itself.
