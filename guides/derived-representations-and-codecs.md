# Derived Representations and Codec-Neutral Storage

Status: Proposal / compatibility analysis. This guide records contracts and
evaluation gates; it does not add a C++ API, DBI, codec dependency or runtime
implementation.

This guide is owned by the linked-knowledge work and complements
[`linked-derived-knowledge.md`](linked-derived-knowledge.md). The normative
milestone boundary remains [`milestones.md`](milestones.md). M0 remains
lexical-only. Optional dense/compact-vector work belongs to an explicit M1a
profile and must not silently change the M0 profile.

## Compatibility matrix

| Concern | Implemented today | Contract already present | Gap / next bounded slice |
|---|---|---|---|
| Canonical unit identity | `KnowledgeUnitEnvelope`, scoped `KnowledgeUnitId`, monotonic `revision` | `KnowledgeUnit` identity/history contracts | No generic aspect registry in the public C++ API |
| Text projection | `SearchProjection`, fixed `ProjectionKind`, `IProjectionStore`, MDBX adapter | revision-aware projection freshness and historical rows | User-defined aspects need a registered descriptor, not a new enum per domain |
| Projection key | `(scope, unit, kind, projection_revision)` in `unit_projections` | deterministic historical listing and delete semantics | Aspect/recipe identity is not yet a physical key; design only in this PR |
| Dense API | `Embedding` is portable `std::vector<float>`; `ExactVectorIndex` consumes it | model, purpose, dimension, metric and normalization contracts | Persisted codec-neutral vector record and native compact scorer adapter |
| Dense persistence | roadmap/manifest describe `embedding_meta` and `embedding_vectors` | multi-model and projection metadata | no general production implementation of THQ/LSQ/PLSQ/TQ/RSLM persistence |
| Compact scorers | native/research implementations and receipts under `tools/agent-memory-bench` | codec descriptors, quality and storage evidence in research docs | extract one bounded library adapter; do not reopen algorithm discovery |
| Text compression | optional Zstd direction and body/segment codec substrate | lossless digest/integrity expectations | measure adaptive row/block policy before selecting a default |
| Provenance | source revisions, evidence anchors, derivation contract | exact input revisions, frontier and generation | generic publication manifest independent of graph adjacency |
| Retrieval admission | lexical retrieval validates canonical revision/frontier | stale/access/incomplete outcomes | apply publication generation checks to lexical, dense and metadata hits |

The matrix deliberately distinguishes implementation from documentation and
research. A research scorer is not a production backend merely because its
quality receipt is complete.

## Generic representation model

One canonical unit may have any number of independently versioned derived
representations without changing the unit kind enum:

```text
KnowledgeUnit U@revision
  Representation identity:
    aspect_name + schema_id/version + recipe_id/version + model_id/version
  Materialization:
    generation + source frontier + publication state + retention policy
    text? / structured payload? / embedding? / physical index reference?
```

The stable logical identity is the tuple
`(scope, unit, unit_revision, aspect_descriptor, recipe_descriptor,
materialization_generation)`. The physical vector codec, index topology and
compression frame are representation details, not knowledge identity.

The model separates five concerns:

1. **Canonical information unit** — authoritative body, identity, lifecycle and
   source provenance.
2. **Derived information unit** — independently addressable knowledge with its
   own body, identity and evidence; it may participate in derivation and
   semantic relations.
3. **Search projection** — rebuildable view of a unit or derived unit for one
   aspect; it does not become evidence by being indexed.
4. **Materialized transformation** — the versioned output of a recipe/model,
   with exact inputs and a publication generation.
5. **Physical index/codec** — encoded bytes and candidate/scoring topology.

An interpretation becomes a derived `KnowledgeUnit` when it has its own
retained canonical content, lifecycle, access policy or citation surface. It
remains a projection when it is reproducible search material for an existing
unit and has no independent evidence or user-visible lifecycle. A derived unit
may itself have projections. No mandatory `Hypernode`, `RelationalFact`,
`SupportSet` or `Assessment` kind is introduced.

Technical derivation dependencies are authoritative publication provenance.
Relation occurrences and `graph_edges_by_src/dst` are optional materialized
views over that binding. A user-defined aspect therefore can be rebuilt with
zero semantic graph edges; adding a semantic Relation is an explicit product
choice, not a storage requirement.

### Publication manifest and transitive freshness

The canonical publication manifest stores the complete ordered input set once:

```text
publication_id, output_ref@revision, input_digest, input_count,
ordered direct inputs (unit/source revision + anchor), source_frontier,
recipe/schema/producer fingerprints, dependency_frontier_witness,
generation, publication_state, retention and access policy
```

If graph views are enabled, each Relation occurrence stores only its own
`publication_id`, output/input identity, occurrence digest and generation. It
does not repeat the full N-input list. This keeps an N-input publication
linear in N rather than quadratic.

