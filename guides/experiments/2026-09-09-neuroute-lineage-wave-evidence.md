# NeuRoute shortlist-generator lineage Evidence archive

Date: 2026-09-09

## Scope and provenance

This compact archive covers measured receipts for PRs #267--#275. It also
records canonical clean-main continuations #317--#319 as lineage-only members
because the original #277--#279 compact receipts were not retained in the
available artifact store. PR #276 was gated off and contributes no unique
measured evidence. No raw DE-1M stores, generated databases, executables, or
large timing dumps are included.

The archive is built and validated deterministically by
`tools/agent-memory-bench/archive-neuroute-lineage-wave-evidence.py`.

## Interpretation ledger

| PR | Status | Bounded interpretation |
| --- | --- | --- |
| #267 | CONFIRMED HISTORICAL RECONSTRUCTION | Historical router recipes were reconstructed on the frozen topology; byte-identical old checkpoints are not claimed. |
| #268 | CONFIRMED NEGATIVE | Fixed-budget learned routers did not pass the registered cascade gate. |
| #269 | CONFIRMED QUALITY CONTROL | Prototype IVF retained near-exact quality at M=4096, but remains a control pending native serving/footprint validation. |
| #270 | CONFIRMED NEGATIVE | Training-sufficient learned shortlist variants did not produce a product-eligible selector. |
| #271 | CONFIRMED NEGATIVE | Tested width-hierarchy/prefix replay did not pass the fixed address-budget gate. |
| #272 | CONFIRMED POLICY BAKE-OFF | Common-policy comparison found no product-eligible cheap selector; prototype ANN remains a quality control. |
| #273 | CONFIRMED NEGATIVE | Prefix-aware routing did not preserve the full cascade at M=4096. |
| #274 | CONFIRMED NEGATIVE REPRESENTATION CEILING | The tested deterministic binary prototype geometry was insufficient; this does not close all supervised hashing. |
| #275 | CONFIRMED NEGATIVE FEASIBILITY AUDIT | Required MIH radii/probe counts were too large for the tested codes; no physical MIH backend was licensed. |
| #317 | CANONICAL CLEAN CONTINUATION | Clean-main landing of the semantic-anchor replay originally proposed by #277; lineage-only in this archive. |
| #318 | CANONICAL CLEAN CONTINUATION | Clean-main landing of the selection-bias correction originally proposed by #278; lineage-only in this archive. |
| #319 | CANONICAL CLEAN CONTINUATION | Clean-main landing of the joint document/prototype binary ceiling originally proposed by #279; lineage-only in this archive. |

## Publication

- Tag: `evidence/neuroute-lineage-wave-267-275-v1`
- Release: [Evidence `evidence/neuroute-lineage-wave-267-275-v1`](https://github.com/LimiNode/agent-memory-cpp/releases/tag/evidence/neuroute-lineage-wave-267-275-v1)
- Measured head: `5d583f7b8d6dd55ceb57005257f238b87c9fe071`
- Archive SHA-256: `0b9ffc9a9fc912fe77576adb3ed3c38cecb8f8a10dac347b25085d1edfe73f8f`
- Bundle-root SHA-256: `f93f3fd70b0c14d28ad185c9c714703f0e79f6c8591d0104c62aeb6d11e447ca`
