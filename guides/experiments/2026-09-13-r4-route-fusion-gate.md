# R4 route-fusion gate (2026-09-13)

This replay tests a deterministic equal-consumed-entry round-robin under one
global whole-posting budget.  It compares the three shallow model-ranked R4
streams with a deep-8192 route (frozen model-ranked 1,024 prefix followed by a
regenerated coarse tail).  The deep route shares the seed-2701 postings, so
duplicate entries are counted as work when both routes are present.

The three-seed baseline remains `.9763/.9783/.9796` at 5k/10k/20k and `.9796`
at 50k.  Adding deep-8192 without deduplicating the shared seed route does not
improve 5k–50k recall: it stays `.9763/.9783/.9796/.9796`, while posting-entry
work rises to approximately `7.1k/14.5k/30.3k/82.5k` and duplication reaches
`1.41/1.45/1.52/1.65`.  At 100k the three-seed-plus-deep union reaches
`.9921`, but only after spending about 143.6k posting entries; this is above
the target candidate budget and is not a production result.

Removing the duplicate shallow seed-2701 route and fusing seed-2702, seed-2703
and deep-8192 produces the same recall through 50k and `.9921` at 100k.  Thus
the naive scheduler cannot convert the `.9961` membership support into a `.99
@ 50k` result.  A query-aware marginal-gain scheduler, multi-anchor route, or
new partition topology is required; teacher-guided ordering is not used here.

All figures are logical posting-work measurements.  No MDBX/OS page bytes,
payload rerank, latency, or production activation is claimed.  See the compact
receipt `2026-09-13-r4-route-fusion-result.json` and external raw bundle.
