# Historical research provenance closure

Date: 2026-10-14
Repository: `LimiNode/agent-memory-cpp`
Verified `main`: `bfa8f19067773da55a1a8964a25ead04fd1cdc4e`

This document is the navigation index and gap ledger for the historical
research preservation work. It does not alter historical experiment notes,
source bytes, benchmark receipts, datasets, or qrels.

## Preservation index

| Scope | Canonical record | Provenance result |
| --- | --- | --- |
| 13 historical documents and 17 research tools | [PR #503](https://github.com/LimiNode/agent-memory-cpp/pull/503), merged as part of the preservation history | Exact source Git blobs are recorded in the [preservation manifest](2026-10-13-historical-research-preservation-manifest.json). The material is historical evidence, not a current routing recommendation. |
| 34 divergent research paths | [PR #506](https://github.com/LimiNode/agent-memory-cpp/pull/506), merged at `c25cf5ae7b4646d3a4d501ddba7caa5dcce3abd0` | 28 source blobs are reachable in `main` history; 6 divergent blobs are preserved under explicit historical paths. |
| 14 paths absent from the target tree | [PCA12 residual addendum](2026-10-13-pca12-residual-preservation-addendum.md) and [divergent-path audit](2026-10-13-pca12-divergent-path-audit.csv) | Exact source blobs and byte sizes are recorded. The files are not connected to CMake, CI, or runtime execution. |
| Validation gates | [`validate-historical-research-preservation.py`](../../tools/validate-historical-research-preservation.py) and [`validate-pca12-residual-preservation.py`](../../tools/validate-pca12-residual-preservation.py) | Fail-closed validators and negative self-tests run successfully in CI on the merged `main`. |

The current `main` also contains the documentation closures from PRs #504 and
#505. Their merge commits and successful post-merge CI are part of the current
repository state, but they do not change the historical source bindings above.

## Provenance gap ledger

| Gap class | Current evidence | Status | Next action |
| --- | --- | --- | --- |
| Historical document bytes | 13/13 source blobs verified against the source commit recorded by #503 | `BYTE_PRESERVED` | None for the documents themselves. |
| Historical research material | 17/17 #503 tools plus 20 residual #506 bindings (9 documents and 11 tools) are recorded by path and SHA-1 | `SOURCE_PRESERVED` | Keep the preservation paths stable; do not connect them to active builds without a separate decision. |
| Divergent paths | 34 audited; 28 reachable in `main` history and 6 retained under explicit preservation paths | `AUDITED` | Re-run only if the source or preservation paths change. |
| Absent paths | 14 source paths retained in the #506 scope | `BYTE_PRESERVED` | No reconstruction is needed. |
| Referenced raw receipts | 11 documents reference receipts marked `REFERENCED_NOT_AVAILABLE` in the #503 manifest | `RAW_RECEIPT_GAP` | Locate an independently verifiable copy, or leave the receipt unavailable. Never infer it from prose. |
| Unreferenced raw receipts | 2 documents are marked `NOT_REFERENCED` in the source manifest | `RAW_RECEIPT_NOT_REFERENCED_IN_SOURCE` | This records only that the source note has no receipt reference; whether independent evidence is needed remains a separate audit decision. |
| Residual raw artifacts | The #506 addendum records `NOT_AUDITED_IN_THIS_PASS`; datasets, qrels, and benchmark outputs were not recreated | `INDEPENDENT_AUDIT_PENDING` | Hash any available local copy and create an independent durable copy before declaring it preserved. |
| Scientific replay | Source code and notes are preserved, but missing inputs/receipts prevent a general replay claim | `NOT_REPRODUCIBLE_FROM_REPOSITORY_ALONE` | Keep this limitation explicit in research notes and release descriptions. |

The complete path-level residual inventory remains in
[`2026-10-13-pca12-divergent-path-audit.csv`](2026-10-13-pca12-divergent-path-audit.csv).
The 13 document-level receipt declarations remain in
[`2026-10-13-historical-research-preservation-manifest.json`](2026-10-13-historical-research-preservation-manifest.json).

The `RESIDUAL_PRESERVATION_PENDING_REVIEW` status in the older PCA12 addendum
is a pre-merge historical status. The current Git-byte preservation in #506 has
been checked; external raw artifacts and independent scientific replay remain
open gaps and are not upgraded by this ledger.

## Branch and archive policy

- `agent/pca12-routing-followups` remains a source witness at
  `f325d794afacf86d79cbabb000a400658eafd5af`.
- The source witness and any related worktree must not be removed solely
  because the preservation PRs have merged.
- Remote branch deletion is tracked as a separate operational event. No
  preservation branch is authorized for deletion by this ledger alone.
- Local research archives, bundles, NPZ/qrels payloads, and receipts remain
  outside this Git closure until their bytes and durable copies are audited.

## Interpretation boundary

`BYTE_PRESERVED` and `SOURCE_PRESERVED` mean that the declared historical
Git content can be recovered and checked by hash. They do not mean that the
original datasets, qrels, raw benchmark receipts, or execution environment
are available. No historical result in this ledger is a current product or
routing recommendation.
