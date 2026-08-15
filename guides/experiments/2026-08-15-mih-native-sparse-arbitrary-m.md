# Native sparse arbitrary-m MIH latency frontier

## 2026-08-15 - predeclared protocol

**Question.** Which of the fixed-r56 `m=15..21` layouts from the exploratory
Python work frontier has the lowest real warm-query latency and acceptable
immutable index footprint on the measured CPU?

**Status.** The prior work-counter sweep is exploratory and does not select a
production layout. This benchmark uses fixed ITQ seed 52 solely to measure a
native representation. It is not a fresh quality confirmation and may not be
used for production selection. The subsequent cost-aware selection must use
calibration data only, then be confirmed once on an untouched split or corpus.

**Hypothesis.** `m=19`, `m=20`, and `m=21` are strong latency candidates, but
Python work counters cannot determine whether several thousand sparse bucket
lookups cost more or less than the extra posting traversal, deduplication, and
Hamming work. No layout is called a winner before native timing.

**Representation.** The initial implementation is deliberately one immutable
baseline, not a storage shootout:

```text
per band: sorted unique uint32 keys + uint32 offsets + contiguous uint32 postings
query: key enumeration -> lower_bound lookup -> posting traversal
       -> generation-array dedup -> full Hamming-256 -> stable top-768
       -> ADC@256 -> exact E5@256
```

An open-address or flat-hash directory is deferred. It becomes a challenger
only if the measured `bucket_lookup` component materially dominates the
independently timed `candidate_generator_total`.

**Exactness contract.** Near-equal widths use the historical deterministic
minimum-enumerated-key schedule with the corrected literal tie-break:
lexicographically maximum radius vector after widths are sorted descending.
Every configuration must satisfy

```text
sum_b (local_radius_b + 1) = 57
```

so every document within global Hamming distance 56 is included. The benchmark
also independently scans all documents for each measured query and rejects a
report if the candidate union violates this inclusion or its Hamming shortlist
differs from a reference sort over that union. This conformance pass is outside
the timed region.

**Matrix.** The committed contract fixes ITQ seed 52, 1,252 query identities,
one unrecorded warm pass, seven timed repeats, K1=768, ADC=256, and exact=256
for `m=15,16,17,18,19,20,21`. It materializes input from the canonical
calibration/evaluation roots and hashes every binary payload, config, report,
runner source, benchmark source, Hamming implementation, vector-similarity
implementation, compiler flags, and build environment.

```text
py tools/agent-memory-bench/run-mih-native-sparse-arbitrary-m.py run \
  --executable <agent-memory-mih-native-sparse-arbitrary-m> \
  --calibration-root <canonical calibration root> \
  --evaluation-root <canonical evaluation root> \
  --output-root tmp/mih-native-sparse-arbitrary-m-v1/matrix
```

**Measurements.** Reports retain logical index bytes; empty/non-empty probes;
posting visits; touched posting-length mean/p95; unique candidates per posting
visit; deterministic candidate and shortlist checksums; and per-query
p50/p95/p99 samples for key enumeration, bucket lookup, posting traversal,
generation deduplication, Hamming scoring, top-K, ADC, exact rerank,
candidate-generator total, and cascade total. The two total timings are
measured end to end; separately timed component values must not be summed as a
replacement because cache effects are part of the real pipeline.

**Decision rule after this run.** This matrix establishes a native latency /
memory frontier only. A following predeclared calibration-only stage will fit
cost coefficients to the measured units and select `m` plus a radius schedule
under the unchanged exact-inclusion condition. That frozen choice then receives
one evaluation on a new untouched split or corpus against BinaryFlat and
BinaryHNSW. The ElasticHash-style 64/80/96-bit locator remains deferred unless
this direct full-code MIH branch misses its declared native budget.

## Result

The complete seven-row matrix passed all per-query fixed-r56 inclusion and
shortlist conformance checks. The table reports one Windows/AMD64,
GNU 15.2.0, warm in-memory run; it is a measured local frontier, not a
cross-machine production claim.

| m | index MiB | probes/query | postings/query | candidates/query | generator p50 ms | generator p95 ms | cascade p50 ms |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 15 | 3.560 | 10,488 | 2,487.0 | 2,308.8 | 1.0893 | 1.2031 | 1.3158 |
| 16 | 3.548 | 7,232 | 3,373.5 | 3,075.6 | 0.8724 | 0.9758 | 1.0836 |
| 17 | 3.419 | 4,803 | 4,208.0 | 3,774.4 | 0.6814 | 0.7749 | 0.8906 |
| 18 | 3.195 | 3,060 | 4,959.5 | 4,385.2 | 0.5252 | 0.6072 | 0.7336 |
| 19 | 2.951 | 1,874 | 4,907.4 | 4,323.7 | **0.3927** | 0.4653 | **0.5954** |
| 20 | 2.723 | 1,554 | 6,463.6 | 5,514.9 | 0.4017 | **0.4640** | 0.6093 |
| 21 | 2.550 | 1,267 | 8,479.4 | 6,939.3 | 0.4111 | 0.4831 | 0.6203 |

`m19` is the measured p50 low point for both end-to-end totals. `m20` is a
nearby tail-latency point: its generator p95 is 0.0013 ms lower in this one
run, while cascade p95 is effectively tied (0.6871 vs 0.6869 ms). It therefore
does not establish dominance. `m21` minimizes index bytes, but its added
posting, deduplication, Hamming, and top-K work more than offsets the saved
lookup time. Conversely, `m15..18` are lookup dominated: at `m15`, lookup
p50 alone is 0.8087 ms of the 1.0893 ms candidate-generator p50.

This answers the question left open by the Python counters: on this sparse
sorted-directory implementation, bucket lookup is material, and the
`m19 -> m20/21` reduction in lookups no longer pays for the additional candidate
handling. It does **not** yet establish that a hash directory would change the
winner; that requires a separately predeclared storage challenger if profiling
or a product budget warrants it.

The next experiment remains calibration-only native-cost schedule selection.
It must preserve `sum_b(r_b + 1) >= 57`, freeze a configuration before seeing
new evaluation data, and compare it once on an untouched split/corpus against
BinaryFlat and BinaryHNSW. The matrix evidence is staged separately from Git.
