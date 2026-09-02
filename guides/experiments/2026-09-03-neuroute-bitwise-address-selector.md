# NeuRoute query-supervised bitwise address-selector frontier

Date: 2026-09-03

## Question

Can a compact query-to-bit head select useful occupied 12-, 14-, or 16-bit
addresses before exact local K8? This is deliberately different from the failed
#273 prefix utility heads: the model predicts signed address bits, and the
query-time score is a sum of at most 16 bit contributions. It never scans K8
prototypes and never runs global K8 at query time.

## Setup

The runner uses the frozen 8,141-query teacher cache and the same three R4
seeds, configuration/locked-internal split, authoritative qrels, and native
Hamming768 -> ADC64 -> exact-E5 cascade as the preceding selector studies. For
each width (12, 14, 16), rank-discounted teacher mass and K8-margin mass are
aggregated into signed bit labels. A rank-64 projected query is fit with ridge
values 0.1 and 1.0. At inference, occupied addresses are ranked by the dot
product between their signed bit vector and the predicted bit logits. Budgets
are 1,024, 2,048, and 4,096 addresses; the latter is the only product-budget
selection point. Global FP32 K8 is an offline teacher/reference only.

Configuration chooses one finalist per width without looking at locked
internal. The best fixed 4,096-address row per width is then replayed on the
reused confirmation partition. No treatment is production-licensed by this
research PR; a passing row would only authorize a separate native integration
step.

## Result

The replay completed on all three seeds and all registered budgets. The
bitwise selector did not approach the registered full-cascade gates. The
configuration finalists (chosen without using the locked partition) were the
`k8_margin_mass`, ridge-0.1 treatment at each width:

| width | budget | mean nDCG loss | final top10 overlap | candidate retention | ADC overlap |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 12 | 1,024 | 0.2653 | 0.3987 | 0.3420 | 0.4118 |
| 12 | 2,048 | 0.1900 | 0.5425 | 0.5014 | 0.5625 |
| 12 | 4,096 | 0.1116 | 0.7004 | 0.6808 | 0.7225 |
| 14 | 1,024 | 0.2196 | 0.4860 | 0.4335 | 0.4984 |
| 14 | 2,048 | 0.1346 | 0.6351 | 0.6018 | 0.6545 |
| 14 | 4,096 | 0.0727 | 0.7671 | 0.7647 | 0.7921 |
| 16 | 1,024 | 0.1807 | 0.5474 | 0.5076 | 0.5650 |
| 16 | 2,048 | 0.1105 | 0.6803 | 0.6738 | 0.7141 |
| 16 | 4,096 | 0.0544 | 0.8180 | 0.8231 | 0.8420 |

The locked confirmation at 4,096 addresses was similarly negative: mean
final top10 overlap was 0.6684 (12-bit), 0.7465 (14-bit), and 0.7899
(16-bit). No row passed the registered quality gate. The failure is already
visible in offline teacher coverage (at 4,096 addresses, rank-discounted
global-K8 coverage was only 0.63/0.71/0.78 for widths 12/14/16), so this is
not a native-cascade or latency artifact. Native coarse p95 stayed below the
15 ms directional target, but quality is the binding failure.

This closes the query-supervised signed-bit selector as a product candidate
under the preregistered 4,096-address bound. Global FP32 K8 remains an offline
teacher/reference only; no native or production integration is licensed.

The raw report is intentionally kept under
`tmp/neuroute-bitwise-address-selector/` and is not committed.

Reproducibility bindings: `result.json` SHA-256
`86f7ec5dca0de3df5920934dae1f53d2429d54ceddfd57927b22ae6a3822c6e5`;
validated `evidence.json` SHA-256
`f9dd39fb859df2c18bf1f48f9a55a2a06d080a92c7ff47cc8ac66964b0e497b4`.

## Follow-up: capacity trend versus saturation

Quality improves monotonically from 12 to 16 bits at the fixed 4,096-address
budget (configuration overlap `0.7004 -> 0.7671 -> 0.8180`; locked
confirmation `0.6684 -> 0.7465 -> 0.7899`). This is evidence that the tested
capacity has not saturated; it is not evidence that this same address-bit
construction can simply be extended to 18/20/24/32 bits. R4 addresses are
16-bit values, so bits above bit 15 are constant and carry no selector
information.

Any post-16-bit frontier must therefore be registered as a new representation
(for example, learned latent or multi-hash address codes) with an explicit
bounded lookup mechanism. It must not be reported as a wider version of this
direct address-bit selector. The stopping rule should be empirical: extend the
new representation until two consecutive width increases have no meaningful
held-out gain, while keeping the address budget and full-cascade gates fixed.

## Limitations

- Teacher labels contain only the cached top-1,024 addresses per training query;
  unobserved addresses are implicit negatives.
- The internal partition is the established reused confirmation split, not a
  new holdout.
- Python selector timing is directional; native timing excludes model training.

## Reproduction

Run `run-neuroute-bitwise-address-selector.py` with the validated policy,
configuration, R4 layout, K8 manifest, native executable, multilingual query
pool, and training-cache roots. Validate the resulting report with
`write-neuroute-bitwise-address-selector-evidence.py`.
