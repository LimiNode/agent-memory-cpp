# Epistemic review of the native full-corpus wave (2026-09-16)

The corrective implementation was reviewed independently after the replay, with
each claim tied to the measurement boundary that can actually support it.

| Claim | Evidence | Permitted conclusion |
|---|---|---|
| 1M codec payload is reproducible | independent chunk-wise audit, `1,000,000/1,000,000` rows, byte parity, fixed NumPy method/version | materialization is reproducible for the bound source and protocol |
| packed THQ4 implementation retains the same candidate set | exhaustive byte-LUT self-test plus independent 384-coordinate reference: retained-set parity `8/8`, bounded score error, no exact cutoff ties | native scalar THQ candidate retention is correct on the eight-query control; internal order is `7/8` and is not used before exact rerank |
| THQ4 is faster than direct INT8 in the smoke | one Release scalar executable, eight queries, resident local files | a machine-specific scalar control was measured; no serving SLA follows |
| THQ4 preserves candidate quality | historical Python candidate replay on one frozen stream | only the supplied candidate stream is covered; the current runner requires a fresh authoritative replay |
| standalone THQ is a viable router | eight-query reference sweep, best overlap `0.9125` | not established; current evidence argues against promoting it without a held-out gate |
| INT9/INT10 improve the codec frontier | chunk-local reference controls | not observed on this slice; no packed-storage or native-latency claim |

The main negative result is therefore bounded rather than architectural: the
current evidence rejects standalone THQ Flow as an immediate production arm,
but it does not reject THQ as a candidate prefilter.  The full-scan page
accounting is now explicit: all `23,438` THQ pages are read by the scan, and
shortlisted INT8 pages are added afterwards.  The unresolved decision is the
native four-arm R4 candidate gate, because its frozen candidate payload is not
present in this worktree.  The native executable now has a fail-closed
`--candidate-gate` mode; until that mode is run and compared with the Python
reference, the serving-quality and serving-latency receipt must remain
`PENDING`.

The next measurements should preserve this separation:

1. replay the native candidate gate on the canonical frozen stream;
2. compare native and reference ordered top-10 IDs and THQ top-128 IDs;
3. only then measure shared-K16 locality and persistent page layouts;
4. defer AVX2, NUMA, MDBX, and zstd conclusions until the native gate has a
   valid quality result.
