# Progressive THQ-ADC AoSoA replay

Date: 2026-09-11  
Fixture: frozen `thq-full-scan-v2` manifest (1,000,000 documents, 152 queries,
THQ4-384, 144 bytes/document).  Raw replay JSON files are kept outside Git in
`E:\_repoz\agent-memory-workspaces`.

The materializer produced two immutable layouts.  The runner verifies the
manifest and document-code SHA-256 bindings before scanning, uses canonical
`(score, document_id)` ordering, and fails closed on top-256 parity when that
check is requested.  Reported bytes are logical block-payload bytes, not OS or
MDBX page counters.

| layout / order | p50 ms | p95 ms | mean payload bytes | mean blocks | mean coordinate fraction | mean survival@256 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 4096×32 fixed | 6639.44 | 6861.62 | 95,840,731 | 2934.6 | 0.702746 | 1.000000 |
| 512×32 fixed | 17423.93 | 17836.62 | 93,125,558 | 22744.6 | 0.702306 | 1.000000 |
| 512×32 ADC-expected | 17335.76 | 18736.74 | 91,818,880 | 22425.1 | 0.641345 | 1.000000 |

The `512×32` page-matched layout is physically finer grained and skips more
whole tiles, but its Python per-file overhead is higher.  ADC-expected block
ordering improves logical payload and coordinate work relative to fixed order;
the p95 latency is not a production result because this runner does not model
native page caching or MDBX I/O.

Parity smoke checks passed for both layouts and both orders tested.  The full
fixed `4096×32` run was executed with parity enabled for all 152 queries; the
full `512×32` runs used the validated smoke parity gate and then measured all
152 queries without a second exhaustive pass.

## Interpretation

Progressive packed THQ-ADC plus query-dependent block ordering is a viable
logical-work reduction candidate.  It is not yet a physical MDBX result and
does not license production activation.  The next gate is native page-aware
measurement (including warm/cold controls), followed by the R4/K8/K32 candidate
cascade.

The corrected dynamic-cutoff oracle was smoke-tested on two frozen queries
with variance ordering and warmups 256/512/1024; all top-256 and cutoff parity
assertions passed after canonical-score ranking was enabled.  A direct
1M-document PQTable smoke was stopped after exceeding 20 minutes and about
7 GB RAM while constructing the multi-width state index.  This is an
execution-resource limitation, not an evidence result, and motivates a
streaming/index-build optimization before a full PQTable replay.

## Corrected v2 replay addendum

The corpus-weighted `adc_expected` order was replayed on all 152 queries using
the v2 layout manifest.  It measured p50 `17053.27 ms`, p95 `17540.48 ms`,
mean logical payload `91,745,489` bytes, mean `22,407.2` blocks, coordinate
fraction `0.636847`, and teacher survival@256 `1.0`.  This timing phase did not
run the exhaustive parity pass; a separate all-152 parity phase is required
before evidence publication.  The old unweighted `adc_expected` result remains
as a superseded diagnostic rather than being silently replaced.

The subsequent corrected all-152 parity phase completed with `152/152`
`exact_top256_parity=true` (parity artifact SHA-256
`77789c00e7e46a67bafd626d21a651962c182e9e03f39b2f92af4a2c90a98dee`).
