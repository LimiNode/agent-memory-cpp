# Schedule-aware MIH routing surrogate

## 2026-08-14 - frozen train-validation gate

This experiment corrects the historical bipolar-to-Hamming scale error and
matches the production `16x16-r56` local schedule: nine bands use radius 3 and
seven use radius 2. The train-only objective estimates expected posting visits
and deduplicated candidate union separately, with a deterministic four-stratum
document pool stratified by posting mass. Documents and their ITQ projection
remain frozen; query projections use dynamic current-code false-positive mining.

The predeclared gate evaluates 128 train-validation queries per seed. A seed
requires strict ADC survival improvement, candidate and posting work at most
`1.02x` baseline, and mean code drift at most eight bits. Held-out is forbidden
unless all fixed seeds pass.

## Result

The corrected proxy improves the 64-query v1 picture only partially: seeds 52
and 53 have early Pareto-admissible checkpoints, while 54, 55, and 56 have none.
The final result is therefore `2/5`, and no held-out experiment was run.

For seeds 52 and 53, early checkpoints increase ADC survival while staying
within the requested work budget; later epochs exceed candidate/posting limits.
Seeds 54--56 are ADC-limited from the first checkpoint and later also increase
work. No failure requires relaxing the drift cap. Thus a schedule-aware work
proxy fixes a concrete modeling error but does not make asymmetric routing
stable across all ITQ rotations.

Draft evidence is tied to `735efe3df6f6e9763840d10307c32ba0d119850d`:
archive SHA-256 `6ed28bc103b165642ba648e307790e46220874a42f17c407a23f30c3d3c6220c`,
bundle-root SHA-256
`055afc7750558cb79e77e6a7d5f366c03f34e3373f6934f01a13ed67ecca7c90`.
