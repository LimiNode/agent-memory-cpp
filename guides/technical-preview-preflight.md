# M0/KB-0 Technical Preview preflight

This document is an evidence report for the current `main` after the guarded
merge of PR #607. It is not a release, tag, API promise, or activation switch.
The intended preview boundary is a narrow, text-only, source-bound path. The
broader normalized-first profile in
[`first-release-profile.md`](first-release-profile.md) remains a target and
must not be reported as implemented merely because this preflight is green.

## Verification baseline

| Item | Value |
|---|---|
| Main commit | `52004324bc0d2b07c3657245195db349dd391774` |
| Integrated change | PR #607, squash merge `52004324` |
| Language baseline | C++17 static library |
| Optional storage | MDBX enabled for the product path; MDBX-off library also built |
| Exact-head CI | [run 43139647504](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43139647504), SUCCESS; Full research CTest SKIPPED |
| Exact-head packaging | [run 43139647610](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43139647610), SUCCESS |
| Post-merge CI | [run 43141245120](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43141245120), SUCCESS |
| Post-merge packaging | [run 43141245115](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43141245115), SUCCESS |

The #607 exact-head workflow exercised 79/79 MDBX tests on Ubuntu and all
installed consumers in packaging. The focused local qualification additionally
ran the Raw Markdown product, importer, ArtifactCatalog, File CAS, typed join
and authoritative hydration tests (6/6 PASS). These results qualify the
checked commit; they are not a claim that every research lane is a release
feature.

## Capability matrix

Status vocabulary is deliberately conservative:

- **Implemented/executable** means code and a relevant conformance test exist
  on the current main branch.
- **Experimental bounded** means an executable path exists, but its input,
  budget, or serving boundary is intentionally narrow.
- **Contract only** means an interface or owner guide exists without the
  complete product path.
- **Docs/tests only** means a hypothesis or contract has a fixture or note, not
  an installable capability.
- **Roadmap/research** means no release claim is made.

| Capability | Current status | Evidence | Preview boundary / missing work |
|---|---|---|---|
| C++17 static library and public headers | Implemented/executable | CMake package, header self-sufficiency CI, installed consumers | No ABI or long-term compatibility promise yet |
| MDBX storage context and table-prefix isolation | Implemented/executable | MDBX wiring and reopen tests | Backup/restore and migration tooling are not supplied |
| Durable resource import and revision frontier | Implemented/executable for M0 lexical profile | `MdbxResourceImporter`, importer/recovery tests | General connector lifecycle and deletion/retirement are absent |
| Raw UTF-8 Chunk persistence | Implemented/executable | Knowledge-unit importer tests and reopen path | Locator-rich table/image/transcript representations are not generalised |
| Original projection and active manifest publication | Implemented/executable for the importer profile | Manifest/profile conformance and #607 product test | General projection lifecycle, compaction and full reindex protocol remain open |
| Persistent lexical candidate retrieval | Implemented/executable as bounded projection | lexical parity/oracle lanes and revision-bound retriever tests | Term-frequency/lexical experimental profile; not a default production serving engine |
| Raw Markdown product composition | Experimental bounded | `MdbxRawMarkdownProductIngestion`, focused test, installed consumer | Only `.md`/`.markdown` and `OriginalText`; no general parser or coordinator |
| Typed resource-to-artifact binding | Implemented/executable in the article slice | typed join/reopen conformance | General multi-artifact product composition is not claimed |
| Authoritative source-backed hydration | Experimental bounded | manifest + join + catalog + CAS digest checks, missing/corrupt/stale tests | Current implementation is a bounded Raw Markdown/article path; no universal multimodal API |
| A@1 to A@2 update and stale rejection | Implemented/executable for the bounded path | exact revision/digest, stale admission and hydration regressions | No general lifecycle retirement policy |
| Reopen and replay after importer/catalog interruption | Implemented/executable for tested intents | expired-lease, catalog and host replay tests | Process-kill and arbitrary cross-filesystem atomicity are not proven |
| Installed external C++17 consumer | Implemented/executable for the checked package | Raw Markdown, hydration and MDBX consumers in packaging CI | Consumer API is preview-level and may evolve before a release |
| Tables/cell locators, retained images, timed transcripts | Contract only / roadmap | `first-release-profile.md`, typed locator contracts | Separate vertical conformance slices are required |
| Derived Summary/Concept publication | Contract only | linked-derived knowledge and publication guides | Exact-input provenance, publication and stale propagation are not product-complete |
| ANN, graph traversal, hybrid fusion and compact codecs | Research/docs only | independent research lanes and roadmap guides | Do not promote these to M0/KB-0; ANN/R4 compatibility is a separate gate |
| Autonomous coordinator, LLM/OCR/ASR runtime | Roadmap/out of scope | architecture and product-path guides | Must remain outside the static library |

## Clean installed-consumer qualification

The supported preview path is qualified through the exported package rather
than repository-private test headers. The deterministic scenario is:

