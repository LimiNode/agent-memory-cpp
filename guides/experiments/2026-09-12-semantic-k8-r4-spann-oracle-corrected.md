# Corrected semantic posting oracle

Date: 2026-09-12. This corrective replay supersedes the initial #382 receipt
(`2026-09-12-semantic-k8-r4-spann-oracle-result.json`) as authoritative evidence.
The initial run was a hybrid Euclidean-trained/raw-dot control and was
mislabelled as cosine K-means; its values remain preserved for history only.

The corrected runner measures two explicitly named arms on the same frozen
DE-1M fixture: (1) Euclidean MiniBatchKMeans with the matching
`x·c - 0.5||c||²` assignment/probe score, and (2) a normalized spherical
control using unit document/query vectors and normalized centroids with cosine
dot-product routing. The latter is a normalized MiniBatchKMeans approximation,
not a claim of a production spherical index.

The compact receipt is
`2026-09-12-semantic-k8-r4-spann-oracle-corrected-result.json`; optional
per-query rows are an external raw artifact. Timing is split into `fit_ms`,
`full_assignment_ms`, `posting_build_ms`, `query_generation_ms`, and
`total_experiment_ms`. Index replication is reported as `N*r` posting entries;
query read cost is separately reported as posting entries touched and its
fraction of the index. Teacher IDs are evaluation-only.

The fixed-`nprobe` sweep remains a generator oracle, not a hard candidate-budget
experiment. The receipt includes mean/p05/p50/p95/max candidate and posting
statistics, teacher-cell-rank diagnostics, worst-query recall aggregates, and
the first-eight query IDs for matched comparison with the raw-coordinate
control. No THQ rerank, MDBX page claim, R4/K8/K32 implementation, or production
activation is implied. Candidate-budget snapshots (5k/10k/20k/50k/100k), larger
K (`1024/2048/4096`), seed/sample convergence, and a verified R4/K8/K32 mapping
are the next experiments after review of this correction.

## K=512 corrected replay

The 152-query replay used `sample_size=100000`, `seed=20260912`,
`max_iter=20`, and the fixed `nprobe={1,2,4,8,16,32}` matrix. The compact
receipt has 36 aggregate rows (two arms × three replication values × six
probe values); the 907,266-byte per-query artifact is external and has SHA-256
`7692d068f00b90c90acb8efd147c14a614ead4ca6dd8bd156d5c707c2e18a393`.

| arm | r | nprobe | mean candidates | mean recall | p05 recall | worst-query floor |
|---|---:|---:|---:|---:|---:|---:|
| L2 | 4 | 4 | 42,303 | .8579 | .455 | .10 |
| L2 | 4 | 8 | 81,641 | .9362 | .700 | .40 |
| L2 | 4 | 32 | 263,910 | .9855 | .900 | .80 |
| spherical-normalized | 4 | 4 | 41,590 | .8559 | .455 | .10 |
| spherical-normalized | 4 | 8 | 79,918 | .9362 | .700 | .40 |
| spherical-normalized | 4 | 32 | 259,347 | .9855 | .900 | .80 |

The `worst-query floor` is computed from the external per-query rows (the
compact receipt intentionally stores p05/p50/p95/max aggregates). These
corrected K=512 controls therefore still do not pass a `.995 @ 50k`
generator gate, and spherical normalization does not materially change this
coarse partition. This is a bounded result for this seed, sample, K, and
training budget—not a universal negative for semantic routing.
