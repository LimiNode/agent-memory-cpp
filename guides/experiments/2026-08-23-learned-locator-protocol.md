# Learned locator protocol

Date: 2026-08-23. This protocol is intentionally separate from ITQ-256 ranking
and from the task-aware static selector. Its machine-readable contract is
`tools/agent-memory-bench/learned-locator.example.json`.

## Execution guard

Execution is forbidden by default. Before any training command, the runner must
accept a passing `task_aware_static_locator_permission_v1` artifact through
`verify-learned-locator-permission.py`. The artifact must bind the exact
task-aware contract and prove its predeclared predicate: the frozen task-aware
row strictly beats both leakage-safe random-static and BinaryIVF comparators on
the Spanish internal-evaluation partition, within its candidate and fresh
latency budgets. The historical random-static result is not authorization.

The Spanish third partition is an **internal evaluation**, not an untouched
project-level confirmation set. French confirmation data remain forbidden here.

## Goal and scope

Learn a 64–128 bit routing code that reduces candidate work while retaining the
E5-relevant documents that frozen full ITQ-256 Hamming/ADC/reranking will rank.
The learned code is a locator only: final ranking remains the frozen full
ITQ-256 cascade. It is a research-only external Python/PyTorch treatment; it
does not add a neural dependency to the C++ library.

Document codes are materialized offline. Query encoding is a recorded CPU
`B x 256` matrix multiplication and hard sign, not Transformer inference.
Training/build time is reported separately from query encoding and candidate
generator latency.

## Predeclared treatment

Encode each ITQ bit as `-1/+1`, apply a bias-free `B x 256` linear map for both
documents and queries, then hard-binarize it. `B` is one of 64, 80, 96, 112,
or 128. Initialize with Xavier-uniform for seeds 20260901, 20260902, and
20260903. Train only on the task-aware selector-training partition: positives
are deterministic exact E5 top-10 documents and negatives are the task-aware
protocol's deterministic 128 non-positives.

The forward binary value and STE are exactly:

```text
b_i = +1 if z_i >= 0, otherwise -1
db_i / dz_i = 1 if |z_i| <= 1, otherwise 0
H_STE(q, d) = (B - dot(b_q, b_d)) / 2
```

For every positive/negative pair in a batch, optimize:

```text
mean softplus(2 + H_STE(q, p) - H_STE(q, n))
+ 0.01 * mean_b(mu_b^2)
+ 0.01 * mean_off_diagonal(C_bc^2)

mu_b = mean_examples(b_example_b)
C = (b - mu)^T (b - mu) / example_count
```

The balance term encourages each bit to use both signs; the distinct
off-diagonal covariance term discourages bit collapse. The old expression
involving `mean_code_b^2 - 1` is deliberately not used: on hard `-1/+1` codes
it is vacuous under one reading and ambiguous under another. Reject a
checkpoint whose document-side bit marginal is outside `[0.05, 0.95]`.

Use CPU PyTorch 2.8.0, NumPy 2.4.6, Python 3.12.13, deterministic algorithms,
one Torch thread, PCG64 Fisher–Yates batches seeded by `20260901 + epoch`, 40
epochs, batch size 64, AdamW (`lr=1e-3`, `weight_decay=1e-4`), and save the
model/optimizer checkpoint after every epoch. The contract pins these details,
the loss, routing schedule, comparator set, selection rule, and evidence
membership.

## Selection and evidence

Use 16-bit bands and the task-aware nested r3-to-r4 schedule. On the separate
configuration-selection partition, choose the lexicographic maximum of
`(E5 survival, nDCG@10, -candidate fraction, -p50, -B, -r4-prefix, -seed,
-epoch)` subject to 25% candidates and a fresh full-ITQ-256 `m19` p50 measured
only on that partition. The internal evaluation cannot select latency,
configuration, seed, epoch, or checkpoint. If no row is feasible, record no
selection rather than moving a threshold.

Compare at matched observed candidate fractions with leakage-safe random static,
task-aware static, and BinaryIVF. A fail-closed evidence archive must bind the
permission artifact, partitions, model/optimizer/training-source hashes,
checkpoint bytes and hashes, materialized-code hash, query-encoding runtime,
comparator shortlists/quality/contributions, selection decision, and per-query
E5/nDCG replay. Any production claim still needs a separately frozen untouched
confirmation dataset.
