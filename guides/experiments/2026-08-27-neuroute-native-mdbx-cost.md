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
