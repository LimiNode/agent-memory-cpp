# NeuRoute physical ADC benchmark activation

Date: 2026-08-28. Completed fail-closed activation audit.

## Decision

#218 produced no calibration-selected random-ADC width satisfying both the
cross-dataset mean and every-dataset quality gates. The conditional 1M physical
benchmark is therefore not activated.

The dormant benchmark protocol remains explicit for a future quality-eligible
representation: identical top-64 IDs and paired requests, full physical files,
warm-page-cache and fresh-process-first-fetch scenarios, and separate query
projection, LUT construction, random fetch, packed scoring, top-10, and total
timings. Re-projecting documents at query time and substituting synthetic timing
are forbidden.

This audit creates no physical code files, emits no timing rows, and licenses no
production selection. Its result and evidence are byte-replayable and bind the
#218 result/evidence digests.

Result SHA-256:
`bdd77ca1df13c9e2bacd88f6083abd8ae0154ad192ad24031c18c81b6d4f0786`.
Evidence SHA-256:
`bb3df462977d7fed781b370491383725e0f8d9ace137243d911706830a15f77a`.
