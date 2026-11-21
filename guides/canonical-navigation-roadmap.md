# Canonical Navigation API

**Status:** Phase 1 read-only C++17 slice implemented. The revision-bound
`CanonicalNavigationStore` adapter is also implemented; semantic routing and
record-level backend navigation remain separate.

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

`CanonicalNavigationStore` is the storage-facing adapter. Its `read_outline`,
`read_children` and `read_subtree` operations each perform exactly one
`ICanonicalContentStore::read_revision()` call and reject a returned document
whose identity or revision does not match the requested pin. They never call
`read_current`, `read_block` or `materialize_markdown` as a follow-up. Child
and subtree limits are explicit: admitted entries/blocks are returned with
`BudgetExceeded` and `truncated=true` when a limit is reached. A missing exact
revision or parent is `NotFound`; malformed trees, including cycles, are
`InvalidDocument`.

The current store contract returns a complete immutable revision, so this
adapter gives one-snapshot and deferred-navigation semantics but cannot avoid
the store's materialization cost. A future record-level backend may implement
the same facade without loading unrelated bodies; it must preserve the exact
revision binding, preorder validation and budget outcomes above. An absent exact
revision is reported as `NotFound` before an invalid zero budget is reported;
this keeps failure precedence consistent with the reference adapter.

The C1 `MdbxCanonicalContentStore` now also exposes backend-specific
`read_outline`, `read_children` and `read_subtree` methods. Each valid-budget
operation opens one read-only MDBX transaction, reads the exact revision row
and its bound body, and validates the complete logical digest, physical
checksum and structure before returning. Invalid zero budgets still resolve
the exact revision first, so a missing pin remains `NotFound` without reading
the body. The decoder keeps revision structure and only copies admitted text
blocks; it does not materialize a full text vector for a bounded subtree or
metadata-only read. C1 still stores the raw body as one MDBX value, so the
physical value read is not a per-block I/O claim. A future framed body profile
may reduce byte reads, while preserving this result contract. Navigation rows
above the hard backend block bound fail closed.
That ceiling applies only to navigation, not to ordinary full C1 reads or the
persisted body format. Full revision/body decoders reject impossible counts
against the remaining encoded bytes before reserving their vectors. The
`mdbx_canonical_large_body_test.cpp` fixture reopens a valid 1,048,577-block C1
body, requires an ordinary read to succeed, and retains navigation rejection.
The C1 backend validates canonical preorder and derives depths/child counts in
one pass with ordered identity maps (`O(V log V)`); it does not reuse the
reference builder's quadratic child scans on the storage path.

Revision `0` is the valid initial canonical snapshot and is addressed exactly
like later revisions. The reference implementation reconstructs and compares
canonical preorder before producing either outline or subtree output. This
validation is intentionally an in-memory reference; its child scans are not a
constant-time navigation claim. A backend adapter must preserve the same
rejection and budget semantics when it replaces those scans.

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
