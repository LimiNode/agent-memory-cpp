# Pre-release retrieval and AN R4 readiness audit (2026-11-15)

Status: `AUDIT COMPLETE / RELEASE BOUNDARY EXPLICIT`

This is a source-level and evidence audit. It does not activate an ANN route,
change canonical schemas, or authorize a merge, tag, release, or package
publication.

## Baseline and reviewed heads

The reviewed baseline is `main@ee974627819130004be62efc79c9b401bbe29ac2`.
The table below is a historical audit snapshot. It must not be read as the
current mergeability or CI state after subsequent corrective commits.

| Item | Head | Classification |
| --- | --- | --- |
| #621 MultiProbeHammingIndex | merged `7ba6979bde75ffe3a483a11d657f850642c633c0` | integrated baseline |
| #622 ExactVectorIndex | merged `ee974627819130004be62efc79c9b401bbe29ac2` | integrated baseline; cancellation corrective tracked separately |
| #625 Hybrid reranker admission | `088a8ca8955375eeb1f43b7660d8e6e521a5ee9a` | Draft; conditional approval only |
| #626 cosine benchmark | `83761bcac3836bb7c8196804ab53c316ca4578fc` | Draft; corrective oracle/CI/receipt pass |
| #628 cosine cancellation corrective | `c85e7e8dc84b8acbbcf8457e9ed95c77e31a0fed` | Draft; exact-head CI pending |

The first agent's #620/#623 branches are outside this audit.

### Current status supersedes the historical snapshot

At the time of the corrective handoff, `main` is
`5efd4ead68cb891a9f7ebb94c9f03f49d902fc2d`. The current Draft heads are:

| PR | Current head | Current status |
| --- | --- | --- |
| #625 | `dc8cbc650ab7103854e4f62a277f76780db6d817` | Draft; restacked on current `main`; exact-head CI `43820825942` and packaging `43820825988` successful |
| #626 | `4fe7eb82eb3a14a103f84c0d72f29751e41db3c6` | Draft research benchmark; corrected-mode self-test and provenance added; exact-head CI `43821058522` and packaging `43821058369` successful |
| #628 | `e2d94c09f9787b53d4454941785217acc6dedbd0` | Draft; restacked on current `main`; exact-head CI `43820484040` and packaging `43820484050` successful |
| #629 | `b40a106fd02b2e2a3f1abeddb3ec42ca31e255e2` | Audit update; CI `43823954404` and packaging `43823954439` successful; no merge or release action |

This row records the latest content-bearing audit head; the follow-up commit
that records this completed CI result is documentation-only.

The earlier rows and CI identifiers remain valid evidence for the heads they
name, but do not certify these newer heads. The new exact-head checks listed
above are successful, but they qualify Draft research PRs and do not authorize
merge or release. Any release decision must use these checks and receipts
together with independent review.

The latest #626 follow-up is `699d225be6036e7a9dbe34d1bff69f09245025db`.
It adds balanced six-order timing, raw observations, and a separate
`ExactVectorIndex::search()` production-path measurement. Its exact-head CI is
tracked separately from the historical receipt above; the follow-up is not a
production performance claim.

## Dependency and ownership map

```text
canonical source/revision
  -> Original/projection and manifest
  -> derived generation (descriptor, bindings, active pointer)
  -> backend candidate locator/score
  -> canonical scope, freshness and hard-filter authorization
  -> ranking/top-k with deterministic ties
  -> source hydration through the owning store
```

The canonical store owns resource identity, unit identity, revisions, source
digests and provenance. Projection and generation stores own derived records;
an index must not mint or mutate canonical identities. `ExactVectorIndex` and
`MultiProbeHammingIndex` are in-memory index contracts, not durable generation
owners. `MdbxExactIndexGenerationStore` is the existing durable lifecycle
owner for the bounded exact profile; a future R4 adapter must reuse that
generation authority rather than introduce a second publication protocol.

`HybridRetrievalEngine::admit_reranked_hits()` (source lines 42-82) protects
the upstream composite identity, metadata and finite score while allowing only
reranker order and score changes. It is an admission boundary, not canonical
authorization: the upstream index and any future R4 adapter must re-check
scope, revision freshness and hard filters before ranking.

