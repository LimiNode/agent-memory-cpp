# Correction: R4 mixed INT5 physical footprint

## Scope

This corrective follow-up fixes the accounting in PR #253. The mixed layout
uses an external `mixed-address-byte-offsets.u64le` directory, but the original
`full_physical_footprint_bytes` field counted only the variable-record file.

## Correction

The materializer now includes the offset directory in the mixed layout's full
physical footprint. The original #253 result and its approximately 33% saving
remain historical evidence; corrected replay must report the directory bytes
explicitly and derive the updated ratio from the corrected manifest.

This changes accounting only. It does not change document order, codec bytes,
query results, latency samples, or the conditional resident/working-set
interpretation. The mixed layout remains a research selection only under the
locked Windows 256 MiB pressure condition; no runtime activation is implied.

## Status

`CORRECTED`: supersedes the #253 footprint field while preserving the original
merge commit and measured artifacts for provenance.
