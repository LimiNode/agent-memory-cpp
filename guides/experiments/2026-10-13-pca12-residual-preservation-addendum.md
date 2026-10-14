# PCA12 residual preservation addendum

Status: `RESIDUAL_PRESERVATION_PENDING_REVIEW`

Source repository: `LimiNode/agent-memory-cpp`

Source branch: `agent/pca12-routing-followups`

Source commit: `f325d794afacf86d79cbabb000a400658eafd5af`

Corrective base: `32f2278251b54967e84bbc0f61cd80b3f0a67576`

This addendum is separate from the original #503 manifest. It preserves the
exact historical source bytes that are not present in the #503 scope and are
not otherwise safely represented by the current `main` tree.

## Scope

The expanded audit compares only research catalogs (`guides/experiments/` and
`tools/agent-memory-bench/`) between the source commit and current `main`.
It found 34 paths present in both trees with different blob IDs:

- 28 source blobs are reachable from `main` history under a supporting commit;
- 6 source blobs are not reachable from `main` history and are additional
  preservation gaps.

The six unreachable divergent blobs, plus the 14 paths absent from `main`, are
the 20 bindings preserved by this corrective branch. For the six files whose
paths already exist in `main`, the exact old bytes live under the explicit
`archived/` or `tools/historical-research/` preservation paths recorded in the
manifest; the active `main` paths are not overwritten. The machine-readable
details, current-main blob IDs, reachability evidence, and exact categories are
in [2026-10-13-pca12-divergent-path-audit.csv](2026-10-13-pca12-divergent-path-audit.csv).

The audit keeps `source_blob_commit` separate from `replacement_commit`:
the former is a witness commit in `main` history where the historical blob
occurs at the original path, while the source commit itself independently
binds the exact historical bytes and the latter proves the current `main`
replacement. In particular,
the two disputed documents remain reachable evidence, not residual gaps:
`fed6bdf9cd68c843949d7af3005e451847b1e696` contains the historical
NeuRoute-v2 document and `b281590c682f7612b2caa5775b2104c072908582` contains
the shared learned semantic-address document. Their replacement commits are
recorded independently in the CSV.

The addendum manifest in this branch records all 20 source blob SHA-1 values
and byte sizes. Historical documents and tools are not current routing
recommendations and are not connected to CMake, CI, or runtime execution.

## Receipt policy

No raw receipt, dataset, qrels payload, or benchmark result is reconstructed.
Document receipt status is explicitly `NOT_AUDITED_IN_THIS_PASS`; tools use
`NOT_APPLICABLE`. Any future receipt preservation must be a separate,
content-addressed step.
