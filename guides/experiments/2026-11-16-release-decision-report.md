# Release decision report — 2026-11-16

This report records the release decision for the exact integrated candidate
`main` at:

```text
5efd4ead68cb891a9f7ebb94c9f03f49d902fc2d
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

The commits between these entries also contain the already-reviewed MDBX and
index qualification fixes. The exact candidate is the only revision to which
the decision below applies.

## CI and package evidence

| Evidence | Result | Boundary |
|---|---|---|
| Post-merge CI for #619 candidate | [run 43818373245](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43818373245) — SUCCESS | Required jobs passed; Full research CTest was skipped by classifier rules |
| Post-merge packaging | [run 43818373070](https://github.com/LimiNode/agent-memory-cpp/actions/runs/43818373070) — SUCCESS | 6/6 installed-consumer and upgrade jobs passed |
| JSON-OFF header gate | Included in the integrated CI | Public headers compile without JSON support |
| #619 negative install fixture | Included in packaging | The forbidden `CanonicalContentStoreInternal.hpp` diagnostic is checked and the expected negative exit is consumed explicitly |
| #620 ownership allocation regression | Reviewed evidence | 6 allocation calls at 128, 512, 2048 and 4096 chunks; this is not a whole-system throughput proof |
| #623 tokenizer regression | Reviewed evidence | CamelCase/golden-token tests preserve the intended boundaries |
| Local C++17 Release CTest in this docs worktree | 46/46 passed | JSON=OFF, MDBX=OFF; this is a focused build check, not the full release preflight |

The packaging result covers Linux, Windows and macOS minimal consumers, the
MDBX installed consumer, the JSON installed consumer, and the OLD-to-NEW
exact-generation upgrade. It does not establish a universal MDBX or research
profile on every platform.

## Open pull requests and process state

### #625 — hybrid reranker admission

Source review of HEAD
`088a8ca8955375eeb1f43b7660d8e6e521a5ee9a` found no confirmed P0/P1/P2 in
the bounded admission contract. Composite identity, protected metadata,
duplicate/foreign rejection, finite scores, mandatory filters, and the
upstream authorization boundary are covered. The benchmark remains
exploratory and stdout-only.

Decision: **CODE APPROVE, PROCESS HOLD**. The branch is based on the old
`ee974627819130004be62efc79c9b401bbe29ac2` base. Rebase onto
`5efd4ead68cb891a9f7ebb94c9f03f49d902fc2d`, run new exact-head CI and repeat
the short source review before considering a guarded merge.

### #628 — exact-vector cosine cancellation

Source review of HEAD
`c85e7e8dc84b8acbbcf8457e9ed95c77e31a0fed` found Neumaier accumulation,
cancellation-sign coverage and scalar/SIMD parity checks. The independent
reference helper is ordinary sequential `double`; it is sufficient for the
bounded regression but is not a general high-precision adversarial oracle.

Decision: **CODE APPROVE, PROCESS HOLD**. Rebase onto the candidate SHA,
obtain new exact-head CI and repeat the review. No additional SIMD corrective
pass is required before that rebase.

### #626 and #629

These remain research/documentation work. They are not required for the
narrow Raw Markdown preview and must not be used to imply ANN, R4, or
production retrieval readiness. Their compatibility conclusions can be
carried into a later, separately qualified phase.

## Findings and deferred debt

No new confirmed P0, P1 or P2 was found in the integrated candidate or in the
bounded source reviews above. The following are deliberately deferred rather
than silently promoted to release guarantees:

- full research CTest and any classifier-skipped research lanes;
- universal multimodal/table/transcript hydration and arbitrary connectors;
- online or process-kill-atomic MDBX+CAS backup, migration tooling and hot
  relocation;
- stable ABI, cross-version migration and cross-platform MDBX file exchange;
- ANN/R4 serving, graph traversal, hybrid fusion and compact-vector codecs;
- a production default for #625 or #628 before rebase and exact-head CI.

## Verdicts

### Raw Markdown Technical Preview

**HOLD.** The integrated evidence is sufficient to request release approval
after one final local preflight against the exact candidate SHA. That preflight
must include the supported C++17 Release profile, JSON ON/OFF, MDBX ON/OFF,
the installed consumer, package-version matching, source-backed import/reopen/
update/hydration, fail-closed stale/CAS/budget cases, interruption/recovery,
cold two-resource restore, and the OLD-to-NEW exact-generation upgrade.

Until that run is recorded, the candidate is not `READY_FOR_RELEASE_APPROVAL`.
No tag, package upload, GitHub Release or production activation may occur.

### Future M1a / AN R4

**HOLD.** The current candidate provides bounded exact-generation evidence,
not a production AN R4 implementation or an ANN compatibility promise.

## Next observable step

Run the final local preflight from a clean checkout of
`5efd4ead68cb891a9f7ebb94c9f03f49d902fc2d`. A successful result must record
the commands, configuration, test counts and any skipped classifier lanes in
an updated decision report. A failure keeps the Raw Markdown verdict at
`HOLD` and must name the failing boundary; it does not authorize a compensating
scope expansion.
