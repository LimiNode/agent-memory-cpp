# AAQ / score-aware LSQ / QINCo2 / residual-hybrid wave

Date: 2026-09-24

This wave follows the corrected TurboQuant+ replay on branch
`research/aaq-qinco2-residual-wave`. It uses the recovered canonical E5/R4
source bundle and the frozen 152-query, 128-document THQ shell from the strong
Faiss LSQ replay.

## Question

Can a query-aware local-search refinement of a frozen additive residual code
recover useful ranking quality, and is there enough evidence to promote this
to an AAQ claim? In parallel, can the official QINCo2 source be exercised
without silently replacing it by a local approximation, and how do the
residual hybrids compare at equal THQ + side-payload budgets?

## Score-aware LSQ control

The runner `run-thq-score-aware-lsq.py` freezes the Faiss LSQ32/LSQ48
codebooks and the THQ top-128 shell. For each query/document it performs one
coordinate pass in which a code may move only to one of the eight nearest
codewords in its stage. The objective is query dot-product; the final result
is ranked by exact reconstructed cosine. The substituted code is persisted and
replayed independently by `audit-thq-score-aware-lsq.py`.

This is intentionally a candidate-local, query-dependent diagnostic. It is
not an official AAQ implementation and is not a deployable document code.

| arm | side bytes | mean nDCG@10 | mean teacher overlap | mean changed-code fraction |
| --- | ---: | ---: | ---: | ---: |
| frozen Faiss LSQ32 | 32 | 0.657264 | 0.886842 | 0.000000 |
| score-aware LSQ32 | 32 | 0.630466 | 0.798026 | 0.875071 |
| frozen Faiss LSQ48 | 48 | 0.661515 | 0.894079 | 0.000000 |
| score-aware LSQ48 | 48 | 0.621929 | 0.799342 | 0.867880 |

The independent audit is `PASS` with zero top-10 mismatches over 304 refined
rows. The negative result is informative: unconstrained local score movement
away from reconstruction quality hurts ranking substantially. It does not
disprove trained AAQ/AVQ objectives, but it does rule out this simple
post-hoc substitution as a useful production shortcut.

## QINCo2 source gate

`run-qinco2-source-smoke.py` imports the official Facebook Research QINCo2
repository at revision `5a324954d5c9b3700d4407d6cc24c3db6e52890e`, constructs
the official `QINCo` model, and exercises CPU encode/decode on a 16-dimensional
synthetic fixture. The smoke is `EXECUTED`; `quality_status` remains
`NOT_EXECUTED` because no QINCo2 checkpoint trained on the E5/R4 corpus is
available. This is a source/provenance gate, not a quality number.

## Residual-hybrid interpretation

The source-bound controls now form a clear, but not fully paired, matrix:

| hybrid/control | payload | mean nDCG@10 | status |
| --- | ---: | ---: | --- |
| THQ + Faiss LSQ32 | 128 B total | 0.657264 | source-bound, audited |
| THQ + Faiss LSQ48 | 144 B total | 0.661515 | source-bound, audited |
| THQ-residual TQ+ exact-wide composite | 156 B total | 0.654486 | corrected source-bound replay |
| THQ-residual TQ+ ideal-float composite | 156 B total | 0.657829 | diagnostic upper/control |
| THQ-residual RSLM3/RSLM4 | 146/194 B side | see faithful-binary note | source-bound controls |
| QINCo2 | 32/48 B target | — | source smoke only; no checkpoint |

The TQ+ values use the corrected residual scaling, scalar
`QuerySimd<8,2>`-style wide query, and persisted composite norm. Its candidate
stream differs from the old frozen stream, so it is a protocol-correction
replay rather than a paired replacement for the LSQ numbers.

## Provenance

The LSQ shell/model artifacts are the existing source-bound replay under
`E:/_repoz/research-wave-2026-09-22-lsq-strong/`; the new result and audit are
under `E:/_repoz/research-wave-2026-09-24-aaq-lsq/`. The QINCo smoke JSON is
stored beside them. All committed runners record source/model/artifact hashes;
large generated arrays remain outside Git.

## Limitations and next checks

* The score-aware control is query-dependent and therefore cannot support a
  serving claim.
* A real AAQ/AVQ comparison still requires a pinned upstream implementation or
  a train-time score-aware objective, with held-out query separation.
* QINCo2 quality requires a reproducible E5/R4 fit or a matching public
  checkpoint; the official source was not modified.
* A fully paired residual-hybrid table should be rerun after a single shared
  candidate receipt and common final reranker. Until then no production codec
  selection is made.
