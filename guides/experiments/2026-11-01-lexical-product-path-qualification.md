# Lexical product-path performance qualification

Date: 2026-11-01
Branch: `research/lexical-product-path-executable`
Experiment family: `lexical_product_path_performance_qualification_v2`

## Question

How costly is the current `ProjectionAwareLexicalRetriever` path that scans
the scope and rebuilds an `ExactLexicalIndex` for every query, and what can be
observed from the existing persistent lexical adapters without changing
production retrieval?

## Setup

The CTest harness is
`agent_memory_mdbx_lexical_product_path_qualification_test`. It imports a
bounded synthetic corpus of 48 documents per scope into MDBX, uses the public
`ProjectionAwareLexicalRetriever::retrieve()` API for the rebuild baseline,
and exercises `MdbxPersistentLexicalIndex` and `MdbxSegmentedLexicalIndex`
through their existing `upsert()` publication paths. The baseline has three
warm-ups and 12 timed invocations in one process. Persistent adapter timings
are one build sample: these adapters do not expose a product query API, and
their read snapshots pin a transaction. The receipt therefore uses
`PERFORMANCE_INCONCLUSIVE` and makes no speedup claim.

Command:

```text
ctest --test-dir <build> -R agent_memory_mdbx_lexical_product_path_qualification_test --output-on-failure
```

The CTest receipt is written to the build directory as
`lexical-product-path-qualification.json`.

This follow-up also runs the deterministic workload generator
`tools/lexical-research/generate_product_path_workload.py` in the normal MDBX
CI job. The C++ qualification test consumes the frozen smoke fixture for 96
documents and two scopes; small (512/4) and medium (2,048/8) profiles remain
executable but intentionally manual-gated. Each executed record binds
deterministic IDs, source/resource revision, normalized text, derived fields,
a body digest and a fixture-record digest. The receipt records the workload
profile and exact document count, so the measured smoke path cannot silently
fall back to a separately constructed corpus.

## Historical #578 directional result

| Path | Samples | Median (ms) | p95 (ms) | Semantics |
| --- | ---: | ---: | ---: | --- |
| `ProjectionAwareLexicalRetriever::retrieve` | 12 | 3.08 | 3.17 | Full public query path, including scope scan, projection reads, tokenization, and per-query index rebuild |
| Persistent flat `upsert` build | 1 | 41.32 | 41.32 | One publication build; not a query latency |
| Segmented `upsert` build | 1 | 501.00 | 501.00 | One publication build; not a query latency |

The executable smoke receipt from GitHub MDBX job `42185830415` (96 documents,
two scopes) recorded the following run-pinned values. Later CI reruns are
independent directional snapshots and do not rewrite this receipt:

| Stage | Samples | Median/measurement (ms) | p95 (ms) | Evidence status |
| --- | ---: | ---: | ---: | --- |
| source import | 1 | 181.636408 | n/a | measured smoke sample |
| public rebuild query | 12 | 0.5361825 | 0.55412195 | measured sample distribution |
| targeted update/reindex | 1 | 1.846191 | n/a | measured smoke sample |
| persistent flat publication | 1 | 13.713391 | 13.713391 | measured build, not query |
| segmented publication | 1 | 42.046046 | 42.046046 | measured build, not query |
| persistent reopen/current read | 1 | 0.255655 | n/a | diagnostic snapshot read |
| segmented reopen/current read | 1 | 0.548796 | n/a | diagnostic snapshot read |

All receipt correctness gates passed: freshness, both scope identities and
foreign-marker rejection, persistent/segmented correctness, and lifecycle
smoke. The receipt still declares `PERFORMANCE_INCONCLUSIVE`; one local CI
run is not a capacity curve, and publication timings are not query latency.

## Persistent candidate-generation decision

The current persistent adapters expose publication and snapshot inspection, but
not a product-level candidate query contract carrying typed scope, projection,
revision, generation and completeness evidence. Therefore a parity prototype
would either bypass final admission or duplicate the #579/B3 contract. That is
not an acceptable comparison. The decision is `DEFERRED`: retain the rebuild
path as the correctness baseline and design the smallest candidate seam only
after the snapshot/CAS contracts are available. No persistent or segmented
backend is enabled by this PR.

The public retriever checks pass freshness and scope isolation. The executable
receipt now records source-import and targeted-update timings in addition to
query, publication and reopen timings. Internal candidate materialization,
tokenization, scoring and final-admission stages remain explicitly
`NOT_MEASURED` because the public API does not expose stage hooks. Both persistent
adapters complete their bounded publication build, exercise revision replacement
and erase, reopen the resulting snapshot, and verify that a second scope does
not leak into the first. Reopen timings are diagnostic only; they are not
product query latency.

## Interpretation and limits

- This is one Ubuntu CI directional snapshot, not production performance
  evidence or a cross-platform performance claim.
- `sample_count` means timed invocations in one process; it is not a count of
  independent requests or independent runs.
- The harness measures the public end-to-end retrieval call. It does not claim
  separate timings for scope scan, storage reads, freshness checks,
  tokenization, scoring, or result materialization because those stages are not
  instrumented by the current production API.
- The 48-document-per-scope fixture is one bounded smoke workload, not a
  scaling curve. Only the generated 96/2 smoke profile is executed in this
  slice; 512/4 and 2,048/8 remain workload definitions pending manual runs.
- MDBX row/byte counters and allocator/peak-memory values remain
  `NOT_MEASURED` in this slice; no zero or inferred values are presented as
  observations.