`dependency_frontier_witness` is a canonical, recursively computed digest of
the direct input revisions and their committed witnesses. A reader validates
the witness for a bounded dependency budget: it checks the publication marker,
then each direct input's committed generation/witness, and follows only the
declared dependency frontier for derived inputs. A source update advances its
frontier epoch; any mismatch makes the current read `Stale` even when reverse
invalidation has been interrupted. The system never scans the whole corpus on
one query. A coordinator may later maintain reverse invalidation eagerly, but
the witness and read-time fence remain authoritative.

### Reader admission fence

Every retrieval path applies three distinct boundaries before returning derived
text, snippets or ContextPack material:

1. **Candidate-pool admission** resolves scope, model/aspect descriptor,
   metric and allowed index generation before any candidate is read. Indexes
   are scope-isolated and a pool cannot contain records from another scope or
   an incompatible representation space.
2. **Safe scoring** runs lexical, SIMD, packed or ANN scoring only over that
   admitted pool. Scores are not evidence and diagnostics expose only bounded
   counts, descriptor identities and redacted rejection reasons. Scores from
   incompatible aspect/model spaces are never fused without an explicit
   compatible policy.
3. **Final publication admission** validates unit revision, committed
   publication generation, frontier witness, lifecycle and effective
   authorization (including required upstream dependencies), plus the
   projection/index and codec descriptor. Only then may text, locators or
   ContextPack material be materialized.

Lexical, dense, ANN, metadata-filter and future hybrid indexes may contain
staged, stale or unauthorized candidates. Their presence is not publication
authority. A candidate that fails final admission is dropped with an explicit
diagnostic (`Stale`, `Unavailable`, `Partial` or `Unauthorized`) and cannot
leak through a snippet, locator or route trace. Per-byte authorization is not
required during SIMD scoring; the scope-isolated pool and mandatory final
admission provide the boundary. Flat original retrieval remains an independent
fallback.

## Aspect, representation and recipe identity

Human-readable names are API inputs, for example
`example/relationships`, `example/actor` and `example/goal-analysis`. The
storage design uses persisted dictionaries but does not make a local integer a
cross-database semantic identity.

### Decision

- `AspectId` identifies the semantic view being requested. It is a stable
  dictionary entry containing canonical name, schema identifier/version and
  scope/workspace ownership.
- `RepresentationId` is not a second global ontology identifier. It is a
  materialization key derived from `(AspectId, schema, recipe, model,
  preprocessing, generation)` and may be represented by a digest. A separate
  numeric field is unnecessary until measured fan-out requires it.
- `RecipeId` is a persisted dictionary handle for lookup only. The complete
  recipe descriptor, version and fingerprint remain stored and validated.
- Numeric IDs are never reused after deletion. Registration is an atomic
  create-or-validate operation; concurrent registrations of the same canonical
  descriptor converge, while conflicting descriptors fail.
- Dictionary scope is explicit: scope-local entries cannot be interpreted as
  workspace-global entries. Export/import carries canonical descriptors and a
  remapping table; local numeric values are rewritten at import.
- Reopen validates dictionary schema/version, descriptor digests and referenced
  IDs before admitting a representation. Unknown IDs fail closed.

The first physical comparison must measure three choices: repeated strings,
numeric IDs with a persisted dictionary, and numeric IDs plus metadata
compression. It must include key bytes, MDBX page occupancy, dictionary/index
overhead, point reads, cold reopen and import remapping. No new DBI is selected
without manifest and migration review.

## Text retention and rebuild policy

Text policy is part of the representation descriptor:

| Policy | Stored value | Rebuild guarantee |
|---|---|---|
| `RetainPlain` | exact UTF-8 materialized text | deterministic re-embedding from retained bytes |
| `RetainCompressed` | lossless frame plus codec/version/integrity metadata | same bytes after bounded decompression |
| `OmitRegenerable` | no intermediate text; canonical material and exact recipe retained | rebuild allowed only when replay is provably byte-equivalent |
| `Omit` | no intermediate text and no exact replay guarantee | return `RebuildUnavailable`, never infer text from a vector |

`Omit` applies only to optional search materializations. It cannot remove the
canonical body of a standalone derived knowledge unit. Every retained or
omitted representation records source unit revision, aspect/recipe identity,
producer/model and tokenizer/preprocessing identity, text digest when present,
active generation, index freshness identity and the policy reason.

The logical representation revision, embedding materialization revision, codec
encoding generation and physical index generation are independent. Changing a
recipe or model creates a new logical/embedding materialization. Re-encoding
the same embedding with a different lossless or lossy codec creates a new
physical encoding generation and, when an index is rebuilt, a new index
generation; it does not change the `KnowledgeUnit` revision or force extractor
or LLM work. A lossy scorer is not mathematically equivalent to FP32 without
its own proof. Existing rows remain historical until retention permits reclaim:

