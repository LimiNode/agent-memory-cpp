# Next score-codec research wave

Date: 2026-09-19
Status: `PLANNED`

The corrected ADC evidence is now a bounded negative/neutral result, not a
license to choose ADC48. The current compact RSLM row is also explicitly only
an RSLM-like FWHT/Lloyd-Max control. The next wave therefore separates two
questions instead of mixing them:

1. Can a faithful, persistable RSLM implementation beat the current INT8
   quality/footprint point?
2. Does side information that is conditional on the THQ4 pattern preserve
   useful residual information more efficiently than a global ADC codebook?

## Gate A — faithful RSLM oracle

Implement the public reference mechanics before any native work: official
two-pass randomized FWHT, Gaussian scaling, Ue7m9 scale, RSLM4/3 one-dimensional
Lloyd-Max, and the joint 2D/4D construction for RSLM2/1. The offline path must
materialize `THQ4 base + side code`; the query path must not access FP32
documents. Require score parity between materialized direct scoring and the
offline reconstruction oracle, then run the same shuffled 152-query
production-shaped gate for RSLM2/3/4.

The existing `RSLM-like` values remain bounded controls until this gate passes.

## Gate B — THQ-pattern-conditioned residual

Start with a zero-side-information THQ centroid baseline, then fit conditional
residual codebooks keyed by each 3D THQ pattern. Compare 2-bit and 3-bit symbols
(32 B and 48 B side budgets) against global ADC48, INT8 and faithful RSLM. The
codebook fit must be document-only or fit-query-only within each OOF fold; query
teacher scores may be used only for a declared retrieval-aware follow-up.

The scorer should operate directly on `query + THQ base + side code`; a full
384D reconstruction is an audit oracle, not the production requirement.

## Gate C — independent residual controls

Run a 48 B residual RaBitQ/TurboQuant-style control with its scale/correlation
correction, and a reconstruction-scale/NEQ control. Keep the implementations
clearly labelled as faithful or local references. Compare candidate-FP32
overlap, qrels nDCG, worst-query loss, and score bias; do not infer MIPS quality
from reconstruction MSE alone.

Only if a classical conditional codec survives Gates A–C should we spend effort
on retrieval-aware training (AVQ/AAQ/Distill-VQ/QINCo-like). That stage needs a
substantially larger independent query pool, repeated shuffled OOF, and a
properly normalized cosine/IP objective. The current 114 fit queries per fold
are not sufficient evidence for such a claim.

No native SIMD, persistent layout, or production selection is authorized by
this plan alone. Those follow only after a codec has a reproducible offline
materialization, direct-score parity, and a stable quality frontier.
