# NeuRoute router mechanism diagnostic

Date: 2026-08-28. Frozen protocol and completed measurement.

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

## Results

Both wider widths activated the scheduler follow-up for all three seeds. At
DE-1M, the strong old heads produced the following top-10 90%-coverage
frontiers, averaged across the three frozen seeds:

| Width | Oracle p50 candidate fraction | Current p50 candidate fraction | Current p50 probes |
| ---: | ---: | ---: | ---: |
| 12 | .001967 | .057284 | 240.2 |
| 14 | .000538 | .045766 | 772.8 |
| 16 | **.000155** | .045564 | 2937.7 |

Every individual 14/16-bit seed passed the predeclared mechanism gate. The
worst oracle p95 candidate fractions were only `.001054` at 14 bits and
`.000294` at 16 bits, while current-minus-oracle p50 gaps ranged from `.0327`
to `.0611`.

The result is even more informative than a generic width failure. Wider frozen
document partitions are cheaper under the exact oracle: top-10 neighbours are
spread across roughly 9.3--9.7 addresses, but their postings are small. The
current independent-logit ordering reaches those useful addresses later as the
space grows. For top-100 90% coverage at DE-1M, oracle p50 candidate mass fell
from `.01572` at 12 bits to `.00501` at 14 and `.00156` at 16, whereas the
current scheduler still consumed `.17067`, `.15272`, and `.13905`.

The bounded scalable controls show the same separation at DE-25k. Expanded
training improved current ordering relative to the matched schedule, but its
top-10 90%-coverage current mass remained about `.071` against oracle mass
below `.001`. More training did not remove scheduler regret.

## Interpretation

The predeclared causal branch is resolved in favor of a query-side scheduler
experiment. These frozen 14/16-bit document partitions have ample address
headroom; they are not intrinsically too fragmented for a 10% candidate budget.
The dominant defect is that query logits impose an increasingly expensive
ordering over the larger address space. This does not yet prove that a cheap
learned scheduler can realize the oracle frontier, and it does not license a
wider production route.

The next PR may therefore freeze document addresses and train or calibrate only
query-side occupied-address ordering. It must retain the original geometry as
an anchor and evaluate the full Hamming/ADC/exact cascade on held-out
configuration queries.

## Evidence

```text
result SHA-256:   95816d8181f4af8eb7f17244926069e0fed5375c2ef40803bc0bd4b475a2b28
evidence SHA-256: 9bca5286284a57775752ee9db0eb5328b8b003bb87e7d06801b2d27dc308f769
```

The fail-closed evidence independently recomputed all three exact-neighbour
sets, all 39 route diagnostics, summaries, and the conditional decision, then
required a byte-identical canonical result.