| Change | Existing materialization | New materialization |
|---|---|---|
| lossless text/vector codec only | same logical identity/provenance | new physical encoding generation; may reuse content digest after integrity check |
| embedding model/version | historical, not mixed | new model descriptor and generation |
| recipe/schema | historical, not mixed | new recipe/schema identity |
| tokenizer/template | stale for current search | new preprocessing descriptor |
| vector codec/codebook | historical codec remains readable if supported | new physical encoding and index generations; logical representation is unchanged |
| omitted text becomes required | unchanged | `RebuildUnavailable` unless exact source/recipe exists |

## Codec-neutral vector contract

`Embedding` remains the backend-neutral public API value (`std::vector<float>`)
for embedders, tests and exact reference paths. It is not a requirement to
persist FP32 for every document. A persisted vector record is independently
described by:

```text
model_id/version, aspect/recipe/template, dimension, metric, normalization,
codec_id/version, codec_config_digest, trained_artifact_digest,
scoring_capabilities, index_generation, storage_generation,
original_fp32_retained?, migration/rollback policy
```

Supported modes are separate configurations: FP32 exact baseline, INT8,
THQ candidate code, LSQ32/48, PLSQ, TQ/TQ+PQ, RSLM and future registered
codecs. A compact scorer may use lookup tables or bounded decode and need not
reconstruct FP32. It may claim only the metric/parity guaranteed by its own
descriptor and receipts. FP32 is an optional exact baseline, not a mandatory
production payload.

Route topology is independent: flat compact scan, Prototype-IVF/balanced
cascade and Modern R4 may share encoded payloads but have separate routing and
quality gates. THQ is not required by every route, and selecting an index must
not change `KnowledgeUnit` or aspect identity.

Research status is recorded on four axes:

1. research family closure;
2. source-bound quality and native scorer parity;
3. persisted MDBX/reopen/update/crash correctness;
4. production-selected profile.

The existing codec receipts establish substantial research and native-scoring
evidence, including fresh packed quality and MDBX screens. They do not by
themselves select a production default or prove the historical Modern R4 route
on fresh qrels. Historical Modern R4 and the fresh R4-shaped surrogate remain
separate evidence rows.

### Recovered evidence matrix

The following is a documentation index of completed evidence, not a new
benchmark claim. Payload values are final-code bytes per document and exclude
shared routing artifacts unless the cited receipt includes them.

The source records are [`codec-evaluation-report.md`](experiments/codec-evaluation-report.md),
the [`codec closure protocol`](experiments/2026-09-25-codec-closure-protocol.md),
the [`serving-mode bake-off`](experiments/2026-10-02-serving-mode-bakeoff.md),
the [`fresh packed quality report`](experiments/2026-10-03-fresh-packed-quality-and-layouts.md),
and the [`MDBX finalist storage bake-off`](experiments/2026-10-04-mdbx-finalist-storage-bakeoff.md).
This guide indexes those receipts; it does not regenerate or reinterpret their
historical measurements.

| Family | Completed evidence | Fresh/quality note | Persistence/selection status |
|---|---|---|---|
| FP32 | exact teacher/reference and exact-index controls | oracle for matched qrels | supported reference mode; not required in every profile |
| INT8 | native packed scorer and full-corpus controls | fresh packed decomposition: nDCG@10 `0.661169` (Prototype-IVF), `0.646156` (R4-shaped surrogate) | research/native evidence; no production selection |
| LSQ32 / LSQ48 | native packed scoring and closure receipts; 32/48-byte code plus 4-byte side metadata in the cited layout | historical mean nDCG@10 `0.657264` / `0.661515` | research/native evidence |
| PLSQ8x4x8 / PLSQ8x6x8 | native packed scoring and closure receipts; 36/52-byte layouts | fresh `0.640796` / `0.651938` on the two route surrogates | research/native evidence |
| TQ1 / TQ1+PQ8 | native packed scoring and closure receipts; 52/64-byte layouts in cited runs | fresh `0.666707` / `0.668428` on Prototype-IVF; R4-shaped values remain surrogate evidence | strongest current fresh candidate, not selected default |
| RSLM1 | native packed scoring and closure receipts; approximately 52-byte code plus layout-dependent sidecar | fresh `0.654728` / `0.638493` on the two route surrogates | research/native evidence |
| THQ3/THQ4 | candidate filtering and route-specific native measurements; THQ4 96-byte shell in the north-star arm | quality depends on route and downstream final scorer | route component, not universal codec/default |
| Modern R4 | historical full-route measurements and separate fresh R4-shaped prototype | the canonical historical route is not identical to the fresh surrogate | route research; canonical fresh replay remains a gate |

