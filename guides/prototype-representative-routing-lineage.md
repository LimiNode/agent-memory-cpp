# Prototype–Representative Routing terminology and lineage

Status: `DOCUMENTATION_PLAN` (version 1, 2026-11-19)

This record fixes the names used for the prototype/representative routing
research and for a possible future product implementation. It is a naming and
compatibility contract, not an activation of a new retrieval backend.

## Canonical names

| Display name | Machine identifier | Meaning | Status |
|---|---|---|---|
| NeuRoute | `historical.neuroute` | Historical name of the external work that motivated the research line. | Historical reference |
| NeuRoute-inspired | `research.neuroute_inspired` | An experiment that borrows a mechanism or evaluation shape without claiming a reproduction of the external system. | Research label |
| NeuRoute R4 / Modern R4 | `research.neuroute_r4` / `research.modern_r4` | Versioned research configurations and their frozen receipts. | Historical research identifiers |
| Prototype–Representative Routing (PRR) | `routing_family.prr` | Family of routing methods that use prototypes, document representatives, and an explicit opening budget. | Planned family name |
| Prototype–Representative Inverted File (PR-IVF) | `routing_algorithm.pr_ivf` | An IVF implementation in the PRR family: prototypes select cells, and representative/document postings expand the selected cells. | Planned algorithm name |

PRR is a family, not a synonym for IVF. PR-IVF is one member of that family,
not the name of the complete retrieval pipeline. THQ, packed scoring, exact
reranking, metadata admission, and canonical hydration remain separately
versioned stages.

The machine identifiers above are reserved naming values for manifests,
receipts, and a future configuration contract. They do not imply that the
corresponding backend is available in the current library.

## NeuRoute R0–R4 lineage

The following table preserves the research sequence without rewriting its
historical artifact names. The stages are evidence lineage, not a claim that
all R4 experiments are PR-IVF.

| Stage | Frozen research contribution | Boundary and interpretation |
|---|---|---|
| R0 | Scalar K8 prototype/query features and the learned address scorer. | A compact address representation; no product routing API. |
| R1 | Shared, teacher-blind projection of the raw K8 prototype geometry. | Still prototype-level evidence; it is not a document posting store. |
| R2 | Query-conditioned gating over the projected K8 representation. | A routing representation experiment, not a durable index format. |
| R3 | Occupancy and residual summaries for documents inside occupied addresses. | Introduces document-level representative information while retaining the research cascade. |
| R4 | Actual-document representatives, postings, K32/R0 scoring, and the bounded candidate cascade. | Historical research configuration; only a configuration that also has PRR/PR-IVF cell selection and representative postings may be described as PR-IVF. |

The R0–R4 labels, seed manifests, source hashes, fixture identities, result
files, receipts, and archived PRs are immutable historical identifiers. New
documentation must link to them, not rename them.

## When the PR-IVF name applies

Use `routing_algorithm.pr_ivf` only when a reproducible configuration manifest
binds all of the following:

1. a prototype/centroid training revision and deterministic seed;
2. cell selection and a declared probe/opening budget;
3. representative or document postings for the selected cells;
4. a stable posting order and candidate identity;
5. an explicit downstream stage contract (for example THQ top-128 followed by
   packed scoring and exact reranking); and
6. an independent quality/recovery receipt that does not silently substitute a
   legacy R4 stream.

The matched historical `Prototype-IVF / balanced cascade` in
[`2026-10-02-serving-mode-bakeoff.md`](experiments/2026-10-02-serving-mode-bakeoff.md)
is the closest current evidence. It may be cited as a PR-IVF precursor or
candidate configuration, but it is not a production implementation. The
`Modern R4` rows remain `research.modern_r4` unless they satisfy the manifest
conditions above; a green benchmark or a similar cascade does not perform the
rename.

## Configuration and compatibility plan

The current public vector contract is `IVectorIndex`; it exposes vector
search and does not expose a routing-family or routing-algorithm selector. The
repository also has no public PRR/PR-IVF factory or descriptor. Therefore this
record deliberately adds no enum, factory, JSON schema, or production default.

When a runtime configuration is justified, it should use independent fields
with explicit unknown-value handling, for example:

```json
{
  "routing_family": "routing_family.prr",
  "routing_algorithm": "routing_algorithm.pr_ivf",
  "document_codec": "codec.example-v1"
}
```

The future contract must obey these rules:

- `routing_family` and `routing_algorithm` are stable machine-readable values;
- routing topology and document codec are selected independently;
- an omitted routing field preserves the legacy exact/default behaviour;
- an unknown or unsupported value fails closed with a typed configuration
  error; it never falls back silently to PR-IVF or to legacy R4;
- a legacy receipt keeps its original identifier and remains replayable;
- a manifest records the exact route, codec, seed, source revision, and
  downstream stages;
- a PR-IVF value is not accepted until a real backend and its capability
  checks exist.

This keeps the compatibility surface additive and prevents a name from
becoming a promise. The future API may collapse the two routing fields if the
implemented backend proves that family and algorithm are inseparable; until
then, no redundant public enumeration is warranted.

## Relationship to current prompt and canonical-content work

Prompt Composition (`compile_prompt()`), Canonical Navigation, and durable
prompt profiles are separate canonical APIs. They must not be reimplemented by
an experimental routing PR. Likewise, PRR/PR-IVF projections remain derived
indexes: canonical content, provenance, revision identity, and source bytes are
owned by the canonical storage contracts.

The experimental consumer in PR #637 must therefore be reconciled with the
already integrated `compile_prompt()` contract before any merge. A second
`build_exact_prompt()` implementation must not become a competing production
compiler. PR #637 may retain experiment-specific evidence only if it calls or
adapts the canonical API without changing its semantics.

## References

- [`IVectorIndex`](../src/agent_memory/index/IVectorIndex.hpp) — current public
  vector-index contract.
- [`2026-10-02-serving-mode-bakeoff.md`](experiments/2026-10-02-serving-mode-bakeoff.md)
  — matched historical Prototype-IVF and Modern R4 comparison.
- [`2026-10-03-native-flat-and-r4-finalist-wave.md`](experiments/2026-10-03-native-flat-and-r4-finalist-wave.md)
  — source-bound routed downstream measurements.
- [`2026-08-29-neuroute-matched-representation-ladder.md`](experiments/2026-08-29-neuroute-matched-representation-ladder.md)
  — R0/R1/R2 representation ladder.
- [`2026-08-29-neuroute-r3-matched-ladder.md`](experiments/2026-08-29-neuroute-r3-matched-ladder.md)
  — document-summary R3 ladder.
- [`2026-08-30-neuroute-r4-document-representatives.md`](experiments/2026-08-30-neuroute-r4-document-representatives.md)
  — R4 representative materialization.
