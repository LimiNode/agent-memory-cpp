# Matched native finalist serving wave (2026-09-30)

This gate runs one native serving contract over the frozen finalist payloads:

```text
frozen R4 candidate shell -> THQ4 top-128 -> ordered cosine top-10
```

The runner is `tools/agent-memory-bench/run-native-finalist-serving-wave.py`.
It uses predecoded, hash-bound payloads for LSQ32, LSQ48, PLSQ8x6, TQ1,
TQ1+secondary payload, RSLM3 and RSLM4. No codec fitting or decode work is
performed in the timed region.

All seven arms reproduced the ordered top-10 contract for `152/152` queries.
The compact result records total p50/p95/p99 and payload hashes. Representative
total p50 values were 0.2349 ms (LSQ32), 0.2351 ms (LSQ48), 0.2307 ms
(PLSQ8x6), 0.2389 ms (TQ1), 0.2404 ms (TQ1+secondary), 0.2381 ms (RSLM3),
and 0.2334 ms (RSLM4).

These numbers are matched native rerank measurements, not a codec ranking:
decode, training, MDBX I/O, cold/restart behavior, updates/rebuilds and fresh
qrels are outside this gate. The frozen top-128 candidate shell is retained as
the explicit scope; no production threshold or winner is inferred.
