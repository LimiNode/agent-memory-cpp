# Release decision report — 2026-11-16

This report records the release decision for the exact integrated candidate
`main` at the current integrated source candidate:

```text
c93a7b867cd802e279a4118b07cc177f10322cf3
```

It is a decision record, not a tag, GitHub Release, package publication, or
production-activation request. No such publication is authorized by this
document.

## Candidate composition

The candidate contains the following reviewed integration points after the
historical #611 preview snapshot:

| Change | Integrated commit | Scope |
|---|---|---|
| #612 | `331d9aea2323d6b501a873a7791d8b719124400d` | bounded exact-generation compatibility evidence |
| #613 | `5cddc2a24a132a04353d9337efe9c7fcb7ff0f4e` | exact package-version matching for `0.x` |
| #614 | `343985bb343a3984b2a0914c14816a190675834` | release-candidate documentation snapshot |
| #615 | `53164c86127c8bd42440ae3e2fe270616cc5aaf0` | SemCS compatibility audit |
| #616 | `dbdc6481401c35aef480fce42cd9fbcbda9d8119` | include-policy documentation audit |
| #619 | `5efd4ead68cb891a9f7ebb94c9f03f49d902fc2d` | installed package excludes backend-only headers |
| #620 | `ca647805a7a644433f5eb074f820afb4b104a5f2` | transactional `ExactLexicalIndex` replacement |
| #623 | `efb5db506cd246ca235690871685d5a8705bc7dc` | tokenizer boundary correction |
| #627 | `c2486bc45301fbb83514094cc4c7929e3fc7a7af` | JSON-OFF public-header self-sufficiency gate |
| #625 | `c93a7b867cd802e279a4118b07cc177f10322cf3` | reranker admission boundary (merge commit) |

The commits between these entries also contain the already-reviewed MDBX and
index qualification fixes. The exact candidate is the only revision to which
the decision below applies.

## CI and package evidence

| Evidence | Result | Boundary |
|---|---|---|
| Post-merge CI for pre-#625 candidate | [run 43818373245](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43818373245) — SUCCESS | Required jobs passed; Full research CTest was skipped by classifier rules |
| Post-merge CI for #625 candidate | [run 43866997725](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43866997725) — SUCCESS | Head `c93a7b867cd802e279a4118b07cc177f10322cf3`; research lanes classified/skipped as shown by CI |
| Post-merge packaging for #625 | [run 43866997726](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43866997726) — SUCCESS | Linux/Windows/macOS consumers, JSON/MDBX installed consumers and OLD-to-NEW upgrade passed |
| JSON-OFF header gate | Included in the integrated CI | Public headers compile without JSON support |
| #619 negative install fixture | Included in packaging | The forbidden `CanonicalContentStoreInternal.hpp` diagnostic is checked and the expected negative exit is consumed explicitly |
| #620 ownership allocation regression | Reviewed evidence | 6 allocation calls at 128, 512, 2048 and 4096 chunks; this is not a whole-system throughput proof |
| #623 tokenizer regression | Reviewed evidence | CamelCase/golden-token tests preserve the intended boundaries |
| Local C++17 Release CTest (clean corrective worktree) | 47/47 passed | JSON=OFF, MDBX=OFF |
| Local C++17 Release CTest (clean corrective worktree) | 52/52 passed | JSON=ON, MDBX=OFF; includes the new #631 focused assertions |
| Local C++17 Debug CTest | 51/52 passed | JSON=ON, MDBX=OFF; `agent_memory_exact_lexical_index_test` failed after a long allocation/ownership path and requires a separate reproducible diagnosis |
| Local MDBX CTest and installed MDBX consumers | not run locally | MDBX submodules are uninitialized in this worktree; installed MDBX evidence is from packaging run 43866997726 |

The packaging result covers Linux, Windows and macOS minimal consumers, the
MDBX installed consumer, the JSON installed consumer, and the OLD-to-NEW
exact-generation upgrade. It does not establish a universal MDBX or research
profile on every platform.

## Open pull requests and process state

### #625 — hybrid reranker admission

