# Prototype-to-document expansion ceiling

Date: 2026-09-08.

## Question

The corrected shared-alpha experiment measured concentration of K8
prototypes.  This replay asks whether the same shortlist remains useful after
each prototype is expanded through its address-major postings into actual
documents.

This is a privileged ceiling, not a serving route: the segment ranking uses
the frozen top prototype teacher anchor(s).  The frozen input contains exact
E5 top-10 document ids, so document-pool survival is also exact-E5 top-10
survival.  It does not contain qrels, therefore qrels nDCG is not reported.

## Setup

* Seed 2026082701 semantic-anchor replay: 1,000,000 documents, 152 queries,
  454,322 K8 prototypes and 65,108 occupied addresses.
* Linear shared-alpha segment score, with prototype budgets `256/512/1024/
  2048/5000/10000`.
* Prototype postings are unioned by document id; address boundaries are
  recovered from the frozen address-major invariant (the first K8 record for
  each address equals its centroid).
* Exact FP32 scoring is applied only after document expansion.  The result is
  equivalent to pool survival for global exact top-10 targets: a target that
  is present in the pool necessarily remains in the pool's exact top-10.

## Results: one teacher anchor

| P prototypes | unique addresses (mean) | unique docs (mean; p05--p95) | duplicate docs | exact top-10 survival (mean / median / p05 / worst) | full 10/10 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 256 | 253.5 | 5,036 (4,012--6,077) | 0.73% | 0.606 / 0.700 / 0.100 / 0.000 | 9.9% |
| 512 | 508.4 | 9,821 (8,100--11,412) | 0.54% | 0.674 / 0.700 / 0.155 / 0.000 | 15.8% |
| 1,024 | 1,018.6 | 19,082 (16,496--21,586) | 0.42% | 0.730 / 0.800 / 0.255 / 0.000 | 21.1% |
| 2,048 | 2,039.4 | 36,938 (33,275--40,908) | 0.34% | 0.800 / 0.900 / 0.400 / 0.200 | 28.9% |
| 5,000 | 4,983.4 | 86,141 (79,464--91,064) | 0.28% | 0.859 / 0.900 / 0.500 / 0.300 | 38.8% |
| 10,000 | 9,970.3 | 166,221 (158,134--173,881) | 0.26% | 0.892 / 0.900 / 0.600 / 0.400 | 46.1% |

The selected prototypes are spread over almost one distinct address each;
address deduplication gives little reduction.  The hypothetical `256 ->
3--6k docs` case does occur in size, but not in quality: mean exact top-10
survival is only `.606` and the worst query loses all ten targets.

## Four-anchor upper-bound control

Using the four frozen teacher anchors in the shared-alpha minimum did not
improve document transfer at a fixed prototype budget.  At `P=256/1024/10000`
the mean exact top-10 survival was `.517/.653/.870`, with mean pools of
`5,433/19,962/169,230` documents.  The extra anchors expose additional
prototype postings, but those postings are not concentrated on the exact
document targets.  This is evidence against a naive fixed-budget multi-anchor
union, not against all multi-anchor selectors.

## Interpretation and limits

1. The `.902/.929/.953/.968/.978/.982` prototype curve cannot be presented as
   document retrieval quality.  After composition, the one-anchor document
   ceiling is `.606` at 256 and `.892` even at 10k prototypes.
2. This run uses a teacher anchor and therefore says nothing about whether a
   runtime selector can find the same anchor.  It is a transfer ceiling for
   the existing posting composition.
3. Exact top-10 survival is the available quality metric.  A qrels replay and
   a native/MDBX cost measurement remain separate follow-ups.
4. Prototype postings in this materialization are representative-document
   postings, not a full document assignment oracle.  A different address
   composition or representative policy must be evaluated as a new artifact.

## Decision

The prototype-to-document gate is now the primary blocker before investing in
physical directional THQ/MIH indexing.  Continue selector work only as a
targeted diagnostic (true best-in-pool and screen recall), and require it to
beat the `.606 @ 256` / `.730 @ 1024` transfer baseline at a declared document
budget.  Do not use the prototype `.982 @ 10k` number as a product claim.

Raw reports are kept outside Git under `tmp/`:

* `prototype-document-expansion-seed2701.json`
* `prototype-document-expansion-seed2701-a4.json`

Runner: `tools/agent-memory-bench/evaluate-prototype-document-expansion.py`.