These rows are intentionally split from crash/reopen/update correctness. A
quality receipt, packed scorer parity result or MDBX footprint screen does not
close the durable publication and migration gates.

### Optional text compression gate

Zstd remains optional. A future measurement compares plain rows, per-row
frames, adaptive plain/Zstd, independently readable blocks and dictionary
compression over 32/64/128/256/512/1024+ byte repeated and random texts. It
records p50/p95/p99 decode, point/sequential/batched reads, update cost,
reopen, corrupted payload rejection, maximum decompressed size and whole-MDBX
page footprint. Lossless recompression does not change content identity or
provenance. Reuse the existing body/segment codec substrate where possible;
do not add a dependency or select a default from Z0 vector results alone.

## Dataset and evaluation specification

No large embedding benchmark is required for this docs pass. Existing MIRACL
and DE-1M fixtures remain useful for lexical/dense and codec quality, but they
do not provide arbitrary-aspect ground truth. A future matched dataset has two
layers:

**Structural conformance fixture:** role inversion, repeated names in distinct
objects, multiple events, alternative relations, short/multi-hop questions,
contradictory and dependent sources, source revisions, recipe changes and
withheld evidence. It must test source/recipe updates, scope isolation,
reopen/crash and unauthorized hydration.

**Realistic retrieval evaluation:** independently annotated entity/role and
relation quality, aspect coverage, linked-source correctness, rare-fact and
multi-hop retrieval, Recall@K/nDCG@K/MRR, and correctness after source/recipe
updates. The model that generated a projection is not its sole ground truth.

Planned comparison arms are Original lexical/dense, Original+QA,
Original+arbitrary aspects, metadata/role filters, and bounded exact
dependency joins. Negative controls cover leakage, false role matches and
graph/path hallucination. Offline extraction, embedding build, index bytes,
retrieval latency and final evidence quality are reported separately.

## Decision matrix and handoff

| Alternative | Decision | Reason |
|---|---|---|
| Mandatory domain-specific unit kinds | Reject | couples storage to one ontology and expands wire/API surface |
| Mandatory graph for every derivation | Reject | ordinary aspects need no semantic edges; provenance remains authoritative |
| Local numeric ID as global identity | Reject | breaks export/import and cross-scope interpretation |
| Persist every intermediate text | Optional | simple and reproducible, but not always space-efficient |
| Omit all intermediate text | Reject | makes rebuild silently impossible; use typed `RebuildUnavailable` |
| FP32 persisted for every document | Reject as requirement | compact native scorers already exist; keep FP32 as selectable baseline |
| THQ/R4 universal default | Reject | route and codec remain independent, production gates incomplete |
| New compression stack now | Reject | reuse substrate and measure before dependency/DBI changes |

The first implementation handoff is intentionally narrow: extract one
existing native packed scorer into a library adapter with an immutable codec
descriptor, bounded encoded payload, metric/normalization checks, FP32 oracle
comparison, reopen/update/stale/crash tests and a migration refusal path. The
adapter must not alter M0 or require a new aspect dataset. Prototype-IVF and
Modern R4 routing remain separate optional interfaces. Existing native scorer,
receipt and audit code under `tools/agent-memory-bench` is research input, not
production API.

## Non-goals and acceptance invariants

This pass does not implement a codec, add a DBI, add Zstd, change a wire enum,
modify C++, activate dense retrieval, or rewrite historical experiment
results. Before an optional dense preview is accepted, it must prove:

- dictionary reopen/import remapping and non-reuse of deleted IDs;
- representation descriptor and codec artifact identity;
- exact scope/unit/revision and publication-generation admission;
- plain/compressed/omitted retention behavior and typed rebuild failure;
- stale, unauthorized, missing-source and corrupted-payload rejection;
- crash/reopen, update and migration/rollback behavior;
- native compact scorer quality/parity and whole-system MDBX footprint;
- independent flat fallback and no cross-aspect score fusion without a
  compatible descriptor/policy.

## Documentation overlap and integration order

This guide adds contract language only. It does not absorb implementation or
research branches:

- #565 remains the product/ANN readiness audit; its historical status table
  must distinguish research-validated compact codecs from production-integrated
  vector storage.
- #563 remains the publication-stack integration audit; its coordinator is not
  assumed by this proposal.
- #518 remains a separate adaptive/graph documentation line and must not become
  a second aspect/provenance authority. Any overlap is resolved in favor of
  this guide's generic publication manifest and the existing owner contracts.
- #520 is repository metadata only and has no representation or codec scope.

The three small documentation candidates may be integrated sequentially only
after exact-head, base, changed-file and CI checks, with explicit user
authorization. This PR remains independent and Draft until semantic/storage
review accepts the generic contract.
