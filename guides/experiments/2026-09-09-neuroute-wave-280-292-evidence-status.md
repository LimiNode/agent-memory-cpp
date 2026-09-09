# NeuRoute wave #280--#292 Evidence status

Date: 2026-09-09

The clean-main merge wave #280--#292 is fully landed through merge `c91d3f7`.
Every PR was reviewed for scope/methodology and passed the complete dual CI
matrix before merge. The correction chain #285/#286 → #287 and the complete
#289 reference lineage are preserved in Git history.

No public Evidence Release is claimed for this wave yet. The available local
workspace contains the experiment notes and selected raw/cache artifacts, but
does not contain compact `evidence.json`/`result.json` receipts for all measured
members. In accordance with the archive policy, a release must wait until
receipts are regenerated or located and pass deterministic validation. Raw
DE-1M stores and generated databases remain excluded.

The earlier lineage archive is complete and public:
`evidence/neuroute-lineage-wave-267-275-v1`.

Next evidence action: regenerate compact receipts for #280--#292 (including
the corrected #287 and #290 heads), build a deterministic archive, validate it,
and publish a separate evidence release whose target is the measured head.
