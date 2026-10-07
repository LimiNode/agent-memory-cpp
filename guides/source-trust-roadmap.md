# Source Trust and Provenance Roadmap

Status: `Normative` for derived memory and retrieval evidence. This guide
extends `artifact-provenance-roadmap.md` to ordinary text, summaries, facts,
tool outputs, and generated projections.

## Provenance classes

```text
raw_user | imported_source | tool_output | extracted_fact |
model_summary | model_generated | external_reference
```

Every derived unit or projection carries a `ProvenanceRef` to its immediate
inputs, source revision, extractor/model identity, and creation timestamp.
Trust is metadata and policy, not a replacement for access control. A strict
trust threshold may exclude a candidate, but the default routing behaviour is
to preserve the candidate with its evidence status.

## Evidence ancestry and corroboration

`ProvenanceRef` records the immediate inputs of a derived unit or projection.
Evidence-aware policy may additionally follow those links transitively to the
available source roots or an equivalent ancestry summary. These are two
different views of the same lineage: an immediate-input reference explains the
last transformation, while transitive ancestry explains which observations or
source roots ultimately support the result. An ancestry chain may be partial;
missing or opaque upstream lineage remains explicitly unknown.

Corroboration is a policy decision over evidence ancestry and observation
lineage, not a count of records, agents, providers or source identifiers. Two
records with different IDs can still derive from one copied source or summary.
For conceptual policy semantics, an independence assessment has three possible
outcomes:

```text
Independent | Dependent | Unknown
```

This is documentation semantics, not a required public C++ enum or storage
schema. `Independent` requires policy-defined independent roots or observation
lineages. `Dependent` records shared or copied ancestry. `Unknown` means that
the available lineage cannot establish either conclusion; it is neither
corroboration nor proof of dependence and must not be silently upgraded.

Storage and epistemic roles remain separate. A representation may be the
retained **primary representation** for a materialization or search profile
while still being a derived transcript, translation or summary with dependent
or unknown evidence ancestry. A storage role does not make that representation
an independent observation or a more authoritative claim. See
[`artifact-provenance-roadmap.md`](artifact-provenance-roadmap.md) and
[`canonical-content-storage-roadmap.md`](canonical-content-storage-roadmap.md)
for the capability-qualified retention rules.

## Epistemic non-laundering

Derived processing may improve structure, accessibility, localization or
calibration, but it does not launder provenance into new evidence:

```text
derivation              != independent evidence
copying                 != corroboration
retrieval frequency     != epistemic authority
shared ancestry         != independent consensus
summary of summary      != new observation
```

Summaries and extracted facts therefore preserve a source/provenance reference
even when a retention profile is allowed to discard the original bytes. The
retained representation can satisfy a materialization capability, but it does
not erase the lineage or change the evidence status of what it represents.

## Required invariants

- summaries and extracted facts preserve a source/provenance reference; a
  capability-qualified retention profile may discard original bytes only under
  the canonical-content contract;
- compression and translation preserve source/citation links;
- retrieval results expose provenance and generation, including when a result
  came from a derived projection;
- prompt or document instructions are data, not authority for the storage or
  retrieval agent;
- copying, repeated retrieval, model regeneration and shared ancestry do not
  increase evidence independence or epistemic authority;
- an `Unknown` ancestry/independence result is not counted as corroboration and
  is not treated as proven dependence;
- deletion/privacy requests propagate to derived projections and rebuildable
  indexes;
- trust policy changes are versioned and auditable.

## Acceptance fixtures

Use a small fixture containing raw text, a generated summary, an extracted
fact, a copied summary with shared ancestry, a separately observed source, a
tool result, an intentionally incomplete lineage, a superseding revision, and
a deleted source. Verify immediate-lineage round-trip, transitive ancestry
classification (`Independent`/`Dependent`/`Unknown`), citation preservation,
capability-qualified retention, deny-by-default access, stale projection
exclusion, and prompt-injection-as-data handling. The fixture must also show
that a derived representation retained as a profile's primary materialization
source does not become independent evidence merely because retrieval uses it.
