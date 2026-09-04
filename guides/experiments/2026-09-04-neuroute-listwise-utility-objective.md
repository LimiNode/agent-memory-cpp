# NeuRoute rank-weighted listwise objective

Date: 2026-09-04. This is a follow-up to PR #285. The asymmetric prototype
codebook improved prototype recall, but its prototype-to-document replay failed
the address utility gate. This experiment tests whether replacing independent
pairwise constraints with a rank-weighted listwise objective improves utility
without changing the encoder, code widths, split, or retrieval protocol.

## Hypothesis and scope

The target is a sampled softmax over teacher positives at ranks
`[0, 1, 7, 31, 127, 255, 511, 1023]`, fixed teacher negatives, corpus-random
negatives, and an optional Hamming hard-negative round. Positive probability is
weighted by `exp(-rank / 256)`. This is a rank-utility proxy, not a true
document-posting loss: the 8,141-query teacher cache has no per-query document
labels. Therefore every candidate must still pass prototype→document dedup
before local K8 or full R4 integration is considered.

## Reproduction

Use `run-neuroute-asymmetric-prototype-map.py` with
`--objective rank_weighted_listwise`, the frozen PR #284 source and teacher
cache, widths `32 64 128`, seeds `285 286 287`, three epochs plus one hard
round. Raw JSON and model/code artifacts stay under `tmp/`.

## Results

The listwise frontier collapsed to near-random held-out prototype recall. Mean
internal recall over the three seeds was:

| width | recall@1024 | recall@2048 | recall@4096 | recall@8192 |
| ---: | ---: | ---: | ---: | ---: |
| 32 | 0.00142 | 0.00347 | 0.00734 | 0.0130 |
| 64 | 0.00184 | 0.00374 | 0.00733 | 0.0152 |
| 128 | 0.00142 | 0.00324 | 0.00664 | 0.0130 |

The best 128-bit cell remains far below corrected #284 (0.17592 at 128 bits)
and PR #285 asymmetric pairwise (0.32840 mean at 128 bits). Code entropy was
1.0, so the failure is ranking/geometry rather than an obvious constant-bit
collapse. The harness now also emits rank-weighted missed-utility statistics;
the frontier JSON above was generated before that additive diagnostic field and
the conclusion does not depend on it.

The address replay confirms the failure:

| prototype budget | prototype recall | candidate docs | final top10 overlap |
| ---: | ---: | ---: | ---: |
| 1,024 | 0.00184 | 15,411 | 0.01118 |
| 2,048 | 0.00418 | 30,346 | 0.03092 |
| 4,096 | 0.00836 | 60,686 | 0.06776 |
| 8,192 | 0.01529 | 117,084 | 0.12434 |

All four budgets have p05 and worst-query final overlap equal to zero. These
directional values are sufficient to reject native integration.

## Decision rule

Prototype recall alone is not a product gate. A cell is eligible for native
local-K8/full R4 replay only if prototype→document dedup has non-zero worst
query survival and meets the established final-overlap gate. If listwise
training fails that gate, stop width expansion and move to a genuine
document-utility or alternating discrete-code objective.

## Limitations

The loss is sampled and uses a smooth sigmoid Hamming surrogate; it is not an
absolute capacity ceiling. Three seeds and one fixed train/held-out split are
directional evidence. Exhaustive prototype Hamming scans remain offline-only.

Raw artifact hashes (files remain uncommitted under `tmp/`):

* frontier JSON: `a0353a6e0db2293f288b8c82cb848110912b290ce098e015d02120e450a1ea9b`;
* 128-bit codes: `9f745f4086ba23b11e554072bd16289c3e4d7ddfc101727a824f2ccf49456590`;
* 128-bit model: `fc7b51d5c15ec2f80572a423fda478bcb077b33376fe4ed51e6dd87e73200216`.
