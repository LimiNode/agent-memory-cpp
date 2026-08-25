# Direct learned semantic-address protocol

Date: 2026-08-25. This is a new research line, not a continuation of the
binary-centroid geometry experiments. Its hypothesis is that a learned,
asymmetric router can map an E5 query directly to a small set of MDBX-like
semantic-address postings, without a runtime centroid scan.

The machine-readable pre-registration is
`tools/agent-memory-bench/direct-learned-semantic-address.example.json`.

## Runtime contract

```text
query E5 -> routing head -> top-P semantic addresses/prefixes -> posting union
         -> ITQ-256 Hamming -> ADC -> exact E5 rerank
```

The binary address is an index key, not an approximation of an E5 vector or a
centroid. The first executable stage isolates query routing: documents receive
nested, balanced, document-only PCA/median prefixes, while a fixed-final-epoch
`384 -> 128 -> 16` MLP learns query-side prefix logits from exact E5 top-10
document addresses. This is not yet a jointly learned document-placement head.

The study reports the symmetric document-head control, learned direct posting
lookups, learned addresses followed by float bucket-centroid refinement, and an
exact float scan over the same occupied bucket centroids/postings. The latter
two are runtime controls; the headline treatment has no runtime centroid scan.

## Leakage-safe training and selection

Use disjoint deterministic query partitions: training, configuration selection,
and internal evaluation. Neither address depth, probe count, replication factor,
nor model checkpoint may inspect internal-evaluation queries. A later external
confirmation split is required before any production claim.

The primary objective is E5 oracle-document survival after ADC under a fixed
candidate-mass budget. The selection partition chooses one predeclared point;
the internal partition is evaluated once. nDCG@10, routing latency, candidate
fraction, posting payload bytes, and document replication are mandatory
secondary evidence.

## Initial matrix

- semantic prefix depths: 8, 10, 12, 14, 16;
- query address probes: 1, 2, 4, 8, 16;
- document address replication: 1, 2, 4;
- candidate-mass targets: 5%, 10%, 25%.

The router emits signed logits. Alternative addresses are ordered by the summed
absolute margins of their flipped bits, with deterministic mask ties. This is
confidence-guided subset probing, not exhaustive Hamming-neighbourhood
enumeration. Candidate-mass targets are hard per-query union ceilings, so the
three budget rows are distinct executions rather than labels on one result.

Document placement is asymmetric and offline: a document may be replicated to
the base address plus its lowest-margin single-bit alternatives. The initial
document-only PCA placement is deliberately a fixed substrate for testing the
query-side hypothesis. If direct routing is viable, a separate pre-registered
follow-up may jointly learn the document-placement and query-routing heads.
The training target uses the exact E5 top-10 document oracle directly.

The evidence archive must replay address assignments, document replication,
per-query probed addresses, posting unions, ITQ/ADC cascades, E5 survival and
nDCG contributions, and bind all model/checkpoint, input, source, and split
identities.

## 2026-08-25 es-25k result

PR #176 executed the predeclared study on the frozen 25,000-document Spanish
E5 materialization: 648 queries were split into 324 training, 162
configuration-selection, and 162 internal-evaluation queries. Model/checkpoint
selection never inspected the internal partition. The MLP checkpoint was the
fixed final epoch; its training BCE decreased from `0.6723` to `0.3844`.

The learned direct treatment selected the following configuration at each
candidate-mass ceiling on the configuration-selection partition:

| Mass ceiling | Prefix | Query probes | Document replication | Actual candidates | E5 top-10 survival after ADC | nDCG@10 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 5% | 10 | 16 | 4 | 4.71% | 55.68% | 0.5679 |
| 10% | 8 | 16 | 4 | 9.77% | 66.98% | 0.6584 |
| 25% | 8 | 16 | 4 | 17.11% | 79.38% | 0.7147 |

Only the predeclared 10% headline configuration was evaluated on the internal
partition. All controls below use its exact `8-bit / 16-probe / replication-4`
posting substrate and hard candidate ceiling; therefore their differences are
not caused by different storage budgets.

| Treatment on internal evaluation | Actual candidates | Raw-union E5 top-10 survival | Survival after ADC | nDCG@10 | Directional routing+cascade p50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Symmetric document-head control | 9.76% | 66.36% | 66.36% | 0.6523 | 3.84 ms |
| Learned direct address postings | 9.77% | 64.81% | 64.81% | 0.6068 | 3.96 ms |
| Learned address pool, then float bucket-centroid refinement | 9.75% | 75.80% | 75.74% | 0.6994 | 4.16 ms |
| Exact float bucket-centroid scan over the same postings | 9.75% | 76.05% | 75.99% | 0.6986 | 4.26 ms |

The full exact-E5 baseline nDCG@10 on this internal partition is `0.8062`.
Single-query NumPy inference for the tiny MLP measured a directional p50 of
about `45 us`, excluding E5 inference. These timings are one warm local Python
run, not a production latency claim.

## Interpretation

The centroid-free learned-direct hypothesis is not supported by this first
model. It improved over the symmetric control on configuration selection by
2.96 percentage points of survival, but lost 1.54 points on the untouched
internal partition and had materially lower nDCG. The fixed-final MLP therefore
overfit or learned an unstable bitwise surrogate from only 324 training
queries. It must not be promoted as the replacement for centroid routing.

The broader semantic-address architecture remains viable. Rescoring only the
confidence-generated address pool with float bucket centroids reached 75.74%
internal survival, within 0.25 percentage points of an exact scan over every
occupied bucket centroid. This isolates the current failure: the learned router
finds a useful address pool, but its raw confidence ordering is not yet good
enough to choose the final 16 centroid-free lookups. At the selected point the
Hamming and ADC stages preserved essentially every oracle document admitted by
routing, so the quality loss is at the locator rather than the downstream
cascade.

The next learned protocol should replace independent bitwise-mean BCE with a
listwise or budget-aware address-ranking objective, compare a direct
multi-address classifier and semantic-tree classifier, and train on a larger
calibration query set. Joint document placement is still worth testing, but it
should be a separately predeclared stage: changing both sides now would obscure
whether the gain came from query ranking or from a new partition. Until that
follow-up succeeds, the float-refined address pool is the viable treatment and
the fully centroid-free path remains negative evidence.

## Evidence and limitations

The local evidence bundle contains the frozen manifests, exact split members,
replayed model checkpoint, all 450 selection rows, and per-query requested
addresses, accepted addresses, candidate unions, Hamming lists, ADC lists, and
rerank contributions for all four matched controls on both selection and
internal partitions. The fail-closed packager retrains the fixed checkpoint and
independently recomputes every quality row before writing a deterministic ZIP.

This is one language, one 25k corpus, one fixed query split, and one tiny model.
The internal partition is confirmation for this calibration study, not an
external production claim. The document placement is document-only PCA/median,
not the final jointly learned placement head, and the float controls scan at
most the occupied 8-bit buckets; their local timing does not predict
million-scale centroid-routing cost.
