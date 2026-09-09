# NeuRoute prefix-aware router frontier

Date: 2026-09-02

## Question

Can 12- and 14-bit prefix utility heads trained jointly from the 8,141-query
offline-teacher cache select at most 4,096 addresses accurately enough for exact
local K8 and the frozen R4 cascade?

## Contract

Global FP32 K8 is used only to create frozen teacher shortlists and as the
reference replay. Treatments score a 12-bit or 14-bit prefix head, expand a
bounded number of descendants, score K1 only for those descendants, and pass
at most 4,096 addresses to exact local K8. There is no global query-time K8 scan.

The training targets aggregate either rank-discounted teacher mass or
rank-and-K8-margin mass into true prefixes of the current 16-bit address space.
Configuration-only offline diagnostics choose two finalists per topology;
native configuration replay then opens one fixed M=4,096 row per topology on
the reused confirmation partition.

## Result

The offline frontier evaluated 24 topology/target/ridge/beam combinations. For
every topology it selected rank-mass supervision, 4x descendant expansion, and
both ridge values. K8-margin mass and 2x expansion were inferior before native
replay.

None of the six finalists passed the full configuration cascade at `M=4096`:

| Generator | Mean / max-seed nDCG loss | Final top10 overlap | Candidate / Hamming / ADC overlap | Generator / local-K8 p95, ms | Fine K1 rows |
| --- | ---: | ---: | ---: | ---: | ---: |
| Prefix12, ridge .1 | .050449 / .078346 | .8478 | .9324 / .9356 / .8966 | 14.01 / 17.34 | 16,391 |
| Prefix12, ridge 1 | .050449 / .078346 | .8478 | .9324 / .9356 / .8966 | 14.22 / 15.47 | 16,391 |
| Prefix14, ridge .1 | .033810 / .047508 | .8680 | .9540 / .9549 / .9206 | 15.36 / 15.83 | 16,386 |
| Prefix14, ridge 1 | .033810 / .047508 | .8680 | .9540 / .9549 / .9206 | 15.45 / 15.76 | 16,386 |
| Recursive 12-to-14, ridge .1 | .029660 / .042204 | .8732 | .9570 / .9575 / .9245 | 17.82 / 16.45 | 16,386 |
| Recursive 12-to-14, ridge 1 | .029660 / .042204 | .8732 | .9570 / .9575 / .9245 | 17.73 / 15.67 | 16,386 |

The best fixed `M=4096` row per topology then failed reused confirmation:

| Generator | Mean / max-seed nDCG loss | Final top10 overlap |
| --- | ---: | ---: |
| Prefix12 | .071551 / .082584 | .8430 |
| Prefix14 | .055854 / .092882 | .8583 |
| Recursive 12-to-14 | .057610 / .091591 | .8640 |

The independent evidence pass also tested whether a prefix shortlist could be
used only for diversity while retaining 50%, 75%, 87.5%, or 93.75% of K1
Top-4096. The prefix rows recover only 0.14--0.21 K1-missed teacher addresses
per query and evict more useful K1 rows. At the most conservative 93.75% mix,
the best teacher coverage/rank coverage is `.998574/.998090`, versus the K1
baseline `.998574/.998105`. No hybrid native replay was licensed.

## Decision

Joint prefix-aware utility training does not repair the cheap-selector frontier
on this evidence. It improves the interpretation of the earlier frozen replay
but remains materially worse than the exact-address K1 control, and a K1/prefix
union has no positive offline complement signal.

No treatment is licensed for native implementation or production. Global K8
remains an offline teacher/reference, and query-time global K8 over all occupied
addresses remains outside the product line. Because there is no passing cheap
selector, the conditional native-integration step is closed without code
changes rather than integrating a known quality regression.

This closes the current learned 12/14/16 routing branch. A future reopening
needs genuinely new supervision or structure--for example judged multi-domain
queries, a different partition objective, or an index whose query path has a
separately acceptable cost--not another beam/ridge sweep of these targets.

## Limitations

- The 8,141 rows contain multilingual pseudoqueries without German judgments.
- The second evaluation partition is reused confirmation, not a pristine
  holdout.
- Python timing is directional; a passing treatment still requires native
  implementation before production licensing.

## Reproduction

The ignored result is stored under `tmp/neuroute-prefix-aware-router/`.

- Result SHA-256: `2eed62bc0ca93fa532ba1b0721d5e0a0ae16eb864a52517861b0cc7e11c9226e`
- Evidence SHA-256: `c21736435bc67269372d0e99e4d683e6175b18c682ede3f6824ebb0235f36d15`
