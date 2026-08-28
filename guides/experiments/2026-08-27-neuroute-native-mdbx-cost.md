# NeuRoute native MDBX cost frontier

Date: 2026-08-27. This protocol follows the configuration-only NeuRoute
training sanity study. It measures a new engineering question and does not
reinterpret that study's preregistered `selected=null` decision.

## Question

The raw-Euclidean route at 512 requested addresses reads roughly 3.1k posting
entries per query, while the replicated PCA control at 16 requested addresses
reads roughly 6.0-6.4k. Probe count therefore does not identify the cheaper
serving route. The question is whether 512 narrow, single-placement learned
address lookups are cheaper or more expensive than 16 wider, four-placement
PCA lookups in the intended native MDBX pipeline.

## Frozen inputs

The study consumes the byte-identical #191 report, evidence receipt, and all
nine non-BatchNorm raw-Euclidean model artifacts for DE/FR/JA. No model is
trained or selected here. All three seeds are measured to avoid choosing a
favourable address distribution after observing timing. The deterministic PCA
route is rebuilt from the same frozen document vectors.

## Matrix and pipeline

Learned routes run at `16/32/64/128/256/512` probes for every seed and
language. PCA runs at its existing 16-probe, replication-four operating point.
Every row preserves the 10% candidate ceiling and the unchanged ITQ256
Hamming-768 -> binary-ADC-256 cascade; exact E5 reranking is deliberately out
of scope.

Postings use one MDBX read transaction per query, a versioned binary key,
packed little-endian document positions, and pages of 256 entries. An oversize
posting list is read and decoded, then rejected without mutating the generation
set, exactly matching the Python candidate-ceiling semantics.

## Timing and evidence

Two full-query warm passes precede nine measured passes. For each query and
stage, the median across passes is computed first; p50/p95/p99 are then taken
across queries. The native harness separates address generation, MDBX lookup
and decode, generation-array deduplication and ceiling enforcement, Hamming
selection, binary ADC selection, and total time. It also records posting bytes,
posting entries, page reads, unique candidates, accepted probes, and stable
sequence/checksum evidence.

Before timing, native candidate sequences must exactly match a Python replay
for every dataset, seed, route, budget, and query. Every measured pass must
reproduce the same deterministic counters. Model, input, source, storage
dependency, configuration, and report hashes are fail-closed.

## Interpretation

The primary comparison is raw-Euclidean at 512 versus PCA at 16 on total p50
and p95 latency per language. The secondary result is the lowest learned
budget whose native total cost does not exceed PCA. The report will publish the
complete cost-quality frontier rather than replace the old probe gate after the
fact. It cannot make a confirmation or scale-transfer claim. Its role is to
provide the serving-cost axis for the separately preregistered relevance-aware
v4 study.

## Expected result

If lookup overhead dominates, the learned 512 route will remain expensive
despite fewer postings. If posting decode, deduplication, Hamming, and ADC work
dominate, the narrow learned lists may be competitive with or cheaper than
PCA. Either outcome closes a currently unresolved MDBX architecture question.

## Result

The complete 57-row matrix finished on the local Windows/MinGW build with the
repository-pinned libmdbx `fc8b8e4` and mdbx-containers `e9e9f2f`. All native
candidate, Hamming, and ADC sequences matched their Python references for
every query before timing and in every measured pass. The replay-only evidence
run rebuilt all 12 MDBX indexes and reproduced the deterministic rows without
replaying timing. The report SHA-256 is
`fa872e9c77b1e406b493714e64d6745c5a3306c8cd5c98fc65844e1b9360174c`;
the evidence receipt SHA-256 is
`621e9af379eb4493698c94a7d9c6085f2e8cabd7600491778c416d25769f0705`.

The table reports the median across the three learned seeds of each route's
per-query p50/p95 latency. PCA is deterministic and has one row per language:

| Language | Route | Total p50 ms | Total p95 ms | Postings/query | Candidates/query |
| --- | --- | ---: | ---: | ---: | ---: |
| DE | PCA, 16 probes | 1.218 | 1.279 | 6369 | 2438 |
| DE | learned, 128 | .942 | .988 | 808 | 808 |
| DE | learned, 256 | 1.332 | 1.407 | 1577 | 1577 |
| DE | learned, 512 | 1.921 | 1.984 | 3111 | 2500 |
| FR | PCA, 16 probes | 1.262 | 1.297 | 6018 | 2448 |
| FR | learned, 128 | .913 | .978 | 796 | 796 |
| FR | learned, 256 | 1.275 | 1.378 | 1561 | 1561 |
| FR | learned, 512 | 1.931 | 2.014 | 3099 | 2500 |
| JA | PCA, 16 probes | 1.269 | 1.307 | 6287 | 2445 |
| JA | learned, 128 | .893 | .977 | 807 | 807 |
| JA | learned, 256 | 1.324 | 1.406 | 1578 | 1578 |
| JA | learned, 512 | 1.949 | 2.029 | 3117 | 2500 |

### Cost-quality interpretation

The native result rejects both simplistic interpretations. Learned 512 is not
free: its extra narrow lookups raise total p50 to about `1.52-1.58x` PCA, and
MDBX lookup/decode alone costs about `.64-.65 ms` versus `.05-.06 ms` for PCA.
Conversely, the old 128-probe gate is not the only useful serving point.
Learned 256 is within roughly `1-9%` of PCA p50 and `6-10%` of PCA p95 while
reading only about 1.56-1.58k postings instead of 6.0-6.4k.

The frozen #191 quality rows show why that intermediate point matters:

| Language | Route | ADC E5 survival | nDCG@10 |
| --- | --- | ---: | ---: |
| DE | PCA 16 | .6961 | .5434 |
| DE | learned 256 | .7768 | .6286 |
| FR | PCA 16 | .6294 | .5955 |
| FR | learned 256 | .7400 | .6185 |
| JA | PCA 16 | .6702 | .6555 |
| JA | learned 256 | .7377 | .6875 |

Thus learned 256 is the current cost-quality knee: it is approximately
PCA-cost on this machine and improves both survival and nDCG in all three
observed languages. Learned 128 remains cheaper but loses substantial quality
on FR/JA; learned 512 buys another quality increment at a real latency cost.
The next v4 protocol should therefore optimize the 64/128/256/512 frontier and
use native-equivalent cost, with 256 as the practical reference rather than
requiring 128 probes unconditionally.

These are directional warm-cache timings from one machine. They exclude E5
query encoding and exact E5 reranking, and they do not license confirmation or
scale transfer. Their scientific role is to replace probe count with a measured
serving-cost axis for the next configuration-only study.

## Additive evidence-status correction

Review after the hardware-POPCNT correction in #198/#199 found that this
measurement used a benchmark-local byte/shift population-count loop. Candidate,
Hamming-shortlist, ADC, and quality sequences remain deterministic evidence, but
the latency table above and the resulting `256 probes` cost-knee interpretation
are historical only. They are not authoritative native-backend timing and must
not be used to claim that learned 256 is PCA-cost.

The corrected #199 scale-transfer measurement uses the library's runtime-selected
`hardware_popcount` backend and establishes that frozen 12-bit/256-probe routing
passes its separate warm 1M serving gate. It does not replay the complete
PCA/128/256/512 matrix here, so it does not restore the old knee claim. Until a
hardware-POPCNT replay of this 57-row materialization is published, 256 probes is
best described as the preregistered v4 evaluation point, not a proven practical
cost-quality knee. No training or quality result is invalidated by this status
correction.
