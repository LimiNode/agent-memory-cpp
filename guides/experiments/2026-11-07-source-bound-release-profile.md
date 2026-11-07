# Source-bound release profile qualification

**Status:** `CONTRACT_ONLY_NOT_EVALUATED`.

This note defines the first-release fixture and its acceptance boundary. It is
not evidence that derived retrieval, timestamp citation or verified hydration
is implemented. The machine-readable contract is
[`source_bound_release_profile_v1.json`](../../tools/lexical-research/fixtures/source_bound_release_profile_v1.json);
the dependency-free validator is
[`validate_source_bound_release_fixture.py`](../../tools/lexical-research/validate_source_bound_release_fixture.py).

## Decision boundary

The release path must preserve the following chain:

```text
SourceRevision -> Representation -> Segment/KnowledgeUnit -> projection/index
             -> candidate -> revision-bound admission -> evidence locator
             -> optional verified materialization
```

The current public APIs provide useful pieces of this chain, but not a complete
source-bound multi-view route. The fixture therefore records unsupported steps
as `NOT_IMPLEMENTED`; it does not synthesize a summary or question into the
`Original` body.

## Fixture

The fixture contains three release-shaped sources: a technical article, a
Telegram post with binary bytes, and a timed transcript. The article has two
source revisions and two independently published summary representations. Every
representation records its exact input revision, producer, recipe fingerprint,
publication generation and typed evidence locator.

`Original` admits only `title`, `heading`, `body`, `code` and `tags`. Summary,
fact, entity and QA views remain separate records. A derived record without an
input revision or producer fingerprint is invalid, even if its text appears
plausible.

## Acceptance matrix

| Capability | Current status | Observable condition |
| --- | --- | --- |
| Source-grounded Original lexical path | `PARTIAL` | only the five declared source fields are indexed |
| Derived summary candidate | `NOT_IMPLEMENTED` | candidate carries exact input revision and generation |
| Stale derived admission | `NOT_IMPLEMENTED` | A@1 summary is rejected after A@2 becomes current |
| Binary artifact hydration | `NOT_IMPLEMENTED` | typed binding, digest verification and bounded materialization |
| Timestamp citation | `NOT_IMPLEMENTED` | time-range locator survives reopen and resolves to source |
| Update/delete/reopen lifecycle | `NOT_IMPLEMENTED` | stale or deleted projection never reappears |

The installed Raw CAS consumer (#599/#600) proves a narrower artifact catalog
and byte-integrity scope. It does not promote this matrix to a hydrated
retrieval or media-decoding result.

## Validation

Run the contract and its negative controls with:

```text
python tools/lexical-research/validate_source_bound_release_fixture.py --self-test
```

The validator rejects a derived field admitted into `Original` and a
representation whose producer recipe is missing. It validates identity and
lineage only; runtime retrieval and hydration remain separate executable gates.

## Follow-up

Enable the `NOT_IMPLEMENTED` cases only after the corresponding public API is
available and has a focused negative control. The first implementation gate is
the article A@1 -> A@2 stale-summary scenario using the canonical
revision-bound admission path. The Telegram and transcript cases then require
typed artifact/evidence locators and reopen checks. No codec or derived-view
backend should become a default from this fixture alone.
