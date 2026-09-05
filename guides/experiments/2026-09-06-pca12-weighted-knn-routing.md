# PCA12 weighted-kNN cell routing

## Question

Can train-query neighbours transfer useful cell sets through a weighted vote,
and thereby improve the frozen PCA12 scheduler without a neural router?

## Protocol

The runner used the same frozen DE-1M PCA12 cells, exact teacher labels,
postings, candidate budgets (32k/64k/128k), and exact E5 final scoring as the
centroid studies.  Weighted votes over neighbour cell sets were evaluated with
multiple neighbour counts and seeds; the final seed-8 replay is the canonical
result.

Runner: `tools/agent-memory-bench/run-weighted-knn-cell-routing.py`.
Artifacts: `tmp/ordinal-lattice-de1m/weighted-knn-cell-routing-v2.json` and
`weighted-knn-cell-routing-v2-seed8.json`.
Seed-8 artifact SHA-256:
`0d3907e3bc2ed59fb2555fbe5fd6b35a02888ffa3f5c2af6129167b86c20d5d4`.

## Result

At 64k candidates the best weighted-vote policies (k=16/32) achieved only
`.351/.361` overlap; internal overlap was about `.293` for the best k=32
setting.  nDCG remained around `.33` on configuration queries and `.27` on
internal queries.  Seed precision was only approximately `.04-.09`.

## Interpretation

The weighted cell vote is not equivalent to the earlier kNN-1 union baseline:
one neighbour's complete cell set can be useful, while aggregating many cell
sets destroys the ranking signal.  This is a genuine negative result, not
evidence that local query smoothness is absent.

## Limitations and follow-up

The study uses one frozen partition and Python routing.  If kNN is revisited,
the meaningful controls are nearest-neighbour set transfer and explicit
diversity/coverage quotas, not another uncalibrated weighted vote.