The corrected HEAD `dc8cbc650ab7103854e4f62a277f76780db6d817` was reviewed,
rebased and guarded-merged. Merge commit:
`c93a7b867cd802e279a4118b07cc177f10322cf3`. The post-merge CI and packaging
runs are recorded above. Composite identity, protected metadata,
duplicate/foreign rejection, finite scores, mandatory filters, and the
upstream authorization boundary remain bounded admission checks; this does
not make `HybridRetrievalEngine` a canonical authorization boundary.

### #628 — exact-vector cosine cancellation

Source review of HEAD
`e2d94c09f9787b53d4454941785217acc6dedbd0` found Neumaier accumulation,
cancellation-sign coverage and scalar/SIMD parity checks. The independent
reference helper is ordinary sequential `double`; it is sufficient for the
bounded regression but is not a general high-precision adversarial oracle.

Decision: **CODE APPROVE, PROCESS HOLD**. The PR is still based on
`5efd4ead68cb891a9f7ebb94c9f03f49d902fc2d`, while `main` is
`c93a7b867cd802e279a4118b07cc177f10322cf3`. Rebase onto the current
candidate, obtain new exact-head CI and packaging, and repeat the short source
review before considering a guarded merge.

### #626 and #629

These remain research/documentation work. They are not required for the
narrow Raw Markdown preview and must not be used to imply ANN, R4, or
production retrieval readiness. Their compatibility conclusions can be
carried into a later, separately qualified phase.

### #631 — brute-force top-k corrective slice

The current source audit confirmed three concrete defects in
`BruteForceTopKIndex`: first-insertion dimension was published before the
potentially-throwing record insertion, non-finite query components reached
ranking, and cosine values were recomputed inside the `partial_sort`
comparator and again while materialising results. Draft PR #631
(`9357504667726ecff1abf754aeb7f5564d3d75be`) moves dimension publication after
successful insertion, rejects NaN/Inf queries, and scores each record once.
The JSON-enabled focused test passed locally; exact-head CI and an independent
review are still pending. The slice is not integrated into the candidate.

Decision: **OPEN_CONFIRMED, FIX_NOW**. It is a correctness repair for the
public evaluation adapter, but it is not yet release-integrated.

## Independent audit disposition matrix

The matrix below relates the 13-November audit baseline `dbdc648` to current
source and evidence. `FIXED_MERGED` requires the implementation and a
targeted regression, not merely a historical assertion.

