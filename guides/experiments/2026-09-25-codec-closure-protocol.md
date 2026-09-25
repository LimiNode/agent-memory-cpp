# Codec closure protocol

Date: 2026-09-25
Status: `IN_PROGRESS`; no product selection.

This is the bounded final research program for compressed final scorers.  It
replaces neither the source-bound historical-152 evidence nor a fresh final
evaluation.  Its purpose is to keep promising codec families comparable rather
than turning a sequence of exploratory runs into a product claim.

## Fixed contracts

- Canonical source: normalized `intfloat/multilingual-e5-small`, revision
  `614241f622f53c4eeff9890bdc4f31cfecc418b3`.
- Candidate shell: frozen R4 IDs rebound to canonical packed ordinal THQ4,
  then canonical THQ interval-squared top-128.
- Scoring metric: cosine, with every direct reconstructed-vector arm charged
  for its persisted final-norm sidecar.
- Historical-152 is an engineering/reproduction fold, not an untouched final
  test.  Query-aware fitting must not train on it.
- Every executed arm needs source hashes, a persisted code/model artifact,
  independent decoding or score replay where applicable, and complete storage
  accounting.

## Current external source registry

External repositories live outside the library under
`E:\_repoz\agent-memory-workspaces\external-codec-references`.  They are
research inputs, not vendored product dependencies.

| Family | Snapshot | License implication | Gate |
| --- | --- | --- | --- |
| RaBitQ Library | `VectorDB-NTU/RaBitQ-Library` `a010649f8faabc286070e5ed18c7dc121e01ffe3` | Apache-2.0; integration may be evaluated separately | Official 2/3/4-bit IP/cosine candidate scorer with real sidecar and model accounting |
| SAQ | `howarlii/saq` `2163ebcedd0ad9c9f4de326e6ca7a860f9eafe52` | Apache-2.0; integration may be evaluated separately | External PCA/segmentation/code-adjustment control; upstream requires AVX-512, so no portable-library claim without a fallback |
| QINCo2 | `facebookresearch/QINCo` `5a324954d5c9b3700d4407d6cc24c3db6e52890e` | CC-BY-NC-4.0; research-only reference, do not vendor or present as a product dependency | Adequately trained external control with 25k/100k/database-vector scaling |
| AAQ | Existing source-pinned bounded reference | License must be rechecked before any integration | Separate reconstruction and query-aware objectives on a new query-training pool |
| LeanVec/GleanVec | External-only control pending source/license review | No library implementation implied | SVS-supported external matched benchmark, with proprietary pieces declared |

## Ordered gates

1. **Classical completion.** Run LSQ32/48 light, medium, and strong budgets
   with 4k and 25k fitting rows and at least three outer seeds.  Fit time and
   candidate-union encoding time are part of the result.  PQ8/OPQ8 and
   normalized TQ-domain residual PQ/OPQ use the frozen canonical TQ1 base.
2. **Training-free and multi-bit comparison.** Run faithful RSLM2/3/4 and
   official RaBitQ 2/3/4-bit under the same top-128 and cosine protocol.  Do
   not call a local approximation vendor-compatible.
3. **Learned controls.** Train QINCo2 on unsupervised database vectors at
   sufficient scale.  Treat it as an external non-commercial research control.
   Run AAQ/query-aware codecs only after a separate judged query-training pool
   and a pre-registered evaluation split exist.
4. **External dimensionality baseline.** Record LeanVec/GleanVec behavior as
   an external benchmark, including implementation availability and all model
   state.  Do not infer a library feature from it.
5. **Production bridge.** Materialize 1M code plus sidecars for finalists and
   compare `R4 -> THQ4 byte-LUT top128 -> codec -> cosine top10` in native
   code, with parity, p50/p95/p99, warm-process terminology, pages, layout,
   and encode throughput.
6. **One final confirmation.** Select a fixed finalist set before opening a
   fresh, pre-registered query/qrels evaluation only once.

## Explicit stopping condition

The algorithm search closes when every gate above has either source-bound
executed evidence or a documented source/licensing blocker.  New papers after
that point enter normal feature/research PRs; they do not retroactively change
the selected frontier without the same protocol.
