# Matched semantic-anchor full-cascade replay

Date: 2026-09-02. This follow-up operationalizes the relocation ceiling from
#276; it does not select a production route.

The materializer accepts only frozen document/query codes and anchor codes
already produced by the same ITQ transform.  It preserves arrays byte-for-byte
and writes a manifest with source, contract, and per-array hashes.  It refuses
to manufacture anchor codes from means or signs, since that would change the
hypothesis being tested.

When ADC arrays are present, the relocation runner extends each fixed unique
candidate budget through the frozen Hamming → ADC@64 → exact-E5 top-ten chain.
It reports ADC target survival and final top-ten overlap in addition to the
radius and posting diagnostics.  Without ADC arrays the runner remains a
geometry-only ceiling and labels final replay as unavailable.

The real run must bind the frozen configuration/internal partitions and retain
per-query rows.  No result is licensed from the synthetic self-test.

## Frozen three-seed replay

The three seed-bound inputs were materialized from the R4 layout manifest, the
current K8 FP32 records, and the de-1m native comparison input. The affine ITQ
transform was recovered from the frozen query-vector/projection pair and was
accepted only after its 305 query codes matched the persisted codes exactly.
The exact-top-ten rows came from the frozen full-E5 oracle. The runner was
executed over all 152 configuration/internal queries for each seed with the
fixed controls and budgets in the relocation contract.

| control (8 anchors, except q-global) | mean unique candidates | mean final top-10 overlap @128 | @1024 | mean r95 |
| --- | ---: | ---: | ---: | ---: |
| q-global | 1,000,000 | 0.7862 | 0.8862 | 82.04 |
| centroid seeded | 150.7 | 0.1399 | 0.1439 | 120.93 |
| prototype seeded | 139.3 | 0.3162 | 0.3178 | 72.18 |
| prototype oracle | 125.2 | 0.5866 | 0.5866 | 48.56 |

These values are a matched relocation/utility diagnostic, not a production
router result. Prototype-centred selection is materially better than centroid
selection at the same eight-anchor budget, but remains far below the global
control. The oracle gap is still large, so anchor selection rather than the
posting union alone is the limiting factor. Candidate lists are intentionally
representative-document postings from the frozen R4 layout; they must not be
read as a claim that a product query scans the full 65K-address universe.

The raw JSON outputs are retained outside Git. Reproduction requires the
materializer command, the three frozen manifests, the full-E5 oracle, and the
seed-specific output paths recorded in the accompanying manifest files.
