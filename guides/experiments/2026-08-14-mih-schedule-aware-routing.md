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

Draft evidence v3 is tied to `37d56d1fdd5e7cd1dd94e975590420f02dc8882e`:
archive SHA-256 `4ba6ac7634663ee842b60ffaa36ec252dac3b13fb383bf79878d98091be6a3dd`,
bundle-root SHA-256
`9cea1fe2501c357b9be22f023fc23e2686ccf8dcf0152cbab397eb47deb7e26e`.
Its packager derives the matrix source hashes from that exact commit through
`git show`, snapshots the evidence source, and is covered by CTest and CI
self-tests.
