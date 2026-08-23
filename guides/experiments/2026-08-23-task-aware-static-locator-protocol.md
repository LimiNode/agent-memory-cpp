# Task-aware static locator protocol

Date: 2026-08-23. This is a separate, predeclared successor to random static
locator calibration. It is not permitted to use French confirmation data.

## Split discipline

The frozen Spanish 25k materialization is split deterministically by the first
eight bytes of `SHA-256("task-aware-static-locator-v1\\0" + UTF-8 query_id)`,
ordered ascending: first 324 query IDs are selector-training, next 162 are
configuration-selection, and final 162 are internal evaluation. No bit, band
order, width, or radius choice may inspect the internal-evaluation queries.
Any subsequently frozen choice requires a new untouched confirmation split.

## Comparator and objective

Keep ITQ-256 as the ranking representation and 16-bit MIH routing bands. The
random 64–128 bit r3/r4 frontier is the non-neural baseline. On selector
training queries, score each bit by its ability to preserve E5-oracle document
membership under a fixed candidate budget; choose a nested bit subset and band
assignment using only that score. On the selection partition, choose one
predeclared width/radius point by E5 survival subject to the same 25% candidate
and fresh-full-code latency budget. The internal evaluation then reports the
frozen point once.

The evidence archive must bind all three query-ID sets, objective inputs,
selected positions, selection decision, native source/config manifests, and
per-query E5/nDCG replay. A selector that cannot beat the random frontier on
the internal evaluation does not authorize a learned locator implementation.
