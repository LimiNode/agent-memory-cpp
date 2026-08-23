# Learned locator protocol

Date: 2026-08-23. This protocol is intentionally separate from ITQ-256 ranking
and from the task-aware static selector. It starts only after that selector is
measured; it is not implicitly authorized by a random-static result.

## Goal

Learn a 64–128 bit routing code that reduces candidate work while retaining
the E5-relevant documents that full ITQ-256 Hamming/ADC/reranking will rank.
The learned code is a locator only: final ranking remains the frozen full
ITQ-256 cascade.

## Leakage and evaluation contract

Train locator parameters only on a declared training corpus and its allowed
query/relevance supervision. Select width, band allocation, loss checkpoint,
and probing schedule only on a disjoint calibration partition. Evaluate the
one frozen choice on a third untouched partition, then use a separate
confirmation dataset before making any production claim. Persist model
architecture, initialization/optimizer seeds, training inputs and hashes,
checkpoint bytes/hash, code materialization hash, all query partitions, and
the full candidate-to-E5 contribution replay in a fail-closed evidence archive.

## Required comparators

Compare at matched observed candidate fractions against random static locator,
task-aware static locator, and BinaryIVF. Report routing build/training time
separately from query encoding and candidate-generator latency. A learned
locator may use document-side offline neural work, but the protocol must state
whether query-side model inference is required; it must not present offline
training time as query latency or hide an inference dependency in the core
C++ library.

## Predeclared first treatment family

The first learned study is deliberately modest: a symmetric learned binary
remapping of frozen ITQ-256 signs, not a new E5 encoder. Encode each ITQ bit as
`-1/+1`, then apply a bias-free `B x 256` linear map and sign activation for
both documents and queries. `B` is one of 64, 80, 96, 112, or 128. The
initialization is Xavier-uniform with seeds 20260901, 20260902, and 20260903.
Document codes are materialized offline; query encoding is the recorded
`B x 256` CPU matrix operation plus sign, not Transformer inference.

For every training query, positives are its exact E5 top-10 documents and
negatives are the 128 deterministic non-positives defined by the task-aware
selector protocol. Optimise a straight-through binary activation for 40
epochs with AdamW (`lr=1e-3`, `weight_decay=1e-4`, batch size 64) and loss:

```text
mean softplus(2 + H(q,p) - H(q,n))
+ 0.01 * mean_b (mean_code_b)^2
+ 0.01 * mean_b (mean_code_b^2 - 1)^2
```

The first term averages over every positive/negative pair in a batch; the
last two terms enforce balance and non-degenerate binary bits. Reject any
checkpoint with a bit having document-side marginal probability outside
`[0.05, 0.95]`. Save checkpoints after every epoch; their bytes and optimizer,
model, input, and source hashes are evidence members.

Use the same 16-bit band construction and r3-to-r4 candidate schedule as the
task-aware static protocol. On the disjoint selection partition, choose the
lexicographic maximum of `(E5 survival, nDCG@10, -candidate fraction, -p50,
-B, -r4-prefix, -seed, -epoch)` subject to the same 25% and fresh-m19 budget.
The selected checkpoint is evaluated once on the internal split. Do not train
on it. No checkpoint that fails the balance gate is selectable. If no
checkpoint/configuration meets budget, record no selection rather than moving
the threshold.

This protocol remains blocked on the task-aware static run: it must use that
run's recorded split definition and compare its frozen internal-evaluation
frontier with both static baselines and BinaryIVF before any external
confirmation. The internal Spanish split is not an external untouched dataset.
