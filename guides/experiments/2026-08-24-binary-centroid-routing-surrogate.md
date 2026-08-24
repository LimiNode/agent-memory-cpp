# Binary-centroid routing surrogate

This external calibration-only experiment follows the float semantic IVF
control. It does not train a new semantic partition: every centroid and every
document assignment is loaded byte-for-byte from the completed float-control
artifact.

For each frozen float centroid set, a deterministic seeded Rademacher projection
and sign produces 128-, 256-, or 512-bit centroid codes. A query scans all centroid
codes by Hamming distance, takes `2 x nprobe` or `4 x nprobe`, then computes
exact float inner products only for that shortlist and selects the original
`nprobe` semantic lists. The downstream ITQ-256 Hamming@768, ADC@256, and E5
rerank cascade is unchanged.

| scale | centroid counts | candidate targets | code lengths | binary shortlist multiplier |
| --- | --- | --- | --- | --- |
| Spanish 100k | 1024, 4096 | 5%, 10%, 25% | 128, 256, 512 | 2x, 4x |
| Spanish 1M | 4096, 16384 | 5%, 10%, 25% | 128, 256, 512 | 2x, 4x |

The 72 rows report recall of the exact-float control’s selected centroid lists,
the separate binary-scan and float-rerank timing distributions, candidate mass,
and the final E5 survival/nDCG. It is deliberately not a centroid HNSW, MIH,
or ADC experiment. Those optimizations would obscure the first question:
whether compact binary codes preserve enough access to the already-good
semantic partition.

The runner and archive packager may only accept a complete, validated float
semantic IVF result root. This protocol neither opens French confirmation data
nor makes Faiss a production library dependency.

The fixed `2 x nprobe` and `4 x nprobe` budgets are part of the treatment, not
an implementation hint. A binary shortlist can therefore be unable to cover
the target candidate count for one or more queries. Such a treatment is
recorded as `infeasible_target_candidate_mass`, with a complete per-query
routing audit and no downstream cascade quality claim. The runner must not
silently widen its shortlist or substitute a different candidate budget.

## 2026-08-24 result

The completed 72-row Spanish calibration matrix produced 32 feasible rows and
40 rows that could not attain their predeclared candidate mass from the fixed
binary shortlist. The latter are retained as routing-audit evidence rather than
being expanded after the fact. In particular, every 1M `K=16384` treatment and
every 1M `2 x nprobe` treatment was infeasible; the viable 1M frontier was
limited to `K=4096`, `4 x nprobe`, primarily at 256 and 512 bits.

The best feasible binary code in every candidate-mass cell was the 512-bit,
`4 x nprobe`, `K=1024` (100k) or `K=4096` (1M) treatment:

| scale | candidate mass | E5 survival after ADC | reranked nDCG@10 | frozen exact-float control survival / nDCG@10 |
| --- | ---: | ---: | ---: | ---: |
| Spanish 100k | 5% | 76.56% | 0.6791 | 84.27% / 0.7250 |
| Spanish 100k | 10% | 86.96% | 0.7377 | 90.69% / 0.7569 |
| Spanish 100k | 25% | 95.62% | 0.7799 | 95.74% / 0.7810 |
| Spanish 1M | 5% | 73.13% | 0.5992 | 82.67% / 0.6480 |
| Spanish 1M | 10% | 82.58% | 0.6440 | 87.69% / 0.6724 |
| Spanish 1M | 25% | 91.39% | 0.6837 | 92.41% / 0.6883 |

Thus a longer random-projection binary centroid code retains useful semantic
routing signal, but this surrogate does not reproduce the float control at the
low candidate budgets where a locator must be most selective. Its apparent
near-parity only occurs at 25% candidate mass, after a full centroid-code scan
and a four-times-larger exact-float centroid rerank. It is consequently a
negative result for this particular non-learned binary-centroid surrogate, not
evidence that semantic IVF itself is weak or that learned routing is ruled out.

The local deterministic evidence archive is
`tmp/binary-centroid-routing-v2-evidence.zip`, SHA-256
`8a3ebd7e933de0c24d653bfa1e47109d4c31b72ed93b13b0753e6b7a0ea916ee`.
It is intentionally untracked pending review and any separately approved
evidence-release decision.
