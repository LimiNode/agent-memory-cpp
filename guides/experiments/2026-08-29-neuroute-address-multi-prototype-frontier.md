# NeuRoute address multi-prototype frontier

Date: 2026-08-29. Frozen implementation `3c02766`; measurement complete.

## Question

The single-centroid diagnostic in #227 retained about 80% of discounted target
gain in the top 1024 addresses at DE-1M, but did not provide a sufficiently
sparse final route. This study tests whether the failure is caused by
within-address multimodality rather than an absence of predictable semantic
structure.

The protocol evaluates `1/2/4/8` nested prototypes for every occupied frozen
16-bit address across DE-25k, DE-100k, and DE-1M, using all three frozen route
seeds. Selection uses 76 German configuration queries. The 76 disjoint German
internal-evaluation queries are opened only after the configuration choice for
the corresponding scale and seed has been recorded.

## Frozen construction

For a posting list with `n` documents and requested width `K`, the effective
prototype count is `min(K, n)`. The first prototype is the normalized document
mean. Every later prototype is a unique frozen member selected by farthest-first
traversal: minimize the maximum cosine to the existing prefix, with the lowest
frozen document position as the tie-break. An address is scored by the maximum
query cosine over its prototype prefix. Posting count is deliberately excluded
from this stage so the experiment isolates prototype capacity.

The measured matrix contains 72 rows:

```text
3 scales x 3 route seeds x 4 prototype counts x 2 query partitions
```

Every row reports global AP, hard-negative AUC, relevant-only density ordering,
discounted gain at 128/256/512/1024 addresses, actionable candidate mass at
50/75/90/95% gain, prototype bytes, and prototype dot-product work.

## Internal-evaluation result

The DE-1M frontier improves monotonically and materially through eight
prototypes. Values below are means over the three route seeds.

| Prototypes/address | AP | Hard-negative AUC | Relevant density pairwise | Gain@256 | Gain@1024 | Candidate fraction at 75% gain |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | .1480 | .6507 | .5874 | .6364 | .7996 | .02530 |
| 2 | .2010 | .6983 | .6093 | .6924 | .8401 | .02101 |
| 4 | .2905 | .7592 | .6350 | .7596 | .8826 | .01400 |
| 8 | .3998 | .8255 | .6667 | .8346 | .9247 | .00894 |

The gain@256 improvement from `K=1` to the best multi-prototype treatment is
`.1825`, `.2056`, and `.2064` for the three DE-1M seeds. All exceed the frozen
`.03` materiality threshold. This directly supports the within-address
multimodality diagnosis.

Configuration selected `K=8` for every DE-1M seed. The held-out internal rows
reached 75% actionable gain for `.9605`, `.9737`, and `.9868` of queries, at
candidate fractions `.00944`, `.00969`, and `.00768`. The scale summary for the
configuration-selected treatment is:

| Scale | Selected K | Internal reach at 75% gain | Internal candidate fraction | Gain@256 | Gain@1024 |
| --- | --- | ---: | ---: | ---: | ---: |
| DE-25k | 2 / 2 / 4 | 1.0000 | .00063 | .9997 | 1.0000 |
| DE-100k | 8 / 8 / 8 | 1.0000 | .00090 | .9868 | .9992 |
| DE-1M | 8 / 8 / 8 | .9737 | .00894 | .8346 | .9247 |

At DE-1M, raw prototype storage and scoring work grow as follows:

| Requested K | Prototype payload | Prototype dot products/query |
| ---: | ---: | ---: |
| 1 | 95.4 MiB | 65,113 |
| 2 | 189.1 MiB | 129,069 |
| 4 | 366.7 MiB | 250,304 |
| 8 | 663.1 MiB | 452,700 |

These are diagnostic full-table counts, not a claim that production should
score every prototype exhaustively. The result instead licenses prototype ANN
retrieval as a coarse address-shortlist generator.

## Decision

`multimodality_supported = true` and `coarse_shortlist_sufficient = true`.
The frozen direct-router gate remains negative: `direct_router_sufficient =
false`, because the selected DE-1M candidate fraction is above `.005` and
gain@256 remains below `.9`. Therefore
`learned_gain_density_reranker_followup_licensed = true`.

Production selection remains forbidden. The next experiment should freeze the
multi-prototype coarse shortlist and train a small gain-density reranker only
within that shortlist, rather than rescore every occupied address with a learned
model.

```text
result SHA-256:   c170ff29f712bccdf48d6fff42e41f31000e60116d6ef446bf8e2e8fba055bf5
evidence SHA-256: 295e43a8cbed597892af11cb47f1d3134730a4ea4a439d23e5a43001b32dda9b
```

The evidence writer rebuilt all prototype tables, reran the complete 72-row
matrix, reproduced the result byte for byte, and retained the authoritative
qrels-to-quality binding inherited through #227.

## Limitations and next check

The address representation is deterministic and not optimized for storage.
Eight prototypes continue to improve DE-1M, so saturation beyond `K=8` is not
established. Exhaustive prototype scoring is diagnostic work accounting, not a
native latency benchmark. The next frozen study must hold the selected
multi-prototype shortlist fixed, train only on the configuration partition, and
compare semantic score, posting cost, and learned marginal gain-density ordering
on the internal partition at address budgets 128/256/512/1024.
