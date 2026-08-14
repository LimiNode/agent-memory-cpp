# Schedule-aware MIH routing surrogate

## 2026-08-14 - frozen train-validation gate

This experiment corrects the historical bipolar-to-Hamming scale error and
matches the production `16x16-r56` local schedule: nine bands use radius 3 and
seven use radius 2. The train-only objective estimates expected posting visits
and deduplicated candidate union separately, with a deterministic four-stratum
document pool stratified by posting mass. Version 3 correctly converts each
stratum sample mean to a population total; it does not divide the estimate by
the sample count a second time. Documents and their ITQ projection remain
frozen; query projections use dynamic current-code false-positive mining.

The predeclared gate evaluates 128 train-validation queries per seed. A seed
requires strict ADC survival improvement, candidate and posting work at most
`1.02x` baseline, and mean code drift at most eight bits. Held-out is forbidden
unless all fixed seeds pass.

## Result

The population-scaled v3 proxy improves the 64-query v1 picture only partially:
seeds 52 and 53 have early Pareto-admissible checkpoints, while 54, 55, and 56
have none. The final result is therefore `2/5`, and no held-out experiment was
run.

For seeds 52 and 53, early checkpoints increase ADC survival while staying
within the requested work budget; later epochs exceed candidate/posting limits.
Seeds 54--56 are ADC-limited from the first checkpoint and later also increase
work. No failure requires relaxing the drift cap. Thus a schedule-aware work
proxy fixes a concrete modeling error but does not make asymmetric routing
stable across all ITQ rotations.

Earlier v1 and v2 replays are historical: their stratified work estimate was
under-scaled. The fresh v3 replay records the complete normalized execution
contract for both accepted and rejected rows and confirms the `2/5` result
under the population-scaled estimator, without opening held-out data.

Draft evidence v4 is tied to `3237a5c744c4e8d7e81c63a441c47530dd9dfa4e`;
the measured matrix execution remains bound to
`37d56d1fdd5e7cd1dd94e975590420f02dc8882e`. Archive SHA-256
`4b522b674619f4aabaae42e0003be2b08be0c83f73f0caadebbb606aecd494df`,
bundle-root SHA-256
`c647ab909b38a14fd5792282c82cb130b0ca08f18f798c3a5fedf7b9e1717fe2`.
Its packager derives the matrix source hashes from that exact commit through
`git show`, snapshots the evidence source, and is covered by CTest and CI
self-tests.

## 2026-08-14 - train-selected held-out confirmation

This follow-up asks a narrower practical question without relaxing the `5/5`
robustness rule: can an offline system select one configuration entirely from
the frozen #140 train-validation matrix, then transfer that one choice to
held-out data? The selection ranks eligible checkpoints by ADC-survival delta,
then maximum work ratio, mean Hamming drift, epoch, and seed. It selects seed
52, epoch 1 before held-out access.

The one permitted paired comparison, selected `Wq` versus that seed's matched
frozen `W0`, raises ADC/E5-oracle second-stage survival by `+0.002636` (paired
95% interval `[+0.000160, +0.005112]`). Candidate and posting work rise by
`+25.37` (`[+21.93, +28.77]`) and `+30.98` (`[+27.07, +35.09]`) per query;
reranked nDCG@10 changes by `+0.000912` (`[-0.002089, +0.003904]`). This is a
positive result for the preselected practical configuration, not a claim that
the method is rotation-robust: the parent all-seed gate remains `2/5`.

Draft evidence v2 is tied to `47043c2ff1568d3500e1507720425b97aa98b431`:
archive SHA-256 `1b3699b85fcd98576e3cd5c8fc7cd1d312eafe1a52cb1c2da4723b1dbe314947`,
bundle-root SHA-256
`3daba23b67606b2033fe6284b19c26fdf48e131a3e783fff29eba0198ecbea96`.
