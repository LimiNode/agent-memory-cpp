# MIH query trust region and dynamic false-positive mining

## 2026-08-14 - predeclared train-validation gate

This branch keeps the document-side ITQ projection, document codes, bands, and
postings frozen. It learns `W_query` only from training qrels, uses a direct
soft query-code drift penalty relative to `W0`, dynamically re-mines false
positives from the current `W_query` MIH union at every epoch, and orders those
negatives by low Hamming distance, high E5 similarity, high posting mass, then
row id. A sampled radius-aware soft-collision routing surrogate represents
candidate/posting work.

The fixed train-validation Pareto gate requires a strict ADC-survival increase,
candidates and posting visits no greater than `1.02x` their baseline, and mean
query-code drift no greater than eight bits. A held-out run is permitted only
if all five fixed seeds have an admissible checkpoint; it is otherwise
forbidden.

## Result

The five-seed gate is mixed: seeds 53 and 54 each have one admissible early
checkpoint, while 52, 55, and 56 have none. Therefore the aggregate protocol
rejects the branch for held-out execution: selecting only the two successful
seeds would be post-hoc cherry-picking.

The result is constructive rather than a general no-go. Dynamic mining plus
the trust region can improve train-validation ADC at low drift for individual
seeds, but the present routing surrogate does not produce a stable all-seed
work/quality frontier. The next method should strengthen the proxy from sampled
soft collisions to a calibrated expected radius-three union/posting estimator,
then repeat the same all-seed gate before touching held-out data.

Draft evidence is tied to source commit
`4578027e5733c5cf25a961d840b0dac72f29c819`: archive SHA-256
`940602345c8b2c321a2b8cd4a11adf7de898d1b022d82ac3d267412be7d21bec`,
bundle-root SHA-256
`47765ea7740826c8cdeb963c7e6f242892ddba6d031f69cc5fe64dc72c5039bf`.
It contains all five train-validation histories and gate outcomes; it contains
no held-out quality report because the contract forbids that run.
