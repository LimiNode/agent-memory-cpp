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
centroid. Float-IVF remains a frozen teacher/control only. The initial study
must report three controls: direct addresses, hierarchical prefix expansion,
and address-to-float-centroid refinement.

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

The router must emit calibrated address scores. Prefix expansion and alternative
addresses are selected only from these scores; exhaustive Hamming-neighbourhood
enumeration is out of scope for this line.

Document placement is asymmetric: a document-placement head may assign one or
more addresses, separately from query routing. The training target may use the
exact E5 top-10 document oracle directly; reproducing Float-IVF `nprobe`
centroids is permitted only as a teacher/control, never as the headline
objective.

The evidence archive must replay address assignments, document replication,
per-query probed addresses, posting unions, ITQ/ADC cascades, E5 survival and
nDCG contributions, and bind all model/checkpoint, input, source, and split
identities.