1. Configure and build a clean C++17 external consumer with MDBX enabled.
2. Import Markdown revision A@1.
3. Retrieve one lexical candidate and verify the returned bytes equal the
   declared source range exactly.
4. Close and reopen the storage, then repeat the verified read.
5. Publish A@2 and verify its full SHA-256, generation and exact bytes.
6. Attempt A@1 admission/hydration and require `Stale` with no bytes.
7. Remove or corrupt the CAS object and require a candidate with
   `CasMissing`/`CasCorrupt`, never hydrated bytes.
8. Configure an index budget below the accepted projection and require the
   typed budget exception instead of a truncated ranking.

The installed Raw Markdown consumer covers the source-range and missing-CAS
parts. The focused MDBX product test covers the complete sequence, including
corrupt CAS, budget overflow, deterministic A@1/A@2 interleaving, importer
and catalog recovery, and the requirement that a host replays the original
request to finish a product join after interruption.

This is a qualification of the bounded profile, not a promise that every
source profile in `first-release-profile.md` is available.

## Canonical persistence and upgrade compatibility

The current preview preserves the following identities:

- `ResourceId` identifies the observed source within its scope; a generation
  and full body digest identify a concrete `ResourceRevision`.
- `KnowledgeUnitId` is the durable identity of the persisted raw unit; source
  references retain the revision-bound resource identity and range.
- `SourceRevisionId` and artifact binding IDs in the Raw Markdown adapter are
  bounded labels derived from the resource, generation and full digest. They
  do not replace the full `ResourceRevisionRef` or artifact digest.
- Manifest publication stores an explicit schema/profile identity and keeps
  active and historical observations distinguishable.
- Original projection and catalog records carry the digest/metadata needed by
  the authoritative hydration proof. A physical codec or representation must
  not change logical identity.

The exercised reopen path can open a database written by the same commit after
a clean restart and can replay the tested expired intents. This does **not**
establish a general upgrade guarantee between future library versions. Before
an upgrade or release, a compatibility gate must verify schema versions,
table-prefix configuration, digest algorithms, codec generations, manifest
profiles and a backup/restore replay. No migration tool or backup format is
included in this preview, and no new canonical schema is introduced by this
document.

## Operational recovery boundaries

| Event | Proven behavior | Not proven / not promised |
|---|---|---|
| Importer interruption before manifest | Intent recovery after its existing lease and host replay | Arbitrary process-kill timing beyond the tested lease/reopen path |
| Manifest visible before typed join | Candidate may be visible, but hydration is `Missing/BindingMissing` with zero bytes | Treating a low-level row as a product publication |
| Catalog interruption before/after CAS or commit | Existing catalog recovery and replay where its intent is recoverable | Physical atomicity between MDBX and filesystem CAS |
| Interruption after typed join before final observation | Host receives `Incomplete`; replay can reconcile; metadata remains fail-closed until complete | A second coordinator or rollback authority |
| Concurrent A@2 replacement | A@1 final observation returns `Conflict`; old admission/hydration is `Stale` | Perpetual freshness after a successful snapshot |
| Missing or corrupt CAS | Candidate remains observable only with typed `CasMissing`/`CasCorrupt`; no source bytes | Automatic repair or retention collection |
| Query budget exceeded | Typed budget exception and no partial ranking as a complete result | Unbounded scan, hidden truncation or implicit budget growth |

## Release classification

### RELEASE_BLOCKER

- The broad normalized-first profile is not yet a single installable product:
  table/cell, retained-image and timed-transcript verticals are incomplete.
- General crash-safe publication, backup/restore, migration and retention
  collection are not qualified as a release-wide guarantee.
- The second agent's ANN/R4 compatibility gate is still required before any
  release claim that includes derived-index persistence or future reindexing.

### REQUIRED_DOCUMENTATION

- Keep the preview explicitly bounded to original Markdown/text and the
  installed consumer scenario above.
- Publish dependency/toolchain/platform details and the exact commit with any
  distributable artifact.
- State that `Full research CTest` is skipped in the normal CI lane and is not
  release evidence.
- Keep experimental retrieval disabled by default; do not describe the
  persistent lexical adapter as a general serving engine.

### SAFE_TO_DEFER

- Backup/restore tooling, migration commands and retention collection can be
  separate operational work after the bounded preview decision.
- Table/image/transcript and derived-knowledge slices can follow as additive,
  independently reviewed verticals.

### RESEARCH_ONLY

- ANN/R4, graph navigation, learned sparse retrieval, compact codecs and
  automatic knowledge-base consolidation remain separate experiments. Their
  benchmark evidence must not silently promote the canonical M0 path.

## Decision boundary

This preflight supports a **bounded M0/KB-0 technical preview candidate** for
the Raw Markdown path after the independent ANN/R4 compatibility review. It
does not authorize publishing a release, creating a tag, changing version
numbers, enabling production retrieval, or merging future feature PRs. Any
future release must re-run this matrix against its exact main commit and
re-record the evidence URLs.
