# Algorithm Research Sweep

## Scope and source identity

This registry records the algorithm research surface at `main` commit
`e34a59a19abc4346cd541bd0ce139df098eb011e`. It is a research planning and
evidence index; it does not change production APIs, ABI, persisted formats,
retrieval defaults, or dependency policy.

The machine-readable inventory is
`2026-11-18-algorithm-research-sweep-registry.json`. Validate it with
`python tools/agent-memory-bench/validate-algorithm-research-sweep.py` and run
its mutation controls with `--self-test`.

Every executable stage must record the exact source SHA, compiler and build
identity, corpus/fixture digest, seed, raw samples, correctness oracle, and
mutation controls. Kernel-only or synthetic timings must not be presented as
production latency. A registry row is not evidence merely because a linked
file exists: `evidence_status=PRIMARY` is reserved for an independent,
execution-bound receipt, while `EVIDENCE_PENDING` rows deliberately carry no
evidence references. `SUPERSEDED` rows retain historical pointers only and
cannot qualify a result.

The `cross_references` object on each row points to the relevant Draft PRs and
research guides. Those links provide review context, not proof of execution.

PR #641 is the feasibility and limitations note for E-means/O-means. The
executable comparison is owned by PR #638; no second executable study is
created from the feasibility note. New routing documents may use `PRR`
(Prototype–Representative Routing) and `PR-IVF` (Prototype–Representative
Inverted File); archived R4 documents retain their original names.

## Inventory

| Status | Surface | Evidence and disposition |
| --- | --- | --- |
| `ALREADY_IMPLEMENTED` | Exact float/vector search, SIMD similarity, binary encoders, flat and multiprobe binary indexes, lexical tokenization/BM25, and `HybridRetrievalEngine` scaffolding | Covered by source tests and existing benchmark/evaluation receipts. The hybrid engine is not evidence of BM25+dense fusion quality. |
| `ALREADY_IMPLEMENTED` | Binary and lexical evaluation harnesses, provenance/receipt conventions, and exact-result controls | Reuse these controls; do not create a second receipt format for a new algorithm family. |
| `NEEDS_CORRECTION` | Early scalar and PQ/OPQ reports, the first RTHQ ITQ orientation row, and Python ANN timing claims | Use the corrected seed manifest, full-calibration report, corrected RTHQ note, and native measurements. Superseded reports remain historical and must not be used for selection. |
| `WORTH_BENCHMARK` | Native packed-LUT scalar ADC/top-k; native MIH layouts under matched budgets; float prototype-IVF and compact THQ/scalar cascades; coordinate-aware ordinal multiprobe; matched-byte PQ/OPQ; hybrid BM25+dense fusion | Each is a separate research stage with a separate baseline, quality gate, and timing scope. No stage implies production adoption. |
| `PREVIOUSLY_REJECTED` | Binary Hamming as a final ranker, label-free learned ADC, equal-payload uncertainty-mask/two-plane ternary controls, tiny-corpus multiprobe selection, and production HNSW adoption from Python-only timing | Preserve the negative evidence and do not reopen these branches without a new falsifiable mechanism and a matched control. |
| `DEFER` | Production IVF/HNSW/PQ/OPQ classes, classical cosine-LSH, decoder-only autoencoder ranking, and morphology/SPLADE/ColBERT integrations | Require a representative corpus and a defined serving contract before implementation. |

## Staged research work

### Stage 0 — corrective closure

PR #635 (`research/exact-numerical-frontier`) and PR #633
(`research/reranker-admission-scaling`) remain independent Draft PRs. Their
acceptance evidence is exact-head CI plus local focused tests and receipt
inspection. They are prerequisites for independent review, not production
changes.

### Stage 1 — native exact-result controls

Measure only after the correctness baseline is fixed:

* full-result sorting versus bounded top-k selection in `ExactLexicalIndex`;
* `MultiProbeHammingIndex` probe-state initialization and duplicate handling;
* packed-LUT scalar ADC and top-k selection with an independent exact oracle.

For each candidate, report build/index cost separately from lookup cost,
allocation/copy cost, exact top-k parity, mutation failures, and raw timing
samples. A speedup is not actionable when it changes tie ordering, filtering,
or freshness semantics.

### Stage 2 — quantization and ANN feasibility

