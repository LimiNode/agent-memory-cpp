# NeuRoute dense wave #258--#266 Evidence archive

Date: 2026-09-09

## Scope

This archive preserves the compact result and evidence receipts for the landed
dense-path research from PR #258 through PR #266. It does not contain raw
DE-1M stores, generated databases, native executables, or large timing dumps.

The measured head is the landed PR #266 head
`efae643db29fc9d09775a1fbe7460b17bb79571f`. Later merge commits and timeline
updates do not change the archived result bytes.

## Interpretation ledger

| PR | Status | Bounded interpretation |
| --- | --- | --- |
| #258 | CONFIRMED/NEGATIVE | The selected nonlinear INT5 routing kernel is conditional on memory pressure; nonlinear final-document INT5 did not replace uniform INT5. |
| #259 | CONFIRMED | The query-path audit preserves ranking identity and removes benchmark-only work from the total timer. |
| #260 | CONFIRMED | The fused final-rerank implementation preserves identity, but further top-64 optimization cannot materially move full R4 latency. |
| #261 | CONFIRMED | Persisted codec bytes are execution-independent; portable C++17 remains the safe default. |
| #262 | CORRECTED/CONFIRMED | Full R4 includes the dominant global K8 cost; earlier post-shortlist timing is not full end-to-end latency. |
| #263 | CONFIRMED CONDITIONAL CLOSURE | The dense policy is closed only for the current exact-K8 design and explicit reopen conditions remain. |
| #264 | DIAGNOSTIC/NOT PRODUCTION LICENSED | Actual-R4 final-codec reselection produced a candidate for new confirmation, not a production default. |
| #265 | ACTIVE PHYSICAL FOLLOW-UP/NOT PRODUCTION LICENSED | The representative-codec candidate is licensed for physical validation only. |
| #266 | CONFIRMED TESTED IMPLEMENTATION CEILING | K8/K32 codec and prefilter conclusions apply to the tested implementations; exact FP32 K8 remains the fallback. |

## Publication

The release tag, archive SHA-256, and internal bundle-root SHA-256 are recorded
here after deterministic archive validation and public Evidence publication.

- Tag: `evidence/neuroute-dense-wave-258-266-v1`
- Release: [Evidence release `evidence/neuroute-dense-wave-258-266-v1`](https://github.com/LimiNode/agent-memory-cpp/releases/tag/evidence/neuroute-dense-wave-258-266-v1)
- Archive SHA-256: `84bb44712850bac66411040fffbb33a31ca4eec1b74b1a6fd1bc038adf6c8ea9`
- Bundle-root SHA-256: `7f3883ffd82a4016da9c7e66eb90d46977616e4a913f66d0f17b0dfe780939e4`

The deterministic builder and validator are
`tools/agent-memory-bench/archive-neuroute-dense-wave-evidence.py`.
