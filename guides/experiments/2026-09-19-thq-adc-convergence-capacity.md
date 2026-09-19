# THQ4 ADC convergence and capacity gate

Date: 2026-09-19
Branch: `research/thq-score-codecs`
Question: does the 32 B learned ADC result improve with a more serious fit, or
does additional fixed-rate capacity help under the same THQ4 candidate shell?

## Protocol

This is a NumPy reference-quality replay on the canonical frozen 152-query,
R4-fused candidate shell. The first 120 queries fit the query-weighted
Mahalanobis transform; queries 120--151 are held out. The `32B-*current`
controls reproduce the previous runner's 8,192-sample, five-iteration,
single-seed Lloyd initialization exactly. The convergence controls use a full
25,000-row detached training sample (or 8,192 for the expensive 8-bit control),
more iterations, and multiple restarts. Capacity controls keep the scorer and
shell fixed while changing only block geometry/rate:

| arm | geometry | fit |
| --- | --- | --- |
| 32B-2bit-current | 128 × 3D × 2 bit | 8,192 / 5 / 1 |
| 32B-2bit-converged | 128 × 3D × 2 bit | 25,000 / 25 / 4 |
| 32B-8bit-current | 32 × 12D × 8 bit | 8,192 / 5 / 1 |
| 32B-8bit-restart | 32 × 12D × 8 bit | 8,192 / 12 / 3 |
| 48B-3bit-128x3D | 128 × 3D × 3 bit | 25,000 / 20 / 3 |
| 48B-2bit-192x2D | 192 × 2D × 2 bit | 25,000 / 20 / 3 |
| 64B-4bit-128x3D | 128 × 3D × 4 bit | 25,000 / 20 / 3 |

The fit objective is reported per block, together with empty-cluster counts.
It is not treated as a retrieval objective.

## Held-out result

The table reports stage-local THQ4-top128 qrels nDCG@10, candidate-FP32
top-10 overlap, and top-10-boundary pairwise accuracy:

| arm | nDCG@10 | FP32 overlap | boundary pairwise |
| --- | ---: | ---: | ---: |
| 32B-2bit-current | .698282 | .896875 | .640625 |
| 32B-2bit-converged | .685667 | .887500 | .635417 |
| 32B-8bit-current | .679356 | .906250 | .666667 |
| 32B-8bit-restart | .677338 | .903125 | .692708 |
| 48B-3bit-128x3D | .677920 | .900000 | .661458 |
| 48B-2bit-192x2D | .664344 | .909375 | .687500 |
| 64B-4bit-128x3D | .674057 | .909375 | .697917 |

All codebooks had zero empty-cluster blocks. The convergence fit reduced the
reported Mahalanobis quantization objective for 32B/2-bit from
`3.0767e-5` to `2.9567e-5`, yet qrels nDCG fell by `.012615`. The 8-bit
restart control likewise improved its fit objective (`5.3594e-5` to
`5.2495e-5`) without improving qrels (`.679356` to `.677338`).

## Interpretation

This is a confirmed bounded result, not a production selection:

* The earlier 32B/2-bit `.698282` result is reproducible when its exact fit
  initialization is retained.
* More Lloyd effort and restarts do not improve held-out qrels on this split;
  optimizing the current Mahalanobis reconstruction objective is not aligned
  with the retrieval objective.
* Neither 48 B geometry nor 64 B/4-bit improves the 32B/2-bit arm here. The
  finer 192×2D locality control is especially negative (`.664344`), so simply
  shortening blocks is not sufficient.
* Higher candidate-FP32 overlap and boundary pairwise accuracy do not imply
  higher qrels nDCG. This is another reason not to select a codec from teacher
  fidelity alone.

The result does **not** prove that 48/64 B is useless: the replay uses one
query split, one objective family, no cross-fitting, and no learned
cutoff-aware scorer. It does show that capacity alone, under this scorer and
training objective, is not the missing ingredient.

## Evidence and next gate

Raw output (kept locally for reproducibility):
`tmp/thq-adc-convergence-capacity.json`, SHA-256
`fb688e4b8cc908358099e90d50c33b1ae709d361477771f3ab5c0e85a3a197d1`.
Runner SHA-256:
`d2080bf314d0b7eb0110301f0878deea64a896d164245e9529ba4f05e0606151`.

Before any materialization, the next discriminating experiment is four-fold
query cross-fitting for the surviving 32B/2-bit arm and the two capacity
controls with the best boundary metrics. Only if an out-of-fold paired qrels
delta survives should we spend effort on cutoff-aware pairwise/listwise
training. Native kernels, persistent layout, and larger sweeps remain
deferred.