Compare TurboQuant/RaBitQ/THQ/ITQ/PQ/OPQ only on a matched corpus, payload
budget, training budget, and query set. Separate training time, encoded size,
held-out quality, candidate generation, exact rerank, and end-to-end lookup.
For E-means/O-means and IVF, first produce a design/feasibility note covering
centroid counts, training corpus size, empty clusters, held-out assignment
error, recall, retraining cost, and whether offline training can feed native
C++ serving. In the agreed new-study terminology, prototype–representative
routing is `PRR` and its inverted-file study is `PR-IVF`; these names apply to
new documents only and do not rename archived R4 material. GPU/CUDA/Triton work
is offline-only; unavailable hardware is reported as `NOT_EXECUTED`.

### Stage 3 — lexical and hybrid retrieval

Before WAND/MaxScore/Block-Max WAND, identify the measured bottleneck among
scoring, candidate generation, I/O, filters, and sorting. Any pruning method
must prove upper-bound correctness and exact top-k parity. Separately evaluate
lexical-only, dense-only, lexical+reranker, RRF, score normalization, and
optional query-adaptive fusion on a sealed quality fixture. Report worst-query
regressions, source loss, freshness/revision behavior, and filter
authorization. No new production default follows from an offline receipt.

## Acceptance contract

Every stage must publish:

1. hypothesis and named baseline;
2. exact source and environment identity;
3. corpus, seed, fixture digest, and parameters;
4. independent correctness oracle and negative/mutation controls;
5. raw samples with explicit timing scope and sample-count semantics;
6. quality, memory, and failure-path results;
7. limitations, unresolved inquiries, and a bounded status.

The registry's `evidence_status` is intentionally separate from the algorithm
`status`: an implemented or benchmark-worthy surface can still be
`EVIDENCE_PENDING` until a primary receipt is produced. Primary receipt paths
must contain an execution/status field and a digest binding the recorded result
to its inputs or runner; a design note, source file, or this registry never
qualifies as a primary receipt.

Allowed statuses are `READY_FOR_INDEPENDENT_REVIEW`, `RESEARCH_QUALIFIED`,
`CORRECTIVE_REQUIRED`, `DEFER`, and `NOT_EXECUTED`. A research result never
activates a production default. Merge, release, package publication, and
production activation remain outside this sweep.

## Next actions

1. Independently review #635 and #633 at their rebased exact heads.
2. Open one Draft PR for the native exact-result controls, keeping benchmark
   code and receipts separate from production index changes.
3. Open separate design/feasibility PRs for quantization/ANN and hybrid
   retrieval only when the preceding evidence gates are satisfied.

## R5.5 appended execution ledger

The inventory above remains the audit at `e34a59a19abc4346cd541bd0ce139df098eb011e`.
Its `source_commit`, algorithm statuses and pending evidence classifications are
not rewritten to a later checkout. The separate `integration_baseline_commit`
is `bfa01a22d4091bb5c5f7c17606199fda6342b790`. This separates historical source
identity from the baseline used to combine the research PRs.

The JSON `evidence_ledger` indexes bounded executed research, not production
qualification. Each entry names its owner PR, latest code SHA, actual run source
and checkout, environment, receipt locator and byte digest, timing scope,
limitations and decision. Compact receipts and their sidecars remain owned by
their research PR; raw dumps and local MSVC reports remain external or in
per-head CI artifacts. They are not copied into this registry PR.

