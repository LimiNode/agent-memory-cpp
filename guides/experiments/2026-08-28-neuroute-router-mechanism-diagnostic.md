# NeuRoute router mechanism diagnostic

Date: 2026-08-28. Frozen protocol; measurement pending.

## Question

Does 14/16-bit routing fail because frozen document addresses form an
intrinsically expensive partition, or because the current query-logit probe
order reaches useful occupied addresses too late?

The study does not retrain a router. It compares the strong old raw-Euclidean
12/14/16-bit heads across nested DE-25k, DE-100k, and DE-1M, then adds the
matched-25k and expanded-100k scalable-schedule heads at DE-25k as controls.
All model and data bytes are inherited from #203 and #209.

## Metrics

For the 76 frozen configuration queries, exact E5 top-10 and top-100 document
sets are computed once per scale. For every frozen route the diagnostic records:

- distinct relevant addresses and their count entropy;
- Hamming-radius distributions from the query address;
- current best-first probe count and cumulative posting mass needed for
  50/75/90/95% relevant-document reachability;
- the exact minimum posting mass achievable by any subset of relevant
  addresses at the same coverage levels;
- current-versus-oracle candidate-mass regret;
- correlation between query-logit uncertainty and early-probe utility.

Relevant-address count and probe count remain separate: ten relevant documents
can occupy at most ten addresses, while reaching those addresses can require
many unrelated probes.

## Conditional decision

The query-side scheduler follow-up activates only when at least two of three
seeds for a frozen 14- or 16-bit DE-1M route satisfy both conditions at 90%
top-10 coverage:

```text
oracle p95 candidate fraction <= .10
current p50 candidate fraction - oracle p50 >= .02
```

This is a mechanism gate, not a production gate. If the oracle partition is
already too expensive, query-only training is not licensed; the next branch
must instead change document geometry or the teacher/miner. No production
router can be selected by this diagnostic.

## Evidence contract

The runner must verify the frozen #203 result, evidence, materialization and
every referenced model/payload hash, plus the #209 result, evidence, and model
hashes. The evidence writer reruns the complete diagnostic and requires a
byte-identical canonical result before emitting a passed receipt.