| Finding | Severity | Current source/evidence | Status | Preview impact | Decision / follow-up |
|---|---|---|---|---|---|
| MultiProbeHammingIndex rollback | P1 | #621 rollback regression on current source | `FIXED_MERGED` | no known impact | `DEFER_WITH_REASON`; retain regression |
| ExactLexicalIndex transactional BM25 upsert | P1 | #620 transactional replacement and allocation-failure evidence | `FIXED_MERGED` | protects lexical index state | `DEFER_WITH_REASON`; fixed in #620 |
| ExactVectorIndex finite inputs, dimensions, overflow and ordering | P1 | #622 validation and ordering tests; #628 is a separate cancellation slice | `FIXED_MERGED` | exact-vector baseline is bounded | `DEFER_WITH_REASON`; #628 remains separate |
| Publication fence classification | P2 | #624 typed errors and negative conformance | `FIXED_MERGED` | fail-closed publication decisions | `DEFER_WITH_REASON`; fixed |
| StandardTokenizer allocation/noexcept | P2 | #623 tokenizer boundary regressions | `FIXED_MERGED` | lexical token identity preserved | `DEFER_WITH_REASON`; fixed |
| Installed backend-only header | P2 | #619 negative install fixture and packaging | `FIXED_MERGED` | consumer package boundary correct | `DEFER_WITH_REASON`; fixed |
| Reranker duplicate/foreign admission | P2 | #625 merged as `c93a7b8`; post-merge CI and packaging passed | `FIXED_MERGED` | bounded admission path protected | `DEFER_WITH_REASON`; no production-authority claim |
| BM25+ naming | P3 | documentation correction in #630 | `FIXED_IN_DRAFT_PR` | documentation only | `FIX_NOW`; merge #630 after rebase |
| BruteForceTopKIndex dimension/query/score handling | P2 | current code reproducer; corrective #631 | `OPEN_CONFIRMED` | eval adapter correctness; not source-backed product path | `FIX_NOW`; independent review and CI pending |
| BowVectorRetriever text and metadata filters | P2 | legacy evaluation-only adapter contract; source-backed preview uses another path | `INTENTIONAL_LIMITATION` | outside Raw Markdown product path | `DEFER_WITH_REASON`; document scope, no silent rewrite |
| ResourceIndexer matching and uniqueness scans | P2 | static O(N²) paths; no release failure reproduced | `NEEDS_EVIDENCE` | bounded ingestion remains functional | `DEFER_WITH_REASON`; measure on representative corpora |
| ExactLexicalIndex full sort before limit | P3 | bounded reference implementation; no budget failure reproduced | `OPEN_CONFIRMED` | performance debt, not correctness blocker | `DEFER_WITH_REASON`; benchmark before optimisation |
| ProjectionAware per-query rebuild | P3 | explicit scan/byte budgets in preview design | `INTENTIONAL_LIMITATION` | bounded preview cost is documented | `DEFER_WITH_REASON`; persistent generation is future work |
| MDBX search mutex and callback reentrancy | P2 | static lock/callback observations; contract evidence incomplete | `NEEDS_EVIDENCE` | no demonstrated preview failure | `DEFER_WITH_REASON`; controlled concurrency/reentrancy evidence |
| MDBX reverse ownership lookup | P3 | linear per-hit ownership search | `OPEN_CONFIRMED` | performance only under larger result sets | `DEFER_WITH_REASON`; profile before bounded reverse index |
| Duplicate CMake source / empty VectorIndex.cpp | P3 | maintenance residue confirmed by source inspection | `OPEN_CONFIRMED` | no observed runtime impact | `DEFER_WITH_REASON`; separate cleanup PR |

## Findings and deferred debt

No new confirmed release-critical P0 or P1 was found in the integrated
candidate. #631 is an open confirmed P2 in a public evaluation adapter; it is
being corrected separately and is not yet part of the candidate. The
following are deliberately deferred rather than silently promoted to release
guarantees:

- full research CTest and any classifier-skipped research lanes;
- universal multimodal/table/transcript hydration and arbitrary connectors;
- online or process-kill-atomic MDBX+CAS backup, migration tooling and hot
  relocation;
- stable ABI, cross-version migration and cross-platform MDBX file exchange;
- ANN/R4 serving, graph traversal, hybrid fusion and compact-vector codecs;
- a production default for #625 or #628 before rebase and exact-head CI;
- release approval while #631 remains unreviewed or while the Debug
  `agent_memory_exact_lexical_index_test` failure is unexplained.

## Verdicts

### Raw Markdown Technical Preview

**HOLD.** The integrated Release evidence is bounded and reproducible, but the
release owner has not yet reached approval. The corrective #631 slice is
unmerged, the Debug `agent_memory_exact_lexical_index_test` result is not
explained, and the local MDBX CTest is unavailable until the submodules are
initialized. Packaging proves installed MDBX consumers on the recorded CI
runner; it does not substitute for the missing local matrix.

Until that run is recorded, the candidate is not `READY_FOR_RELEASE_APPROVAL`.
No tag, package upload, GitHub Release or production activation may occur.

### Future M1a / AN R4

**HOLD.** The current candidate provides bounded exact-generation evidence,
not a production AN R4 implementation or an ANN compatibility promise.

## Next observable step

Next steps are bounded: obtain independent review and exact-head CI for #631;
diagnose or explicitly disposition the Debug lexical-index failure; then
rebase and requalify #628. Only after those gates should the release owner
initialize the MDBX submodules in a clean worktree and run the remaining local
MDBX/consumer/recovery matrix. A failure keeps the Raw Markdown verdict at
`HOLD` and must name the failing boundary; it does not authorize a compensating
scope expansion.
