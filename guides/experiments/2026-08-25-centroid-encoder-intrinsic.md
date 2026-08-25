# Frozen semantic-centroid encoder frontier

This calibration-only follow-up isolates binary encoder error from the document
cascade. It keeps the frozen Spanish 1M `K=4096` float semantic-IVF centroids,
their byte-identical document assignments, and the exact float centroid oracle.

The new query bundle is MIRACL Spanish **train** queries at the pinned dataset
revision. It is E5-materialized independently and may be used to fit the
`centroids_plus_calibration_queries` ITQ variant and to select configurations.
The 648 Spanish dev queries and all downstream E5/qrels metrics are forbidden
until a configuration has been selected by this calibration protocol.

The intrinsic matrix measures mean coverage of the float top-16 centroids by
the binary top-16/32/64/128 for five encoders and 128/256/384-bit codes:

- seeded Rademacher projection/sign control;
- seeded random orthogonal rotation/sign;
- train-only PCA/sign;
- centroid-only PCA/ITQ;
- centroid-plus-calibration-query PCA/ITQ.

Strict orthogonal/PCA/ITQ transforms cannot exceed the 384 E5 dimensions.
Overcomplete 512+-bit projections are outside this matrix and are permitted
only if a non-Rademacher encoder strictly improves top-64 coverage from 256 to
384 bits. The predeclared selection gate is top-64 recall >= 0.95 and top-32
recall >= 0.85; at most three configurations proceed to end-to-end evaluation.

Exact float rerank remains part of the route: it is deliberately retained until
the binary shortlist itself is shown to cover the float centroid frontier.

## 2026-08-25 strict-dimension result

All 15 calibration rows completed against 2,162 Spanish train queries. The
best strict transform was centroid-only ITQ at 384 bits: float-top-16 coverage
was 50.66% at binary top-32, 64.88% at top-64, and 77.51% at top-128. The
centroid-plus-calibration-query ITQ variant was close but slightly lower at
top-64 (64.65%).

Both ITQ variants improved from 256 to 384 bits (60.54% to 64.88% for
centroid-only ITQ), satisfying the predeclared condition for a separate
overcomplete encoder follow-up. No strict-dimension configuration meets the
95%/85% end-to-end selection gate, so this result intentionally makes no claim
about downstream dev E5 survival or nDCG.

The local deterministic evidence archive is
`tmp/centroid-encoder-intrinsic-v1-evidence.zip`, SHA-256
`edad3119c95708101f4064270b244f9b6af7842dc32f9d1ab1bd9f142f6615e4`.
It is intentionally untracked pending review and a separately approved
evidence-release decision.
