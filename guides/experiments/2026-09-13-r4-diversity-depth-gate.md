# R4 diversity and address-depth gate (2026-09-13)

## Scope

This experiment extends the corrected, model-ranked frozen R4 comparator.  It
tests whether independent materialized seeds provide useful candidate
diversity and whether regenerating a deeper address frontier (8,192 occupied
addresses) improves the frozen 1,024-address ceiling.  The DE-1M fixture and
all three R4 mapping manifests are frozen inputs; teacher IDs are used only for
evaluation.

The measurement is a logical whole-posting oracle.  `posting_entries_touched`
and `actual_unique_candidates` are candidate-work proxies; physical MDBX/OS
page traffic, payload reranking, and latency were not measured.

## Seed-union gate

Each seed uses the corrected model-ranked order inside its frozen 1,024-address
shortlist.  Streams are merged with deterministic equal-consumed-entry
round-robin.  The union is evaluated at 5k, 10k, 20k, 50k, and 100k unique
candidate budgets, with overlap and duplication recorded.

The three-seed union reaches mean teacher recall `.9763` at 5k, `.9783` at 10k,
and `.9796` at 20k and 50k (p05 `.855/.900/.900` respectively; minimum `.7`).
At 50k it touches about 60.5k posting entries (duplication ratio `1.244`) and
exhausts near 50.3k unique candidates, so the 100k row is an exhaustion result,
not a fulfilled budget.  Two-seed unions reach `.9612–.9684` at 50k.  A single
seed remains near `.90` and exhausts around 21k.  Seed diversity is therefore
real and valuable, but the tested union does not pass the `.99 @ 50k` gate.

## Depth gate

For seed `2026082701`, the runner reconstructs the full-dimensional R4 address
space from persisted mappings and regenerates an 8,192-address frontier.  The
evaluated stream deliberately forces the old frozen 1,024 prefix and appends a
regenerated tail deduplicated against it; this is a construction policy, not a
claim that the naturally regenerated first 1,024 entries are identical.  The
receipt reports natural prefix overlap separately.  Two arms are reported:

* `frozen_prefix_coarse_tail`: frozen coarse prefix followed by regenerated tail;
* `model_prefix_coarse_tail`: frozen model-ranked prefix followed by the same
  regenerated coarse tail.  The tail is intentionally not called model-ranked;
  the model was trained only on the old 1,024 shortlist.

Both arms are effectively identical beyond the prefix.  Mean recall is about
`.907/.926/.954/.978` at 20k/30k/50k/100k candidates, with p05
`.6/.7/.8/.9` and minimum `.4/.5/.6/.6`.  The deeper frontier therefore raises
  the single-seed ceiling from roughly `.904` to `.954 @ 50k` and `.978 @ 100k`,
  but still misses `.99 @ 50k`.  Teacher address ranks have median 30 for the
  coarse prefix and 6 for the model prefix; the p95 is about 2,095, with rank
  8,193 denoting an address outside the generated frontier.

## Decision point

The tested frozen R4 topology is not sufficient to license the conditional
`R4 -> interval² THQ-ADC -> top-256` cascade.  Seed union gives a strong but
sub-gate `.9796` result, while depth helps materially but remains sub-gate.
The next branch is therefore a multi-anchor R4 experiment (`M=2/4/8`) or a
larger/deeper verified R4 topology, rather than physical THQ/MDBX
materialization.  These results do not rule out a future multi-partition R4;
they establish that the current 1,024-address single-seed and three-seed union
cannot meet the production-selection gate.

## Reproducibility

Compact receipts:

* `2026-09-13-r4-seed-union-gate-result.json`
* `2026-09-13-r4-depth-gate-result.json`

Raw row bundles are external to Git under `E:\\_repoz\\agent-memory-workspaces`.
Both receipts bind the frozen fixture and R4 manifests by SHA-256 and declare
`production_activation: false`.
