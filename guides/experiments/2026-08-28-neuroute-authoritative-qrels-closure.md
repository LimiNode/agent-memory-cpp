# NeuRoute authoritative qrels closure

Date: 2026-08-28. Additive evidence hardening over #219.

## Question

Do the published per-query nDCG values replay from the exact qrels, query-ID,
and document-ID payloads declared by each frozen E5 and prepared-study manifest?

The prior evidence writers replayed every quality result byte-for-byte, but the
quality loaders did not independently hash the qrels payload against the frozen
manifest entry. A self-consistent ranking replay could therefore remain
disconnected from the authoritative relevance bytes.

## Closure

The shared validator now requires this chain before and after every full quality
replay:

```text
E5 manifest bytes
  -> prepared-study manifest bytes
  -> evaluation qrels/query-ID/document-ID payload bytes
  -> prepared-manifest qrels descriptor
  -> deterministic quality runner
  -> byte-identical published per-query nDCG and decision
```

It also validates the qrels row structure, row count, unique query/document
pairs, and non-negative integer grades. The evidence receipt records every
manifest and payload SHA-256. Revalidating the roots after replay closes a
time-of-check/time-of-use substitution window.

The additive writers cover the nDCG-bearing results from #201, #205, #207,
#211, #213, #217, and #218. #216 publishes a routing-mechanism diagnostic, not
a new qrels-based nDCG result. #219 only binds #218's negative activation
decision, so neither needs a separate quality replay.

No measured runner, model, treatment row, native executable, timing sample, or
scientific gate changes in this closure.

## Expected result

All seven historical quality results must remain byte-identical when replayed
from roots whose actual payload bytes match both manifest layers. Any payload
mutation must fail before a new evidence receipt can be written.
