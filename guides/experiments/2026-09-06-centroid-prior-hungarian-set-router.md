# Centroid-prior and Hungarian set-anchor router

## Question

Can a set-prediction model learn several continuous PCA12 anchors per query,
then use those anchors as a residual correction to a cheap centroid prior?

## Protocol

`run-hybrid-centroid-hungarian.py` trains an 8-anchor MLP with Hungarian
matching between predicted and teacher-projected top-document anchors.  At
runtime, predicted-anchor cell costs are combined with the frozen PCA centroid
score using alpha in `{0.1, 0.25, 0.5, 1.0}`.  The replay uses the same DE-1M
split, 32k/64k/128k candidate budgets, exact E5 final scoring, and seeds
13/37/101.

Runner: `tools/agent-memory-bench/run-hybrid-centroid-hungarian.py`.
Artifact: `tmp/ordinal-lattice-de1m/hybrid-centroid-hungarian.json`.
Artifact SHA-256: `04ac7af73a2c44dde798fa6c8823a9cd4a300d488d6cb7452a217142dcb4a8e9`.

## Result

The strongest setting was the conservative centroid prior (`alpha=.1`).  At
64k it reached configuration overlap `.7939` and internal overlap `.7693`; at
128k these were `.8987` and `.8785`.  It is better than the plain Direct4096
router but below the frozen E5-centroid K=8 control (`.815/.796` at 64k) when
the latter is given the same multimodal budget interpretation.  The model is
large for this routing-only experiment (`246,656` bytes) and Python p95 route
time was about 19 ms at 64k and 45 ms at 128k.

## Interpretation

The experiment is diagnostic rather than a clean architectural victory.  The
Hungarian set model can provide a useful low-weight correction to a centroid
prior, but larger residual weight consistently hurts.  It does not establish
that a learned cell residual head is better than direct E5 centroids.

## Important limitation

This is not an independent pure-Hungarian baseline: the evaluated score is a
hybrid of a frozen centroid prior and predicted-anchor distances.  The residual
is not a separately trained per-cell head, and the run is a routing ceiling,
not a native R4 cascade.  Those distinctions must remain explicit in any later
comparison.
