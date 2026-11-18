# Canonical Navigation API

**Status:** Phase 1 read-only C++17 slice implemented. Storage adapters and
semantic routing remain separate.

Canonical navigation is a bounded view over an already materialized
`CanonicalDocumentRevision`. It gives an agent stable addresses for inspecting
structure and selecting a subtree without turning an outline or index into a
source of truth.

## Contract

`build_canonical_outline()` returns metadata-only entries in canonical preorder.
Each entry carries a `CanonicalBlockLocator` containing the document ID, exact
document revision, stable block ID and block revision. The outline has no
authority to replace source text and does not copy block bodies.

`read_canonical_subtree()` accepts an optional root block, a maximum block
count, a maximum decoded text budget and an `include_text` switch. Results are
always pinned to the supplied document revision. A budget stop returns the
blocks already admitted together with `BudgetExceeded` and `truncated=true`;
the caller must treat that status as partial rather than as a complete read.

Malformed trees, duplicate IDs, dangling parents and non-preorder input are
rejected. A missing root is `NotFound`. No function consults a newer revision,
retrieval index, ambient clock, network or access-control policy.

The API is deliberately pure. A storage-facing adapter may first call
`read_revision(document_id, revision)` and then pass that exact value to the
navigation functions. This keeps freshness and authorization at the owner of
the storage/consumer boundary and prevents an accidental `latest` read.

## Relationship to hierarchical retrieval

This API supplies the first local navigation primitive for
[`hierarchical-navigation-roadmap.md`](hierarchical-navigation-roadmap.md).
It does not implement DomainMap admission, lexical/dense search, route hints,
source hydration or graph traversal. A navigation result is an addressable
view; factual answers still require canonical source reads and the applicable
provenance/access checks.

The next integration slice may add backend-specific bounded reads that avoid
materializing an entire revision. It must preserve the same locators, preorder,
revision pinning and explicit budget outcomes, and it must prove parity with
this in-memory reference implementation before changing the public semantics.
