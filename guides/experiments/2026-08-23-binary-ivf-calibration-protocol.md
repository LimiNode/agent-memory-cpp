# BinaryIVF calibration protocol

Date: 2026-08-23. Context: a draft follow-up to the static ITQ locator budget
frontier. This is an external calibration experiment; it does not add Faiss or
any other dependency to the C++ library.

## Question

Can a binary inverted-file partition of the frozen ITQ-256 codes provide a
better approximate candidate-quality frontier than a static MIH locator at the
same actual candidate fraction?

## Frozen scope

Use only Spanish 25k `dev` materialization and its existing E5 evaluation
manifest. French confirmation data is forbidden. The ranking representation is
unchanged ITQ-256; every candidate shortlist receives the existing full-code
Hamming, binary-ADC, and E5 rerank cascade.

The reference query positions and exact Flat Hamming shortlist are the frozen
ones produced for the completed locator-frontier run. This prevents a change
in query sampling from masquerading as an index result.

## Treatments

The first external baseline uses `faiss-cpu==1.13.2`, pinned in an isolated
`tmp/` environment. It uses `IndexBinaryIVF(IndexBinaryFlat(256), 256,
nlist)`, trained only on the frozen document codes. Start with `nlist` 1024
and 4096; a 16384-list run is diagnostic-only because this 25k corpus gives a
very small training population per list.

For each codebook, vary global `nprobe` to target observed list-union fractions
near 5%, 10%, and 25%. A 1% generator-only diagnostic is not comparable to the
frozen cascade: 250 candidates cannot supply its required Hamming@768 list.
Record the actual sum of probed list lengths rather
than treating `nprobe/nlist` as a candidate count. Faiss's approximate binary
search result is then stably ordered by `(Hamming distance, document position)`
before the standard ADC/rerank evaluation.

## Interpretation

This is a calibration frontier, not a production selection or confirmation.
It may justify a separate Ball-IVF pruning diagnostic only if BinaryIVF has an
interesting quality/work region. Its results cannot be used to choose a
task-aware or learned locator; those remain distinct later protocols with
disjoint selection data.

## Evidence-bound calibration result

The first run used Faiss 1.13.2 and the pinned Flat query order. Faiss warned
that 25k vectors are below its recommended training population for both 1024
and 4096 centroids; these results are therefore a directional external
baseline, not a production codebook choice.

| nlist | nprobe | actual candidates | search p50 / p95 (ms) | E5 survival after ADC | reranked nDCG@10 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1024 | 51 | 5.40% | 0.371 / 0.682 | 85.11% | 0.7481 |
| 1024 | 102 | 10.48% | 0.465 / 0.960 | 90.63% | 0.7825 |
| 1024 | 256 | 25.37% | 0.643 / 1.115 | 95.99% | 0.8039 |
| 4096 | 205 | 5.66% | 0.546 / 1.039 | 92.38% | 0.7866 |
| 4096 | 410 | 10.86% | 0.768 / 1.156 | 95.15% | 0.7977 |
| 4096 | 1024 | 25.82% | 1.239 / 1.849 | 97.90% | 0.8069 |

At comparable 5–10% candidate fractions, BinaryIVF is materially stronger
than the random static locator frontier. This warrants the next diagnostic:
measure whether each trained IVF list's Hamming ball can prune enough lists
against the known Flat distance cutoff to justify a strict, tie-safe Ball-IVF
implementation. It does not yet establish a production latency claim because
the Faiss search loop is external Python benchmarking and no repeated
whole-process timing. It does now have an independent fail-closed evidence
archive: each index is serialized after training, reloaded before search, and
its SHA-256 is bound to every shortlist. The evidence packager reloads that
same index, replays every shortlist and ADC order, and replays E5 survival and
nDCG from per-query contributions. Two deterministic archives produced
SHA-256 `9b67dc4c8e85811083553660fdbcc3cace46b5c3821157752ca6ae8f4891b73e`.