## Findings and corrective status

| Severity | Finding | Evidence | Disposition |
| --- | --- | --- | --- |
| P1 | Sequential double cosine accumulation lost the sign of a representable near-cancellation score and could change top-k. | `ExactVectorIndex.cpp`; regression in `tests/index/exact_vector_index_test.cpp` | #628 uses Neumaier-style compensation and inserts the bad candidate first so a false tie changes the winner. Focused Release CTest passes. |
| P1 | #626 target was absent from explicit benchmark CI build lists. | `.github/workflows/ci.yml` native benchmark lane | #626 adds the target and a fast CTest self-test. Exact-head CI is required before approval. |
| P2 | Reranker admission performs two linear searches per accepted candidate and copies the accepted records. | `HybridRetrievalEngine.cpp:42-82`; #625 opt-in receipt | Keep #625 Draft; measured 10/50/100/500/1000 candidate pools before choosing an index. No speculative map refactor. |
| P2 | No dedicated TSAN qualification exists for the retrieval/index surface. | CI workflow and sanitizer configuration audit | Do not claim thread safety. The supported contract remains owner/external-synchronization unless a future immutable published-generation design says otherwise. |
| P3 | The exact benchmark is arithmetic-only, not end-to-end `ExactVectorIndex::search()`. | #626 protocol and receipt schema | Documented explicitly; no production throughput claim. |
| P3 | Historical benchmark and current source receipts can be confused if reused after kernel changes. | #626 experiment note | Historical receipt is labelled; regenerated receipts must carry source/toolchain/fixture provenance. |

## Numerical oracle and benchmark qualification

The #626 corrective pass now has three explicit layers:

1. the historical legacy FP32/selected-SIMD arithmetic;
2. the actual #622 production sequential scalar-double path;
3. an independent compensated long-double-style reference. This reference
   uses a distinct accumulation strategy and remains valid on platforms where
   `long double` has double width.

Score errors are matched by candidate ID, including candidates that move out
of the reference top-k. The executable self-test covers near-cancellation,
near-ties with deterministic ordering, and normalized vectors. Local Release
build and CTest passed; the self-test is intentionally a correctness gate, not
a timing gate. `sample_count` is a measured query-level distribution count,
not independent-request count; p99 is directional and is not a stability
guarantee.

The cancellation fixture is a negative control for the current #622 production
path. It remains separate from #628 so the benchmark cannot silently make an
unmerged production fix appear in the historical comparison.

The #625 opt-in admission receipt (Release-like MinGW/GCC 15.2.0, 12 repeats)
was: `10: 0.0162417 ms`, `50: 0.13225 ms`, `100: 0.261208 ms`,
`500: 2.47847 ms`, `1000: 7.96409 ms` (mean). This is a local scaling receipt,
not an SLA and not an authorization proof; the PR remains Draft.

Earlier exact-head CI completed successfully for the then-current #625
(`43666590301` / packaging `43666590356`), #626 (`43667644953` / packaging
`43667644948`) and #628 (`43666263091` / packaging `43666263072`). Those runs
remain historical evidence for those exact heads. The current #626 CI
(`43821058522`) uploaded
`exact-vector-cosine-benchmark-receipt`; its downloaded JSON has SHA-256
`967ee6ba8631c83bf39ea35604fe18f1c8212cb33451b9d1fd6d82bb4a26b2c7`, schema
version 2, source commit `ee974627819130004be62efc79c9b401bbe29ac2`, and all
three current top-k checks passed against the independent reference. The
updated receipt also includes the corrected-mode provenance and has digest
`sha256:3f4f902ee223e3ff0f227be9fde4e3d1de2da3df62bad7a57a801de5af385e81`.

## Exception safety, memory, race and lifetime matrix