| Owner | Recorded execution | Bounded decision |
| --- | --- | --- |
| [#664](https://github.com/LimiNode/agent-memory-cpp/pull/664), exact vector | v2 source `6272e7b2`, GNU receipt plus sidecar; actual local MSVC source `1fc9c8b3`; six correctness cases, ten timing cases and nine mutations | `KEEP CURRENT` compensated cosine. Sequential FP64 loses the cancellation term; FP32 also fails tiny cosine. Arithmetic controls are not replacement indexes. |
| [#664](https://github.com/LimiNode/agent-memory-cpp/pull/664), E-means | v2 source/checkout `6272e7b2`, compact receipt plus sidecar; two real CPU methods and 13 negative controls | `DEFER`, synthetic smoke only. Full training input and measured assignment work are matched; update cost and wall time are not. Native IVF scans and the Python capped-scan proxy are distinct. |
| [#665](https://github.com/LimiNode/agent-memory-cpp/pull/665), lexical top-k | Historical Linux source `7a99add2`/synthetic checkout `9724be45`; separate final `59ce09c3` GNU and actual MSVC local receipts | `CONDITIONAL` follow-up only for large N/small K. Near-N, ties and no-match cases prevent a universal-selector claim. |
| [#666](https://github.com/LimiNode/agent-memory-cpp/pull/666), admission/copy qualification | Actual MSVC source/checkout `df17946c`; compact receipt plus sidecar; three focused CTests, 18 cases/648 samples, 11 fail-closed controls per arm | `CONDITIONAL` crossover on this synthetic regime; `KEEP CURRENT` production default. |

For E-means, v1 is `INVALID_FOR_COMPARISON`: unequal effective training inputs
and moving-pass MSE labels invalidated the old timing ratio and quality
comparison. Historical receipt bytes and identities are preserved rather than
rebound to v2. Peak RSS is `NOT_MEASURED`; O-means, GPU training, representative
R4 data and portable native serving remain unqualified.

For lexical top-k, complete search includes the fixed in-memory metadata/filter
and returned-value work, but stage attribution is `NOT_MEASURED`. Authorization,
tenant isolation and revision freshness are `NOT_EXERCISED`. Repeated timings
are samples, not independent requests or evidence of stable tail latency. The
old successful Linux CI run remains evidence for its original source, not the
latest HEAD. Neither CI success nor this guide promotes an inventory row to
`PRIMARY`, changes a production default, or establishes representative quality.

The initial R5.5 validator checked ledger identity, owner, execution, digest and
scope fields without fetching or scientifically re-validating another PR's
receipts. It retained 14 earlier rejection controls and added six ledger
counterexamples: `PRIMARY` promotion, missing receipt digest, malformed
execution SHA, activation, non-project owner and CI/docs-only evidence. The
closure below adds compact-byte/disposition guards; owning validators continue
to enforce the full raw-result contracts.

R5.2 measures only `retrieve()` in its primary timer: fixture-index search,
deterministic reranker, per-query lookup-index construction, admission checks
and response materialization are included; fixture/index/engine setup is not.
The wider setup-plus-retrieve timer is separately recorded and not used for
break-even. The first observed mean sorted/hash crossovers were `500/50`,
`500/100`, and `100/100` candidates at metadata widths 2, 8, and 32. These are
machine-dependent observations, not stable thresholds or production choices.
The generated arms remain research-only, with unchanged production source.

## R5 closure: dispositions and portable validation

There is still one algorithm inventory and execution ledger. The canonical
[R5 historical disposition manifest](https://github.com/LimiNode/agent-memory-cpp/blob/76914c0c3422407325aaf097622fbd5478de2ab1/guides/experiments/2026-11-20-r5-evidence-dispositions.json)
is owned by #664, not copied into a second registry. This registry pins that
owner commit, repository path and byte digest. The owning validator preserves
all six historical raw/sidecar/receipt files and checks their v2 replacements.
E-means v1 is `INVALID_FOR_COMPARISON`; vector v1 is `SUPERSEDED`. Neither can
be promoted to `PRIMARY` or used for a current research/production decision.
Two small offline receipt tombstones in this checker reject both old paths
and renamed old receipt bytes; the full reasons and artifact inventory remain
in the manifest. Original `EXECUTED` fields are historical occurrence, not
validity or a current source claim.

The ledger now separates `production_default_decision=KEEP CURRENT` from the
bounded research decision. A run also names its experiment so that another
algorithm's receipt cannot be silently attributed to it. The seven external
receipt/source/checkout identities already recorded above are explicitly
pinned in the checker: replacing a source with another plausible full SHA or
substituting an unknown receipt does not pass. New external bytes require an
intentional binding update after owner validation, not an automatic checksum
refresh. These pins do not fetch the raw artifact or constitute scientific
replication.

`EXTERNAL_POINTER_NOT_REPLAYED` means a pinned public blob/Actions pointer,
not a locally replayed receipt. Historical host-specific pointers are marked
`UNAVAILABLE_HISTORICAL_POINTER`; their old source identities and digests are
preserved. `LOCAL_BYTES_VERIFIED` requires an existing repository-relative
receipt, the actual byte digest, matching sidecar, matching experiment schema,
execution status and actual source/checkout fields. In a combined checkout the
checker additionally validates available pinned blob-receipt bytes and their
sidecars. In standalone #640, absent #664/#666 files remain explicit external
pointers and are not called locally verified.

The default validator and self-test remain in the independent Documentation
checks lane. The self-test now has 41 rejection controls, including revoked
evidence promotion, missing manifest, valid-looking provenance substitutions,
nonexistent locally verified evidence, external-as-local claims, cross-arm
attribution, production-default activation and actual receipt/sidecar mismatch.
The owner validators still check full raw/source/generated/fixture contracts.

The representative E-means comparison is closed as
`NOT_EXECUTED: REPRESENTATIVE_CORPUS_UNAVAILABLE`, separately from the executed
v2 smoke. #664 checked both pre-existing DE-1M payload locators from the source
recovery receipt; both were absent on the inspection host. This does not deny
the existing 1M/305-query frozen corpus history or invent a real query-size
distribution. Compensated cosine remains `KEEP CURRENT`; no production policy
or dependency is activated by this closure.

## 2026-11-20 closure update: direct stages and current CI identities

The latest lexical and admission code is respectively
`f2bb63527f9d8289b5f592e89873641b552265ca` (#665) and
`fd29d257c4a6017f41870e3cf845125e7a473130` (#666). Their completed owner CI
runs [44644634821](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44644634821)
and [44645703444](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44645703444)
retain separate GNU/Linux and MSVC/Windows artifacts. The ledger adds those
four reviewed receipt identities without modifying any of the seven earlier
runs or their bindings. Earlier stage-availability and crossover observations
above describe their named historical revisions, not the closure v2 results.

Actions compiled synthetic merge checkouts `65a77fbd71f909b7b81b098063a9471da9a7284f`
for lexical and `50a3658359870cc5b8cb4d8a850986532f9477a2` for admission.
Each new row preserves that actual source/checkout separately from its expected
PR HEAD. The checker pins all four identities together with the receipt digest
and rejects their substitution or omission. The owner validators were replayed
against downloaded raw files, sidecars and the exact compiled source trees on
2026-11-20. This registry does not perform that replay: the rows remain
`EXTERNAL_POINTER_NOT_REPLAYED`, not `LOCAL_BYTES_VERIFIED`. Admission artifacts
use `production-reranker-admission.sha256.json`, not a text `.json.sha256`
sidecar. The checker self-test adds 16 closure-provenance rejection controls,
bringing its total to 57.

Lexical v2 adds four explicitly `SYNTHETIC` document/token/metadata/filter
profiles. Separate intrusive timers directly observe traversal, filtering,
BM25 scoring, candidate construction, selection, final vector truncation and
full search. Metadata copying belongs to candidate construction, not the final
truncation stage. Timed stages are not inferred from kernel subtraction and
must not be substituted for uninstrumented latency. Actual HybridRetrievalEngine
filter/source-preservation controls pass; authoritative authorization, tenant
isolation and revision freshness remain `NOT_EXECUTED` at this boundary.
The selection decision remains `CONDITIONAL`; production defaults `KEEP CURRENT`.

Admission v2 separately measures index search, trusted-pool copying, reranker,
admission, lookup construction, validation, trusted-result copying and response
materialization. Nested instrumented scopes are not additive or interchangeable
with the primary uninstrumented `retrieve()` timer. The expanded matrix includes
sorted trusted input and early/late repeated-ID rejection controls. Actual
candidate-count frequencies remain `NOT_EXECUTED`, with reason
`REPRESENTATIVE_REQUEST_DISTRIBUTION_UNAVAILABLE`; synthetic case counts are not
request frequencies. Historical crossover numbers retain their old source
attribution. Admission remains `CONDITIONAL`, conditional dispatch `DEFER`, and
the production default `KEEP CURRENT`.

Both studies leave peak retained workspace/process RSS `NOT_MEASURED` and make
no stable-tail claim. These updates close bounded measurement gaps; they do not
qualify production activation or replace the independent combined integration
gate owned by the closure assembly.

## R6 bounded certified-ADC follow-up (2026-11-20)

R5 integration is closed on actual `main@9005a010b1044e1c93ef3690d41fd56a2c7bbb2e`;
the [inventory PR #676](https://github.com/LimiNode/agent-memory-cpp/pull/676)
records the current source map without replacing this registry's historical
identities. The [FastScan experiment](2026-09-21-thq-fastscan-v3.md#executed-bounded-mechanics-result-keep-the-existing-pair-avx2-arm)
adds a research-only certificate/refinement pilot on frozen source
`a587fa326d25b40a92dacf09ea93c772b9fcaa9c`.

It passes bitwise pair-ADC correctness but loses the full persistent-dense
component comparison against existing pair AVX2 in all six fixed N/K profiles:
720 samples, eight exposed queries, 3.91–7.33 times the baseline median.
Disposition: `KEEP_CURRENT` for this scalar-screen/fixed-tau implementation.
This is not a family-wide rejection, raw-float quality result, fresh held-out
evaluation or authorization to optimize production. K8 screening and sparse
replication remain `DEFER` for missing prerequisites, not disproved methods.

Raw/manifest digests and exact local commands are owned by the experiment note.
The evidence remains local, with public full-source replay `NOT_ESTABLISHED`;
no machine-readable row is promoted to `PRIMARY`. No historical receipt,
invalidation identity, registry binding or production-default decision is
rewritten. Both new R6 PRs remain independent Draft changes on the R5 baseline.
