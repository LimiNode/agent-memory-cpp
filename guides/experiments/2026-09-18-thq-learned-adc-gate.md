# THQ score-weighted block/PQ-like ADC gate

Date: 2026-09-18
Branch: `research/thq-score-codecs`
Scope: canonical frozen 152-query semantic R4 candidate shell, 1M DE-1M rows.

## Question

Can a compact document side-code preserve the `q·x` ordering without storing or
reconstructing a full FP32 document vector? This gate tests a score-weighted
block/PQ-like ADC with one symbol per disjoint block. Codebooks are fitted and
assigned in the same query-weighted Mahalanobis space from detached 25k training
vectors and the first 120 query rows. Queries 120--151 are held out. This is
not a full additive-quantization/AQ implementation.

Scoring uses per-block query LUTs and analytic norm terms. The runner does not
materialize a stored 384-dimensional approximation. This is a NumPy quality
gate, not native latency, persistence, or page-accounting evidence.

## Corrected results

The first receipt used Euclidean symbol assignment after Mahalanobis training;
those numbers are superseded. The corrected replay uses the fitted transform
for assignment and evaluates both the full ~5k shell and the production-shaped
`THQ4 interval² top128 → ADC` stage. The non-norm stage-local top-10 lists are
identical to their full-shell counterparts for all 152 queries.

Held-out (queries 120--151) results by equal side-code budget:

| side budget | 2 bit/block | 4 bit/block | 8 bit/block |
| --- | ---: | ---: | ---: |
| 8 B: nDCG@10 | .6928 | .6739 | .6902 |
| 16 B: nDCG@10 | .6941 | .6790 | .6850 |
| 32 B: nDCG@10 | .6983 | .6800 | .6794 |

For the 32 B rows, held-out candidate-FP32 overlap is `.8969`, `.9000`, and
`.9062` (2/4/8 bits). Top-10-boundary pairwise accuracy in stage-local mode is
`.6406`, `.6927`, and `.6667`. There is no monotone rate frontier: bit depth
and block count trade off at fixed bytes. The 32 B/2-bit arm has held-out
nDCG@10 `.698282`, versus reconstructed direct INT8 `.684879` (mean delta
`+.013403`, bootstrap CI95 `[-.002125, +.033602]`, worst query `-.032532`)
and reconstructed RSLM3 `.686792` (mean delta `+.011490`, CI95
`[-.000348, +.029352]`, worst query `-.032532`). It is therefore an
inconclusive survivor, not a confirmed improvement: both bootstrap intervals
include zero. The other tested arms do not establish a quality win.

The corresponding full-shell and stage-local non-norm top-10 lists are exactly
equal for all 152 queries. This is the required production-shaped check, not an
assumption that the full shell and top128 task are interchangeable.

The 2-byte control stores the exact source-document FP16 norm as denominator.
It is a negative control for this ADC only; it does not establish that learned
or jointly fitted norm side information is useless.

## Evidence status

* **confirmed:** transform-consistent Mahalanobis assignment, rate-matched
  2/4/8-bit grid, top-aware pairwise diagnostics, and stage-local top128 replay
  with SHA-bound raw/compact evidence;
* **inconclusive:** 32 B/2-bit has the highest held-out qrels nDCG in this
  grid, but its paired bootstrap intervals include zero and its teacher and
  candidate-FP32 fidelity remain imperfect;
* **bounded negative:** the remaining block/PQ-like ADC arms do not establish
  a win over direct INT8 or direct RSLM3 on the frozen shell;
* **not tested:** pairwise/listwise-trained codebooks, AVQ/Distill-VQ-style
  retrieval objective, score-aware rotation/grouping, faithful RSLM, full AQ or
  QINCo-like codebooks, native SIMD timing, persistent side-code materialization,
  and held-out-domain replay. The diagnostic pairwise metric treats an
  approximate tie as correct; it is not a top-10 retrieval proof and should be
  replaced by deterministic score/doc-ID tie ordering in a future replay.

The input binding is retained in the compact evidence (`documents`, `training`,
`queries`, qrels, teacher IDs, candidate flat/raw/receipt hashes). The accepted
THQ4 layout is the canonical 96-byte ordinal materialization; the discarded
144-byte-layout/96-byte-stride run remains excluded.

## Discriminating next step

Do not add native kernels yet. The next algorithmic gate is a genuinely
pairwise/listwise-trained scorer inside the THQ4 top-128 shell, concentrating
teacher score differences near the top-10 boundary at the same rate-matched
budgets. Require a held-out paired bootstrap delta against direct INT8 before
considering materialization. A faithful RSLM2/3/4 replay is a parallel control,
not evidence that this block ADC is additive quantization.

Committed evidence:

* `2026-09-18-thq-learned-adc-gate.compact.json`;
* `2026-09-18-thq-learned-adc-gate.receipt.json`;
* `tmp/thq-learned-adc-gate-corrected.audit.json` (local audit output).
