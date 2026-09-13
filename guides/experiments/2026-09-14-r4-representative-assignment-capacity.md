# R4 representative-aware secondary assignment capacity (2026-09-14)

## Question

The raw-posting centroid gate rejected SPANN/SOAR-style assignment.  Can the
already materialized K32/document representatives provide a more meaningful
secondary-address geometry without creating a new 1M-document copy?

## Protocol

This is a teacher-only capacity oracle over the frozen R4 materialization.  For
each occupied address, the runner forms a normalized mean of the address's
existing `representative_documents` sidecar (the frozen K32/document
representatives).  It repeats the geometry diagnostic and, for every teacher
whose primary address is absent from the frozen model-ranked 1,024-address
prefix, checks whether one of the nearest `M={8,16,32,64}` representative
centroids is already visible in that prefix.  Teacher IDs are evaluation-only;
no replica, posting assignment, or serving-time route is materialized.

The runner and audit are
`tools/agent-memory-bench/run-r4-representative-assignment-capacity.py` and
`tools/agent-memory-bench/audit-r4-representative-assignment-capacity.py`.

## Results

Representative centroids are essentially indistinguishable from whole-posting
centroids:

| seed | representative-centroid rank p50 / p95 | primary/nearest ratio p50 / p95 | representative dispersion p50 |
| ---: | ---: | ---: | ---: |
| 2701 | 17,749 / 59,719 | 1.655 / 2.421 | .1084 |
| 2702 | 16,471 / 59,511 | 1.650 / 2.417 | .1080 |
| 2703 | 11,280 / 58,279 | 1.587 / 2.363 | .1084 |

Teacher-only recoverability among primary-prefix misses:

| seed | misses | M=8 | M=16 | M=32 | M=64 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 2701 | 139 | .2374 | .3381 | .4892 | .6763 |
| 2702 | 148 | .2095 | .3378 | .4932 | .7230 |
| 2703 | 152 | .3224 | .5263 | .6776 | .8487 |

The representative-aware oracle changes the result by at most one recovered
pair at M=64 and never approaches `.99`.  The K32/document representative
sidecar does not repair the missing semantic geometry.

## Decision

Do not spend a materialization wave on representative-based SPANN/SOAR closure
for the current R4 addresses.  Both raw posting centroids and existing K32
representatives point to the same conclusion: the current R4 address topology
is not a VQ-cell partition suitable for selective secondary placement.  The
remaining justified branch is a genuinely new multi-anchor/partition topology
with an explicit route-quality gate.  THQ-ADC/MDBX cascade activation remains
gated.

## Limitations and provenance

This is a teacher-leaking logical oracle, not a serving assignment or physical
replica benchmark.  It does not measure MDBX pages, latency, or storage
footprint.  Frozen manifest SHAs are the same as the preceding geometry gate:
THQ `f58e074e481dc910ca7b12b35bc27dc51097640704fb2b0c749018bcf2edd57b` and R4
`95886a3b62eb0c2fc9182b721e94a252097395edc34d7604f7d11872bb5c039c`.  The
external raw bundle SHA-256 is
`c8dd229b2b2a1aa703009b6207e323ae66069e81b3e3e311c7a0b1486bec3f11`.
