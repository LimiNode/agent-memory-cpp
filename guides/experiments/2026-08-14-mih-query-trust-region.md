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
work/quality frontier. The gate-failure diagnostic attributes seed 52's early
failure to ADC and later failure to candidate/posting work; seeds 55 and 56
remain ADC-limited before work also grows. The two admitted checkpoints are
early (seed 53 epoch 1 and seed 54 epoch 0). No learned epoch violates the
eight-bit drift limit, so that constraint was not the observed limiter here.

The sampled soft-collision surrogate has a known historical scale error: it
mapped bipolar agreement to the local Hamming radius without the factor of two,
and it modeled all 16 bands as radius 3 rather than the production r56 schedule
of nine radius-3 and seven radius-2 bands. This PR intentionally preserves that
miscentered v1 result rather than rerunning it under a corrected objective.
The next method must predeclare a corrected schedule-aware estimator of separate
posting and deduplicated-union work, then repeat the same all-seed gate before
touching held-out data.

The executable runner already prohibited mixed statuses before the result was
known; the later explicit all-five wording in the JSON manifest preserves that
existing behavior and is not claimed to have been the literal pre-result field.

Draft evidence v2 is tied to provenance-verifier commit
`7b1b4898739cac8278efab1cd5e87d00dc961275`: archive SHA-256
`b404afba21e5c3aaec7931fb38a23e3f0a0779bbaf5fd6f3f2e45bdcc9b0bf92`,
bundle-root SHA-256
`d189be0242faec5dea80f2705d5630b994ae16c0f5874fdbc2c63f97b48ee8b3`.
It replays every gate inequality and lexicographic checkpoint choice from all
five histories, contains the failure decomposition, and contains no held-out
quality report because the contract forbids that run.
