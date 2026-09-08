# Research methodology audit and corrections

Date: 2026-09-08

This follow-up records corrections requested during review of the recent
routing and compact-code studies.  It is a methodology change log, not a new
quality claim.

## Corrections

* The `float_ivf_local_residual_k8` row in the routing architecture bake-off
  was misnamed.  The implementation scored selected documents as `x·q`; the
  written `(x-c)·q + c·q` decomposition is algebraically identical and did
  not quantize or search prototype residuals.  The row is now named
  `float_ivf_exact_document_control`.  The real residual-K8 experiment remains
  in `run-local-residual-ivf.py` and requires the frozen K8 prototype cache.
* Cosine locality now has an explicit exact-K tie contract: documents below
  the Hamming boundary are retained and the smallest document IDs fill the
  boundary shell.  Strict and tie-expanded survival plus shell size are
  recorded, so ties cannot silently change the conclusion.  A three-seed
  replay for 512-bit Gaussian and Rademacher planes is the reported control;
  the one-seed rows remain historical context only.
* The landmark-affinity runner now uses explicit `levels` and
  `thresholds_per_coordinate`.  `THQ-L` means L ordinal levels and L−1
  thermometer bits per coordinate.  The earlier `bits=3`/`bits=4` rows are
  relabelled as THQ4/THQ5, and a true THQ3 row was recomputed.
* The k-means affinity FP32 row is named `naive-affinity dot control`, not a
  universal affinity ceiling.  Cosine-normalized and Gram-whitened controls,
  and Gaussian random directions, are separate metrics/families and must not
  be folded into the negative k-means result.

## Consequence for interpretation

The corrected evidence rejects naive k-means landmark affinity but keeps
Gaussian direction-affinity as an open candidate.  Random-hyperplane
exhaustive Hamming remains a locality control, not a classical LSH index; a
future LSH study must add independent tables, multiprobe, posting/page
accounting, and cold/warm latency.  The post-#269 prototype-IVF → local K8 →
K32/R0 architecture is also still a valid quality reference, but it must be
rerun with a genuinely implemented residual tail before product comparisons.

## Reproducibility

The corrected runners are:

* `tools/agent-memory-bench/run-cosine-lsh-locality.py`;
* `tools/agent-memory-bench/evaluate-landmark-affinity-thq.py`;
* `tools/agent-memory-bench/run-neuroute-routing-architecture-bakeoff.py`.

Large raw reports stay outside Git.  The experiment notes record commands,
fixture identity, schema/version, and artifact paths for replay.
