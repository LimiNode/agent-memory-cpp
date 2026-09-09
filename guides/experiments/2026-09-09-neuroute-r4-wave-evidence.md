# Evidence archive: NeuRoute R4 wave #244--#257

## Scope

This archive closes the Evidence backlog for the fourteen landed NeuRoute R4
research PRs `#244--#257`. It contains the frozen experiment notes and compact
`result.json`/`evidence.json` receipts (or the named equivalent for the final
INT5 closure). Raw DE-1M stores, generated databases, and large query dumps are
not copied into Git or the release asset.

## Validation

`archive-neuroute-r4-wave-evidence.py` performs a fail-closed inventory:

- every PR has an evidence receipt, a compact result receipt, and its note;
- each declared `result_sha256` matches the archived result;
- no receipt explicitly reports failure;
- the ZIP is deterministic (a second build produced the same SHA-256).

The archive was built from the replication materialization tree at measured
head `a82a7e2a97181f68c6e82ce416c4b264ada9e0d0`, which includes corrective PR
#312's mixed-INT5 footprint accounting. The original #253 measurements and
merge SHA remain unchanged; the correction is interpretive/accounting only.

## Release

[Evidence release `evidence/neuroute-r4-wave-244-257-v1`](https://github.com/LimiNode/agent-memory-cpp/releases/tag/evidence/neuroute-r4-wave-244-257-v1)

- Archive SHA-256: `2f3a63cafcc945b9cdc110760302df9a859e290fd20662938f913edb38319703`
- Bundle-root SHA-256: `bd3646791832b325d02be23085448ccd12499bce4bdfa0cd032a6522d1f35176`
- Members: 14 PR-scoped receipt groups

This release is archival evidence, not a production activation or codec policy
change.
