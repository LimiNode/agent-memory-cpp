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

## Required invariants

- summaries and extracted facts never replace the raw source reference;
- compression and translation preserve source/citation links;
- retrieval results expose provenance and generation, including when a result
  came from a derived projection;
- prompt or document instructions are data, not authority for the storage or
  retrieval agent;
- deletion/privacy requests propagate to derived projections and rebuildable
  indexes;
- trust policy changes are versioned and auditable.

## Acceptance fixtures

Use a small fixture containing raw text, a generated summary, an extracted
fact, a tool result, a superseding revision, and a deleted source. Verify
lineage round-trip, citation preservation, deny-by-default access, stale
projection exclusion, and prompt-injection-as-data handling.
