# Markdown Technical Preview release-candidate matrix

## Current progress register (2026-11-26)

This section tracks existing gates; it does not expand release scope or approve
publication. Foundation baseline: `77a278bef338074227efa05365e83e967334f429`;
current AMXR integration: `7a9be667b4f5657c1c50590dde6b0a4888acdd4b`;
Foundation integration: `4418a58c41d6c4e8f42d311708f849e00e40e59f`.
The frozen candidate and decision below remain historical. Update this register
after accepted integration stages, preserving exact source/evidence identities.
An implemented component, a merged corrective PR and a qualified release
candidate are different states. General CI is not product qualification.

Profile owners remain [milestones](milestones.md),
[Raw Markdown operations](markdown-technical-preview.md),
[normalized-first profile](first-release-profile.md),
[product path](product-path.md) and
[retrieval coverage](retrieval-roadmap-coverage.md). RM = Raw Markdown Technical
Preview; NF = broader Normalized-First Knowledge Base Preview; CC = early opt-in
Conversation Core. Numeric integration is a separate product workstream, not a
new universal prerequisite for all profiles. M1/M2/M2+ retain milestone scope.

| Existing gate / implementation | Current status and source-bound evidence | Owner / remaining action and dependency | Blocks which profile? |
| --- | --- | --- | --- |
| C++17 static library, public headers, installed/relocated package | IMPLEMENTED. #700 `8bc8ab49`: GNU15.2/MSVC19.44 each64/64 tests and six installed+six relocated consumers per compiler. Final cleanup `f7de2469`: full64/64 each, Linux ASan/UBSan8/8, CI45528005943/Packaging45528005671 PASS; integrated main has the same tree. Earlier relocated receipt is historical, not a new run. | [Build](build-and-test.md); run exact release-candidate package, headers and feature matrix, not only source-tree CTest. | RM/NF/CC when selected; final candidate gate OPEN. |
| Package version policy #613 | MERGED `5cddc2a24a132a04353d9337efe9c7fcb7ff0f4e`; historical #611 Draft label below is not current. | [Decision report](experiments/2026-11-16-release-decision-report.md); retain ExactVersion tests at final candidate. No stable ABI promise. | Previous PR-integration HOLD CLOSED; candidate requalification remains. |
| ANN compatibility #612 | MERGED `331d9aea2323d6b501a873a7791d8b719124400d`; historical bounded OLD-to-NEW evidence, not numeric migration. | [Index lifecycle](index-lifecycle-roadmap.md); preserve exact generation/source frontier and requalify any changed selected storage. | Previous PR HOLD CLOSED; no ANN serving requirement added to RM. |
| ExactVector #628 / BruteForceTopK #631 | MERGED `425aca9911d5aebef02c6c475dbecba63cfb34e5` / `9bf52cbe214fcfd399356715ddbc5957324f610b`. Existing corrective tests are integrated. | [Retrieval coverage](retrieval-roadmap-coverage.md); execute required final-candidate regressions. | Previous corrective PR HOLDs CLOSED, not proof of all product gates. |
| Shared MDBX context / actual toolchain and flags | IMPLEMENTED optional `MdbxStorageContext`; exact selected composition needs qualification. Historical MSVC setup failure in the old decision report is not a current unresolved PR verdict. | [Backend owner](storage-backend-integration-roadmap.md), [build](build-and-test.md); final GNU/MSVC MDBX matrix, actual flags, DBI capacity, reopen and ownership. | RM/NF/CC selected MDBX profiles; OPEN at final product candidate. |
| Raw Markdown ingestion / resource identity and revisions | IMPLEMENTED bounded `MdbxRawMarkdownProductIngestion`, importer/manifest stores; #607/#611 source-bound evidence is historical. | [Resource owner](resource-reindexing.md); exact candidate A@1/reopen/A@2/replay and unchanged identity/frontier. Depends on selected product storage. | RM and selected NF text vertical; not a reason to require all CC. |
| File CAS, SHA-256, exact source hydration | IMPLEMENTED `FileRawBlobStore`, ArtifactCatalog and verified hydration; old consumer receipts retained. | [Artifact owner](artifact-provenance-roadmap.md), [operations](markdown-technical-preview.md); exact-source ranges, missing/corrupt/stale and budget refusals at final candidate. | RM/NF selected artifact path; OPEN final end-to-end evidence. |
| Original projections / lexical retrieval / publication frontier | IMPLEMENTED bounded product facade and persistent lexical path; not universal recoverable publication. | [Product path](product-path.md), [retrieval coverage](retrieval-roadmap-coverage.md); selected product composition, stale frontier and authoritative hydration regressions. | RM bounded composition; broader NF publication gate remains OPEN. |
| Cold offline backup/restore | HISTORICALLY QUALIFIED #611, not fresh qualification of a changed physical schema. | [Operations](markdown-technical-preview.md); repeat exact selected schema+CAS, verify identities and bytes; no hot/online backup claim. | RM advertised operations; OPEN at final candidate. |
| Numeric/typed/flat primitives and product default | IMPLEMENTED opt-in `MdbxKnowledgeUnitTypedStore`, numeric pilot, identity mapping and canonical flat storage; legacy product still decimal-primary at baseline. | [Numeric register](mdbx-numeric-identity-evidence-register.md), [foundation closure](audits/2026-11-foundation-closure.md#3-identity-closure-register-at-main); differential reuse of #681, #674 and #651, then transactional/snapshot/product integration. Their open PRs are not automatically accepted. | Selected numeric product path, not every RM/CC installation; independent work OPEN. |
| Numeric migration / rebuild / reject-old | Existing opt-in evidence is POST_NUMERIC_REJECT_OLD, `migration_executed=false`; historical upgrade workflow is not migration proof. | [Numeric register](mdbx-numeric-identity-evidence-register.md); exercise declared policy for every selected changed DBI with exact OLD/NEW and consumer receipts. No production migration authorized. | Any release selecting changed numeric schema; OPEN until chosen policy is qualified. |
| AMXR primary byte API / Foundation integration | #700 MERGED at `7a9be667`, post-main CI45531307893/Packaging45531307854 PASS; #699 MERGED at `4418a58c`, exact-head CI45543670619/Packaging45543670719 and post-main CI45544579308/Packaging45544580025 PASS. No wire/digest/golden migration. | [Identity](identity-policy.md), [foundation](audits/2026-11-foundation-closure.md); historical inventory plus explicit AMXR delta retained, not rewritten to claim a whole new semantic audit. | Immediate CC implementation gate CLOSED; other binary APIs and release acceptance remain open. |
| Conversation phases1/2/3A/3B | INTEGRATED through #698 at baseline; domain, AMCE, reference store and source importer exist. | [Event owner](conversation-event-contract.md); phase count is not percent effort or product acceptance. | CC prerequisites met within bounded slices, no RM dependency introduced. |
| P4-0 / P4-1 / P4-2..5 | P4-0 and INTERNAL P4-1/P4-2/P4-3 INTEGRATED through #703 at `8a092d51`, accepted tree `ffe98770`. Private create/import/exact/history/correction/reopen; no public full store/deployment. [Integration receipt](audits/2026-11-26-conversation-p4-2-p4-3-integration.md) separates original local evidence, exact integration-HEAD checks and post-main CI/Packaging. [P4-2](experiments/2026-11-25-conversation-durable-p4-2.md) and [P4-3](experiments/2026-11-26-conversation-durable-p4-3.md) qualification notes remain historical source-bound runs, not reruns. | [Phase4 plan](conversation-durable-store-phase4-plan.md): P4-4 HOLD for [exact DTO/witness2/read-only approval](conversation-durable-admission-v2-proposal.md); fresh opt-in profile without dual-reader/migration is selected in direction only. Current parity/benchmark and P4-5 full/65,537-admission qualification NOT RUN. Separate diagnostic-allocation fix [mdbx-containers #336](https://github.com/LimiNode/mdbx-containers/pull/336) stays Draft/unmerged; downstream dependency update and fault matrices follow acceptance. No new schema/API adoption or automatic P4-4/P4-5 merge authorized. | CC; not RM/NF universal blockers. |
| Conversation phases5/6 | PLANNED: full Core qualification, then derived knowledge bridge. | [Event owner](conversation-event-contract.md); qualify selected durable profile before full Core claim. | CC full claim; derived bridge only when selected. |
| Normalized-first source verticals / provenance / recoverable publication | PARTIAL implementations and contracts, not a finished NF profile. Tables/images/transcripts/derived multi-input views retain separate gates. | [First-release profile](first-release-profile.md), [product path](product-path.md), [retrieval coverage](retrieval-roadmap-coverage.md); implement and qualify declared verticals. | NF only; do not add these to narrow RM. |
| M1/M2/M2+ and Managed Memory Blocks | PARTIAL / SPECIFIED / RESEARCH per owning capability; no blanket completion claim. | [Milestones](milestones.md), [foundation maturity](audits/2026-11-foundation-closure.md#5-historical-architecture-maturity-and-gate-classification); keyed facade reuses canonical content/prompt composition, later work. | Only selected capability/profile; not new P4-1 or RM gates. |
| Final release decision / version/tag/package/GitHub Release | OPEN; old release report is superseded physical evidence. No release candidate selected/approved by this register. | [Preflight](technical-preview-preflight.md), [decision report](experiments/2026-11-16-release-decision-report.md); one exact candidate matrix, residual findings, owner approval, then publication. | RM/NF/CC release claims as applicable. |

Critical path for the first limited RM release: select the intended product
composition and old-data policy -> qualify that exact installed path, source
frontiers, reopen/update/replay and cold restore -> close actual residual
findings in the final preflight -> owner release decision -> version/tag/package
publication. Numeric product activation requires its own integration evidence
if selected; merely merging an opt-in pilot does not migrate the product.
Conversation P4-1..5 and broader NF/M1/M2 work continue independently and are
not silently added to that narrow release path. This register authorizes no
release, database migration or deployment.

> **Numeric-identity migration notice (2026-11-19).** The matrix below is a
> pre-numeric physical-storage snapshot. Its exact SHAs and CI receipts are
> retained for provenance, but the matrix is `HISTORICAL_ONLY` until the
> numeric-key migration decision is exercised with a fresh OLD/NEW fixture.
> See [`mdbx-numeric-identity-evidence-register.md`](mdbx-numeric-identity-evidence-register.md).

This is a historical readiness snapshot, not a release approval. It evaluates
the frozen candidate main `dde38ae89fd8bef4be4e7306f2250a7fe3a094d4` after
#611. The current release decision is recorded in
[`experiments/2026-11-16-release-decision-report.md`](experiments/2026-11-16-release-decision-report.md).
No tag, version publication, package upload, GitHub Release or production
retrieval activation is authorized by this document.

## Evidence baseline

| Surface | Evidence |
|---|---|
| Post-merge CI | [run 43224174466](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43224174466), SUCCESS; Full research CTest SKIPPED |
| Post-merge packaging | [run 43224174341](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43224174341), SUCCESS |
| Operational installed consumer | #611; A@1/reopen/A@2, generation/SHA-256/bytes, stale A@1, missing/corrupt CAS, typed budget, two-resource cold restore |
| Internal persistence regression | #611; post-join interruption, readable completed product, identity-preserving replay and later stale rejection |
| Local focused checks | #611: six MDBX tests, installed consumer, MDBX-off build, header probes, links, preservation and `git diff --check` PASS |
| Dependency baseline | libmdbx `fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f`; mdbx-containers `4ab32b5eab3cdfce95d1a6c0f05f566019793ec4`; C++17/CMake 3.20+ |
| Package version | `0.1.0`; #613 proposes ExactVersion policy and remains a separate Draft gate, not part of this frozen #611 candidate |

The installed product evidence is Ubuntu MDBX in packaging CI, with an
additional local Windows/MinGW run. Windows/macOS default CI lanes validate
the MDBX-off/minimal package, not MDBX product execution. Do not generalize
the Ubuntu product result to those platforms.

## Capability decision

| Classification | Requirement | Evidence / gap | Owner and action |
|---|---|---|---|
| `BLOCKER_FOR_MARKDOWN_PREVIEW` | No unreviewed storage or hydration defect; exact bytes and stale evidence fail closed | #607/#611 executable path and CI are green | First agent: retain independent review and rerun exact-head evidence if candidate changes |
| `AWAITING_ANN_COMPATIBILITY_REVIEW` | Derived-index generation/source-frontier upgrade proof before any release claim including future reindexing | #612 is Draft, currently based on older main and not independently approved | Second agent: rebase/complete #612; first agent must not edit or merge it |
| `DOCUMENTATION_REQUIRED` | Conservative CMake package-version policy for `0.x` with no ABI promise | Current candidate still uses `SameMajorVersion`; #613 tests `ExactVersion` and remains Draft | Independent review and guarded integration of #613, if approved |
| `DOCUMENTATION_REQUIRED` | User-facing platform scope must distinguish Ubuntu MDBX from minimal Windows/macOS package lanes | This matrix and operations guide state the distinction | Preserve this wording in any release notes |
| `SAFE_TO_DEFER` | General migration tooling, online/atomic MDBX+CAS backup, process-kill durability and hot relocation | Explicitly outside #611 cold/offline contract | Future separately scoped operational work |
| `SAFE_TO_DEFER` | Tables/cells, retained images, timed transcripts, derived summaries, graph navigation, general connectors and coordinator runtime | Broad normalized-first profile remains separate | Additive vertical PRs only |
| `SAFE_TO_DEFER` | Stable ABI, cross-version migration and cross-platform MDBX file interchange | No such promise is made by `0.1.0` candidate | Reassess only with explicit compatibility evidence |

## Release contract

If eventually approved, the preview may advertise only C++17, the optional MDBX
backend, original Markdown import, bounded lexical candidate retrieval,
source-backed verified hydration, same-version reopen/host replay and the
qualified cold offline backup/restore procedure. It must not advertise the
broader normalized-first profile, real ANN/R4 serving, arbitrary connectors,
online backup atomicity, crash-safe multi-authority publication or stable ABI.

The backup procedure verifies the expected test resources and full CAS objects;
an MDBX file plus a CAS directory merely existing is not sufficient evidence.
`MdbxStorageContext::open()` can create a new empty database, and a wrong table
prefix can create an empty namespace, so host preflight must preserve and check
the original path, prefix, profile and identities.

## Decision boundary

Current decision: **release candidate frozen, publication blocked**. The next
decision requires independent #612 review/outcome, the #613 package-version
policy decision, a final exact-commit preflight regenerated after any accepted
integration, and explicit release approval. The post-#611 matrix is historical
evidence, not final release evidence after a candidate-changing merge.
Until then, do not create a tag, publish a package, enable production retrieval
or merge unrelated research work.