- p95 is descriptive for this small sample distribution. It does not establish
  a stable p99 or a capacity SLO.
- The persistent adapters currently expose durable state and snapshot views,
  not an equivalent product query API. Any latency comparison against the
  public retriever is therefore `PERFORMANCE_INCONCLUSIVE`.
- The corpus is synthetic and small. The measurement identifies the current
  rebuild bottleneck and confirms the existing adapter seams; it does not
  justify changing production defaults or MDBX schema.

## Follow-up

After #576 is integrated, rerun this harness on the pinned R3A corpus and
retain separate measurements for query execution, index publication, snapshot
materialization, and reopen. The current branch does not claim R3A execution
or quality. A persistent adapter should only be wired into the product path
after an equivalent query contract and freshness/scope proof exist.

## 2026-11-22: full persistent candidate-call baseline

This is a new experiment using the opt-in candidate API from Draft #683, not a
relabeling of the historical rebuild/R2 measurements above. The question is
whether TF and BM25 agree with independent arithmetic on identical inputs,
and how the complete `generate_window()` cost scales with active flat state.
This does not complete product lexical Phase 1 or enable a new default.

The native target is
`agent-memory-persistent-lexical-candidate-qualification`; its runner/validator
is `tools/lexical-research/validate_persistent_candidate_qualification.py`.
It invokes the production adapter, including tokenization, MDBX snapshot
reconstruction, postings, scoring, selection, result creation and destruction.
It does not substitute a separately timed scalar scorer for the public call.

The full matrix has 144 arms: N=32/128/512, fixed 16-token or mixed 16/128-token
documents, six query classes (common, rare, absent, repeated, technical
identifier, Russian/English), K=1/10 and TF/BM25. The smoke matrix omits N=512
(96 arms). Each matched pair uses the same database and ordered token-corpus
SHA-256. K changes the request limit, not the unadmitted window; equality of
the returned windows is checked. TF remains the default, including old
three-field aggregate options and legacy `generate()` limiting.

The independent double oracle computes collection lengths, DF and scores from
input tokens without calling the production BM25 helper or reading snapshot
statistics. It checks every candidate ID/order/score, including empty results
and ties, with absolute/relative tolerance 1e-5. Analytic BM25 goldens and
score/identity/nonfinite mutations test the oracle comparison. Replacement
uses explicit expected-current identity; erase and same-process close/reopen
are checked against the new corpus. This is not a crash/restart test.

After two warm-ups, seven fixed-query samples alternate TF/BM25 execution
order. C++ ordinary/aligned `new` counts and cumulative allocated bytes are
measured in separate calls, with counters disabled for latency samples. These
are not MDBX malloc/mmap, live memory, peak memory or RSS. Independent
`read_snapshot()` and tokenizer timings are diagnostics: their medians cannot
be subtracted from the full call to infer internal stage costs. No internal
postings/scoring/selection stage attribution is claimed.

The source binding covers the sorted union of the explicit evaluator/build
list and every tracked file under `src/` and `cmake/`, hashed as ordered
`path:sha256\n` records. Receipts also record exact compiled checkout, expected
HEAD, actual executable hash/size, compiler/configuration/flag hashes, platform,
pointer width, pinned dependency revisions and compiled JSON version. Qualified
runs require actual in-tree libmdbx/mdbx-containers targets in BUNDLED mode and
clean source/dependency checkouts. JSON version is observed, not a complete
third-party header-source attestation. The executable hash identifies its
linked bytes; these records are reproducibility checks, not signed execution
attestation.

The expected HEAD must be the checkout or the second parent of a two-parent
merge (CI may compile the synthetic PR merge). Receipt validation requires the exact measured
checkout and executable; a later documentation commit must not rebind old raw
data to itself. The SHA-256 sidecar detects receipt-byte changes. CI runs the
native/validator controls and smoke matrix for research or storage changes,
and uploads the receipt plus sidecar.

Build with JSON, benchmarks, tests, research tests and MDBX enabled, BUNDLED
dependencies, and a Release C++17 compiler. Set
`AGENT_MEMORY_CANDIDATE_EXPECTED_HEAD` explicitly on every measured configure;
a Git HEAD change alone does not refresh CMake definitions. With a clean
committed checkout:

```text
ctest --test-dir <build> -C Release --output-on-failure -R persistent_candidate_qualification
python tools/lexical-research/validate_persistent_candidate_qualification.py --source-root . --binary <executable> --expected-head <measured-sha> --output <build>/candidate-full.json
python tools/lexical-research/validate_persistent_candidate_qualification.py --source-root . --binary <executable> --expected-head <measured-sha> --receipt <build>/candidate-full.json
```

This protocol is a directional synthetic mechanics/cost baseline. Seven
repeated invocations are not seven independent requests or process trials;
they do not establish stable p99, a capacity SLO or a production threshold.
Applied query quality remains unqualified. The existing manually judged
synthetic English fixture is not representative applied qrels; the R3A corpus
has no qrels. Neither is silently promoted into new quality evidence.

For a future flat-snapshot optimization, preserve this source-bound workload,
all IDs/order/scores and completeness/lifecycle/admission controls. Compare
full-call cost, including absent/common queries and both length profiles;
do not move reconstruction outside the timer. Require independent process
trials and environment control before claiming a stable performance change.
Lower scorer-only latency or a faster incomplete window is not equivalent.
No storage schema, production default, metadata/access authority or shared
canonical/lexical frontier is changed by this qualification.
