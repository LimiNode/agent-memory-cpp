# Opt-in numeric flat Markdown consumer

This executable fixture composes existing adapters without changing production
defaults or adding a Markdown parser to the library. It is a consumer-level
conformance check, not a migration, benchmark, or new publication coordinator.
The canonical-content owner remains
[canonical-content-storage-roadmap.md](canonical-content-storage-roadmap.md).

The checked-in Markdown fixture has one `# ` heading and two single-line
paragraphs, separated by blank lines and ending with LF. The test normalizes
CRLF to LF before import and records the parser recipe. No general Markdown
grammar, arbitrary source round-trip, or lossless parser is claimed.

The consumer imports those actual bytes through
`MdbxRawMarkdownProductIngestion`, binds exact scope/resource/generation/digest
and source-revision provenance in numeric flat canonical metadata, edits one
paragraph, deletes another, renders and republishes the edited source. A
metadata-only commit then binds its exact second source revision without
rewriting block payloads. The explicit two-step publication is not atomic
across the canonical store, importer and File CAS.

Before exposing an actual lexical retrieval and authoritative hydration result,
the fixture's host-side check requires the selected canonical revision's scope,
resource, source generation, SHA-256 and source revision to match hydration,
and requires hydrated bytes to equal its exact canonical render. It rejects
stale pinned revisions, wrong scope, insufficient body budget and canonical
corruption. This single-writer fixture does not establish concurrent atomic
admission or activate production retrieval.
Scope isolation here is the host's explicit metadata predicate, not an
assertion that the flat store provides a `ScopeId` partitioning API.

The existing raw-product importer still uses its legacy canonical-content
adapter internally; this check does not replace that adapter with numeric flat
storage. The fixture uses short scope/resource IDs because that legacy path's
hex-framed string keys remain subject to the MDBX environment's maximum key
size. Numeric flat composite keys are separately fixed at 16 bytes. A passing
consumer does not establish that all production DBIs have migrated to numeric
identifiers or that arbitrary-length logical identities work on the legacy
importer path.

The Python driver runs four fresh, externally bounded processes: create,
reopen, corrupt, and verify-corrupt. Reopen checks historical/current renders,
metadata-only outline and children, selected subtree, point lookup, historical
block identity no-reuse and retrieval-visible edited bytes. It verifies one
canonical value read for outline, two for point/subtree, one block payload
write for text edit and zero for metadata edit. These are physical-value
observations, not constant-time claims: dictionary lookup and structure/ledger
validation still perform their documented work. The final process proves raw
retrieval remains independently usable while canonical corruption suppresses
the consumer-visible body.

With an MDBX-enabled Release build:

```sh
cmake --build BUILD --target agent_memory_flat_markdown_consumer
ctest --test-dir BUILD -R '^agent_memory_flat_markdown_consumer$' --output-on-failure
```

Each subprocess has a 25-second timeout; the enclosing CTest has a 120-second
timeout. Process completion and assertions establish only this bounded fixture
path, not release qualification or upgrade compatibility.
