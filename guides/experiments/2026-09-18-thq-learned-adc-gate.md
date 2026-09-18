# THQ score-aware learned ADC gate

Date: 2026-09-18  
Branch: `research/thq-score-codecs`  
Scope: canonical frozen 152-query semantic R4 candidate shell, 1M DE-1M rows.

## Question

Can a compact document side-code preserve the `q·x` ordering without storing
or reconstructing a full FP32 document vector? This gate tests an additive
block ADC: one 4-bit symbol per disjoint block, with 16/32/64 blocks for 8/16/32
logical bytes per document. Codebooks are fitted in a query-weighted
Mahalanobis space from the detached 25k training vectors and the first 120
query rows. Queries 120--151 are not used for fitting and are reported as the
held-out split.

Scoring uses per-block query LUTs and analytic norm terms. The runner does not
materialize a stored 384-dimensional approximation. This is a NumPy quality
gate, not native latency, persistence, or page-accounting evidence.

## Results

| arm | payload | all-query teacher overlap | held-out teacher overlap | all-query candidate-FP32 overlap | held-out qrels nDCG@10 | pairwise order (all) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| learned ADC | 8 B | .8493 | .8813 | .8513 | .6728 | .9019 |
| learned ADC | 16 B | .8599 | .8875 | .8625 | .6893 | .9087 |
| learned ADC | 32 B | .8730 | .9000 | .8750 | .6847 | .9212 |

The held-out split is not directly comparable to the all-query score-only
receipt because the latter reports all 152 queries. On the common all-query
shell, the earlier controls were: direct INT8 `.9895` teacher overlap and
`.6570` nDCG@10; direct RSLM3 `.9658` and `.6540`; THQ-SDC 3-bit `.9454` and
`.6550`. The learned block ADC therefore remains below the existing direct
INT8/RSLM3 controls at all tested payloads. Increasing the payload from 8 B to
32 B improves pairwise order (`.9019 → .9212`) but does not produce a quality
frontier gain.

For the held-out 32-query slice, the 16 B arm has mean nDCG delta `+0.0043`
against candidate FP32 and `+0.0044` against direct INT8, but the paired
bootstrap 95% intervals are `[-.0170, +.0293]` and `[-.0172, +.0293]`.
The 8 B arm is negative (`-.0122` against candidate FP32), while the 32 B arm
is effectively flat (`-.0002`). No held-out improvement is therefore
statistically established by this single split.

## Evidence status

* **confirmed:** the score-aware block ADC scorer is executable, provenance
  bound to the canonical candidate receipt, and has a distinct 120/32 query
  split;
* **bounded negative:** the tested 4-bit-per-block additive codebooks do not
  match direct INT8 or direct RSLM3 on this frozen shell;
* **not tested:** pairwise-gradient codebook learning, learned query-side
  transforms beyond the covariance weighting, native SIMD timing, persistent
  side-code materialization, and held-out-domain replay.

One discarded run used the 144-byte THQ4 layout from the full-scan experiment
with a 96-byte row stride. Its near-random result was rejected before evidence
publication. The accepted run is bound to `thq4-ordinal.u8`, SHA
`0a0c825720bccef97a0fd1af5c7727671b5e0a79be09b643e0fcb558236e2b70`, which is
the layout used by the canonical score gate.

## Discriminating next step

Do not add native kernels yet. The remaining algorithmic test is a genuinely
pairwise-trained ADC on the same shell: use teacher score differences inside
the THQ4 top-128 candidates, keep the 8/16/32 B budgets fixed, and require a
held-out paired bootstrap delta against direct INT8 before considering any
materialization.

Committed evidence:

* `2026-09-18-thq-learned-adc-gate.compact.json`;
* `2026-09-18-thq-learned-adc-gate.receipt.json`;
* `tmp/thq-learned-adc-gate.audit.json` (local audit output).