| Component | Ownership / mutation | Failure behavior reviewed | Concurrency status |
| --- | --- | --- | --- |
| ExactVectorIndex | owns in-memory records; caller owns object lifetime | dimension, finite values and replacement validation; #628 compensation is non-allocating | concurrent read/read is only safe when the object is not mutated; read/write and write/write require external synchronization |
| MultiProbeHammingIndex | owns records and postings; mutation is owner-driven | staged replacement/rollback and deterministic allocation-failure controls are covered by existing tests | no independent immutable snapshot; do not infer concurrent mutation safety |
| HybridRetrievalEngine | references index and owns reranker | fail-closed admission on foreign, duplicate, non-finite or protected-field mutation | reconfiguration concurrent with retrieve is unsupported unless externally synchronized |
| MdbxExactIndexGenerationStore | owns durable generation rows and active pointer; read participant pins snapshot | incomplete build, failed activation, stale binding, reopen and rollback are fail-closed in the bounded exact profile | independent MDBX read transactions and pinned-generation lifetime are supported by the profile; callback remains transaction-bounded and must not be treated as arbitrary reentrant work |

No TSAN run is claimed in this environment. Ordinary CI and the focused CTests
are not substitutes for TSAN. An addressable bounded stress suite and a Linux
TSAN lane remain future gates for any concurrent R4 publication implementation.

## AN R4 gap matrix

| Required property | Current evidence | R4 gate still required |
| --- | --- | --- |
| durable candidate-to-unit/revision binding | bounded exact-generation profile | durable binding for the selected R4 topology |
| full index-space descriptor | exact profile checks model/recipe/dimension/metric/codec fields | bind topology, codebook and route configuration without canonical identity migration |
| generation publish/pin/reopen/rollback | `PASSED — EXPERIMENTAL EXACT GENERATION PROFILE` for the exact reference backend | repeat the matrix with retained embeddings and the chosen R4 backend |
| pre-top-k authorization | exact profile and #625 upstream admission checks | prove authorization in the future adapter, not only in a wrapper |
| stale revision/tombstone rejection | exact profile | cover every R4 candidate locator and restart path |
| bounded fallback and Partial/Unavailable | typed exact rebuild-unavailable path | explicit route-level semantics under budget exhaustion |
| retained vectors/model/recipe provenance | host-asserted FP32 in exact profile | cryptographically or operationally bound model/recipe provenance |
| codec/topology independence | exact profile rejects mismatched descriptor | no reinterpretation of lossy codes; separate compatible generations |
| quality/performance comparison | historical research receipts | canonical replay with identical corpus, manifests, qrels, latency scope and flat/Prototype-IVF/R4 controls |

The exact profile is not production AN R4, THQ, IVF, HNSW or a default route.
Reference limits such as 1024 rows, FP32, Original projection and dot/L2 are
not production R4 limits.

## Release-boundary verdicts

### Narrow Raw Markdown Technical Preview

`CONDITIONALLY READY — lexical/source-bound scope only`

The preview may remain ANN-neutral: canonical source/revision identity,
manifest/projection provenance, stale admission, exact hydration, MDBX/CAS
ownership, installed C++17 consumers, cold reopen/restore and the merged exact
generation compatibility proof are separate gates. Dense/ANN/R4 numerical and
performance debt is outside that narrow product scope. The first agent's
preview corrections and their exact-head CI remain required before publication.

### Future M1a / AN R4

`HOLD — experimental exact-generation evidence is necessary but insufficient`.

Required before production routing: one durable adapter using the existing
generation authority; canonical candidate bindings; descriptor validation;
pre-top-k authorization; stale/tombstone rejection; interruption/reopen/
rollback; retained vector and model/recipe policy; bounded Partial/Unavailable
semantics; canonical replay quality and latency receipts; and TSAN/ASan/UBSan
evidence or a documented executable alternative.

## Safe deferrals and blockers

Safe to defer: production ANN topology selection, SIMD optimization of the
correct kernel, broad index-manager abstractions, speculative admission maps,
R4 default activation, cross-filesystem atomicity and large-corpus performance
claims.

### Historical blockers versus current handoff

The original snapshot listed exact-head CI for #626/#628 as blocking. Those
checks are now successful for the recorded historical heads. #625 and #628
have been handed to the first agent for independent review and integration;
this research branch performs no merge.

Current blockers are deliberately different: independent review of the Draft
handoff, qualification of the new #626 production-path receipt, isolated
#625 admission-scaling evidence, and the future canonical Modern R4 replay.
No merge, tag, release or package publication is performed by this audit.
