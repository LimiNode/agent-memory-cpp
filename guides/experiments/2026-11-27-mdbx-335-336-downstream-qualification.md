# MDBX #335/#336 downstream corrective qualification

- Date: 2026-11-27. Status: qualification in progress; no merge or activation.
- Base: `agent-memory-cpp@8a092d515f99ed34e50ffbc7642cd2fb80e4ef8d`.
- Dependency pin: `mdbx-containers@b28ce3a969ccde427e699cbb3ece74d35acc08c5`.
- Owner: [dependency policy](../dependencies.md), [historical installed
  consumer](../index-lifecycle-roadmap.md) and [durable conversation
  contract](../conversation-durable-physical-contract.md).

## Question and exact change

Does the merged pin preserve the existing MSVC bulk-retrieval fix and remove
the reproduced successful-diagnostic allocations without weakening coordinated
admission, rollback or uncertain-outcome retry?

Merge receipts are #335 `dc814252ad0c85fc5542ac211ec70f7033760394` and #336
`b28ce3a969ccde427e699cbb3ece74d35acc08c5`. The latter contains original #335
HEAD `c2a2326273a3d85f29283e2cd34942c7b8d72cbd` as an ancestor. Its only
production changes against common base `4ab32b5e...` are concrete-container
`db_load` deduction, lazy `check_mdbx(int, const char*)`, and stack-owned
`Transaction pending` before the manual guard/control-block allocation.
Regression additions cover bulk retrieval and diagnostic/transaction allocation
with sync ON/OFF. `external/libmdbx` remains
`fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f`.

## Historical-consumer guard

The first #705 packaging run (HEAD `59aaa817e30651da0e9f493002a97e97bbd63f35`,
[run 45908535999](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45908535999))
refused before building the historical consumer: the exact external-tree pair
was not audited. This is the intended fail-closed dependency guard, not a
detected persistence incompatibility.

The new directional audit is strictly historical OLD tree
`b949d6822582edc1f445837442b91aabf5093ac6` ->
`f5679c6faf286af65e637301aed320c4e433f918`, with only the exact
`mdbx-containers` gitlink changed. It does not admit arbitrary dependency
updates, altered libmdbx, reversed/incremental transitions or added entries.
`tools/test-exact-generation-upgrade.py` retains old controls and adds explicit
merged-pin controls. Historical replay remains `HISTORICAL_ONLY`, never
numeric migration/activation evidence.

## Observed and outstanding checks

At the initial dependency HEAD, [CI 45908536006](https://github.com/LimiNode/agent-memory-cpp/actions/runs/45908536006)
passed its Linux MDBX lane (116/116 CTests), native MSVC Release/Debug lanes,
MDBX-off platform lanes and CI gate. Packaging installed MDBX/minimal consumers
passed; the historical guard refusal above kept that workflow unsuccessful.
The MSVC Debug allocation-injection suites intentionally skip unsafe checked-STL
injections; a passing Debug job is not evidence that those injections ran.

Exact final-head CI/Packaging, direct allocation counts, GNU local durable
fault/process/concurrency repetition and Linux ASan/UBSan are pending. Existing
tests retain before/after-state validation and idempotent retry even if this
particular post-native-commit allocation disappears. Lost acknowledgment,
process termination, native device errors and power loss remain distinct.
No native device/power-loss result, database migration, valuable-v1-data
inventory, production activation or full bundled-dependency MSVC claim is
made by this corrective pin.
