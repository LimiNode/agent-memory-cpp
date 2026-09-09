# NeuRoute 12/14-to-16-bit hierarchy replay

Date: 2026-09-02

## Question

Can a 12- or 14-bit coarse route generate a sufficiently faithful shortlist of
the current 16-bit occupied addresses before exact local K8, and does a true
same-head prefix avoid the many-to-many loss of independently trained widths?

## Artifact audit

The current R4 layout was reconstructed back to document order and compared
with the retained width-study materialization. For all three frozen seeds, its
document addresses equal the retained independent 16-bit head for every one of
the 1M documents (`same_fraction = 1.0`). Inference from those model bytes
reproduces the 76 stored configuration-query logits with maximum absolute error
`2.98e-8` and also supports the 76 later requests.

This permits two distinct tests without inventing a new topology:

- project scores from independently trained 12- and 14-bit heads into current
  16-bit rows through document co-occurrence;
- take the low 12 or 14 bits of the same frozen 16-bit head, expand selected
  prefixes to their true 16-bit children, and score suffixes only inside that
  beam.

Direct full 16-bit address-logit order is the control. Exact local FP32 K8 and
the remaining frozen R4 cascade are unchanged.

The formal replay opens all 12- and 14-bit beam factors, records the number of
coarse prefixes and fine children actually scored, and reports model/index
bytes. Global FP32 K8 is an offline teacher/reference only. The 8,192-address
points are sensitivity controls; production eligibility stops at 4,096.

## Directional offline diagnostic

Before native replay, a bounded all-seed diagnostic measured global-K8
top-1024 coverage. Values below are configuration / reused-confirmation means
at M=8192:

| Generator | Coverage |
| --- | ---: |
| Direct same-head 16-bit logits | 0.8567 / 0.8495 |
| Same-head prefix, 1x expansion | 0.7438 / 0.7328 |
| Same-head prefix, 2x expansion | 0.8423 / 0.8356 |
| Same-head prefix, 4x expansion | 0.8563 / 0.8494 |
| Independent 12-bit mean association | 0.7948 / 0.7674 |

Thus 4x prefix expansion faithfully approximates the direct 16-bit order, but
the direct frozen router itself remains materially below K1/ANN coverage. The
native replay is still required to locate losses at candidate, Hamming, ADC,
and final boundaries; this table alone cannot make a production decision.

## Result

No treatment passed the registered product gate at `M <= 4096`. The complete
configuration replay at the largest eligible budget was:

| Generator at M=4096 | Prefixes scored | Fine addresses scored | Generator / local-K8 p95, ms | Mean / max-seed nDCG loss | Final top10 overlap | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Direct same-head 16-bit | 0 | 65,113 | 1.477 / 14.568 | .017142 / .038780 | .8750 | fail |
| Prefix12 beam 1x | 258 | 4,104 | 1.582 / 14.585 | .044438 / .071342 | .7785 | fail |
| Prefix12 beam 2x | 512 | 8,143 | 1.793 / 14.406 | .018155 / .031536 | .8557 | fail |
| Prefix12 beam 4x | 1,024 | 16,286 | 2.176 / 14.615 | .016523 / .038780 | .8750 | fail |
| Prefix14 beam 1x | 1,030 | 4,097 | 2.846 / 14.606 | .031005 / .047112 | .8320 | fail |
| Prefix14 beam 2x | 2,048 | 8,143 | 3.085 / 14.514 | .019824 / .033009 | .8719 | fail |
| Prefix14 beam 4x | 4,096 | 16,283 | 3.433 / 14.790 | .017142 / .038780 | .8750 | fail |
| Independent 12-bit association | 4,096 | 65,113 | 16.095 / 13.828 | .117565 / .130764 | .6974 | fail |
| Independent 14-bit association | 16,384 | 65,113 | 16.361 / 13.766 | .100042 / .127296 | .7298 | fail |

The 4x prefix treatments recover the direct 16-bit downstream result while
scoring about 16.3k rather than 65.1k fine addresses. They do not recover the
global-K8 reference because the frozen direct head itself is the limiting
component. The independently trained heads do not define a usable tree.

All five failing families opened only their registered non-product `M=8192`
boundary on the reused confirmation partition:

| Boundary control | Mean / max-seed nDCG loss | Final top10 overlap |
| --- | ---: | ---: |
| Direct same-head 16-bit | .010758 / .014028 | .9447 |
| Prefix12 beam 4x | .011021 / .014028 | .9447 |
| Prefix14 beam 4x | .010758 / .014028 | .9447 |
| Independent 12-bit association | .057056 / .090880 | .8009 |
| Independent 14-bit association | .086093 / .114629 | .8224 |

The final v4 replay contains 37 configuration and six confirmation checkpoints.
Its evidence validator rechecked the bound model, topology, shortlist, native
executable, authoritative-qrels, stage-gate, and decision hashes. The v3-to-v4
maximum difference across the reported quality metrics was zero; v4 only makes
the preregistered `M=8192` fallback explicit and enforceable.

## Decision

Frozen same-head hierarchy is not licensed for native or production
integration. It establishes that 12-to-16 and 14-to-16 beam expansion can
implement the direct 16-bit ranking with much less fine scoring, but it cannot
make an inadequate frozen router accurate. `M=8192` also fails confirmation and
remains a sensitivity control.

Global FP32 K8 remains an offline teacher/reference only. The next product
experiment must compare all existing generators at identical `M <= 4096`, then
train a prefix-aware 12/14/16 selector from the 8,141-query teacher cache if no
cheap existing selector passes. It may use bounded local K8 after selection; it
must not turn global K8 over all occupied addresses into the query path.

## Limitations

- The second partition has been reported by preceding experiments. The runner
  prevents it from selecting treatments, but it is not a pristine holdout.
- The independent-width associations are diagnostic sparse projections, not
  proposed production execution paths.
- These retained heads were trained under the historical width-study regime;
  this experiment does not relabel them as an 8,141-query learned router.

## Reproduction

The ignored result is stored under
`tmp/neuroute-width-hierarchy-replay-confirmed-v4/` and binds the width result/model bytes,
width materialization, current layout/K8 manifests, protocol closure, native
executable, generated shortlist bytes, and authoritative qrels receipt.

- Result SHA-256: `b5fce5cefe974bd9524055995a4a7eb54b0b50c0b6bd6d752d4677b5a63e2c38`
- Evidence SHA-256: `da02dbc7fec8d2014b7dcf2b45964540d96fa58475a90fabace82848116bf922`
