# Evaluation and Benchmark Roadmap

Status: `Normative` for retrieval and index claims. This guide defines what a
future implementation PR must measure; it does not require a benchmark
dependency in the core library.

## Evaluation ladder

1. **Contract tests** — deterministic ordering, tie breaks, scope/filter
   semantics, reopen and delete behaviour.
2. **Exact oracle** — a small in-memory or brute-force implementation used to
   measure recall and ranking loss for every approximate or compressed path.
3. **Component benchmark** — isolated tokenizer, scorer, encoder, index,
   decoder, or reranker cost.
4. **End-to-end benchmark** — ingestion, index publication, query execution,
   candidate generation, reranking, and context materialization.
5. **Operational replay** — warm/cold cache, restart, update/delete, rebuild,
   and crash-recovery cases.

A passing lower rung never implies that a higher rung is passed.

## Encoder gate before ANN and codec fitting

Embedding-model comparisons have a separate E0 gate before any ANN, THQ, or
codec work. The encoder-only lane evaluates exact FP32 retrieval on the same
corpus, tokenizer-bounded chunks, query order and qrels. It records nDCG@10,
MRR, Recall@128, query/corpus encoding cost, memory, language slices and
chunk-length sensitivity. See [`embedding-model-evaluation.md`](embedding-model-evaluation.md).

An embedding dimension is not an embedding-space identity. Every vector and
derived index binds model/provider revision, tokenizer, pooling,
normalization, document input policy, output dimension and any Matryoshka
projection. Query instructions/prefixes and retrieval scoring (metric, score
normalization, ANN parameters) are separate evaluation identities. Vectors
with the same dimension from different models must never be compared. A
candidate that passes E0 receives a fresh E1 route, THQ and codec fit in its
own space; E5 results cannot be reused for MiniLM, Nomic, or another encoder.

E0 reports qrels-based relevance/candidate Recall@128. E1 reports
`ANNRecall@128` as overlap with the exact FP32 top-128 in the same space. Exact
E0 retrieval must not claim ANN recall against itself.

## Canonical serving modes for compressed retrieval

Codec comparisons use three distinct serving modes:

1. **Full-flat packed 1M:** scan every packed document and produce ordered
   top-10. This isolates codec/kernel cost and cache behaviour.
2. **Prototype-IVF / balanced cascade:** deterministic prototype cells produce
   a declared candidate budget, followed by THQ top-128 and the packed final
   scorer. This measures a cheaper coarse route.
3. **Modern R4 cascade:** the frozen R4 route produces its candidate stream,
   followed by THQ4 top-128 and the packed final scorer. This is the primary
   quality-oriented production path.

The fixed-top128 scorer fixture is component evidence only. It is not a fourth
serving mode. Each mode has its own candidate contract, routing parameters and
latency scope; values from one mode must not fill another mode's table.

For a codec bake-off, freeze the same corpus, query order, numeric-ID tie rule,
payload revisions, one warmup plus five measured repeats, percentile rule and
independent parity audit. Prototype-IVF additionally records the prototype
manifest, training prefix, seed, `nlist`, assignment and candidate budget.

## Required report

Every comparative run publishes a compact JSON report and a manifest binding:

```text
commit, compiler/build flags, hardware, corpus/chunking, query set, qrels,
embedding/model IDs, index parameters, candidate depth, final limit,
filter policy, concurrency, cold/warm procedure, and inclusion of encoding time
```

For external or lifecycle comparisons, the machine-readable manifest must also
carry these fields (not merely prose in a report):

```text
corpus_sha256, query_sha256, qrels_sha256, oracle_sha256, benchmark_config_sha256
row_counts, cpu_model, physical_cores, logical_cores, numa_topology, ram_bytes
storage_medium, process_affinity, thread_affinity, effective_thread_counts
repeat_count, warmup_count, raw_distribution_sha256, startup_open_load_ms
index_parameters, batch_size, concurrency, result_shape
```

`result_shape` states whether the measurement returns IDs only, IDs plus
scores, or full records. A backend's advertised batch size is not assumed to
be one physical write operation; the manifest records the actual operation
path.

Quality fields are selected by workload (`recall@k`, `nDCG@k`, `MRR`, source
coverage, citation preservation). Cost fields include build/ingest time,
query p50/p95/p99, peak RSS/map size, logical and physical index bytes, page
reads, and update/reindex cost when applicable.

For activation and runtime evidence, report declarative and procedural quality
separately. `DeclarativeRecall` measures retrieval of required facts and
preconditions; `ProceduralExecutionSuccess` measures host-side completion and
verification; `ProcedureTransfer`, `CorrectionReuse` and
`ProcedureGeneralization` measure transfer, reuse of corrected failures and
performance outside memorized concrete cases. These metrics never authorize
execution and do not replace retrieval `Recall@K` or qrels-based `nDCG@10`.

## Decision-Relevant Retention Gate

This gate evaluates whether a summary, compacted context, or compressed
representation preserves the information required for a declared decision. It
is a research and evaluation contract. It does not introduce a C++ type,
lifecycle state, authority flag, or storage schema.

Retention quality is separate from retrieval quality:

- retrieval asks whether the required source material can be found;
- retention asks whether a bounded derived artifact still carries the details
  required by fixed decision probes.

A shorter artifact or a high semantic-similarity score is not evidence that the
retained information is sufficient.

### Evaluation mode and source access

The primary gate is **query-independent**: the compressor receives no decision
probes, target questions or probe-derived hints. The fixed probe set is held out
until evaluation. A separate **query-conditioned** mode may provide a declared
task or probe set to the compressor, but its results are reported separately and
must never be combined with query-independent scores or used to claim general
retention.

Every report separates three access arms:

1. **Artifact only** — probes use only the bounded retained artifact.
2. **Artifact plus bounded source hydration** — the artifact may request a
declared, capped number of source spans; report hydrated count, bytes and
provenance coverage.
3. **Full-source oracle** — the authoritative source is available as a ceiling
control and is labelled separately.

The retention gate evaluates whether compaction or replacement is safe under the
declared arm. Legal, security, privacy and explicit user erasure obligations
remain independent lifecycle obligations; they must not depend on passing this
research gate or on preserving source material for evaluation.

### Evaluation input and comparison arms

Each case binds the source revision or a retained replay reference, exact source
hydration/provenance references, the derived output revision or generation, the
compression or summarization policy/model revision, the total byte/token
budget, and a fixed probe set. Metadata, provenance and required replay
material count toward the declared budget when they are part of the artifact.

At minimum, compare these arms on the same source and probe set:

1. **No-op source reference** — the uncompressed source under the declared
   budget or an explicitly recorded full-source control.
2. **Extractive retention** — selected source spans with their source anchors.
3. **Abstractive retention** — a generated summary with derivation and source
   links.
4. **Structured operational handoff** — optional fields for goals, constraints,
   pending steps, verification and unresolved state.

The no-op control establishes the source-side ceiling; it does not make a
larger artifact comparable to a smaller one. Every comparison reports the
actual bytes/tokens, metadata overhead, latency and decoded/read cost.

### Fixed decision probes

The probe set is fixed before the run and must include exact or key-based
checks; an LLM judge may be an additional signal but is not a required oracle.
Probe families include:

- numbers, units, identifiers, versions and thresholds;
- commands, API names, paths and configuration keys;
- goals, constraints, exceptions, negative requirements and safety limits;
- ordered steps, dependencies and pending work;
- conflicts, alternatives, unknowns and unresolved decisions;
- source/inference separation, evidence roots and source drill-down;
- revision and lifecycle state needed to avoid acting on stale material.

For ADELIA/Ephi recovery-oriented profiles, the fixture should additionally
cover the goal, constraints, pending steps, verification result,
unresolved/conflict state and evidence roots. A profile may add domain probes,
but it must not remove the common probes without recording a new evaluation
profile.

### Metrics and loss taxonomy

Reports include exact/key retention and decision-probe accuracy, plus separate
coverage for numeric values, commands/versions, constraints/exceptions,
sequence/dependency order, conflict/unknown state, provenance and source
drill-down. Report source-versus-inference conflation as its own failure class.

At minimum classify losses as:

- **detail loss** — a required value, command, version or constraint is absent;
- **chronology loss** — order, dependency or valid temporal relation is lost;
- **exception loss** — a caveat, exclusion, conflict or unknown is erased;
- **provenance loss** — the retained result cannot reach the required source or
  evidence root;
- **source/inference conflation** — a derived statement is presented as an
  observation or commitment;
- **replay loss** — required baseline, transformation input or model/policy
  material is unavailable.

A result with missing replay inputs is PENDING_SOURCE_REPLAY or unavailable; it
must not be converted into an inferred pass or failure from a substitute
baseline.

### Safety and lifecycle boundaries

A summary, sketch, compressed context or compiled handoff remains a derived
projection. Its creation does not by itself supersede, erase or lower the
addressability of source units, and it does not upgrade source trust, evidence
independence or action authority. Retrieval, usage frequency and summary
promotion are not substitutes for this gate.

The source remains addressable until the ordinary lifecycle and retention policy
explicitly permits retirement after the required provenance, replay and
decision-retention checks. Persistence for audit/replay is distinct from
admission into ordinary factual retrieval.

## Acceptance rules

- Approximate search must report quality against the exact oracle on the same
  candidate contract.
- A latency claim requires repeated runs, warmup policy, fixed environment,
  and a preserved raw result; one local timing is directional only.
- Codec claims separate storage codec bytes, search-code bytes, model/global
  tables, and temporary decode buffers.
- Hybrid claims report each component, fusion, and end-to-end result; a fused
  score must not hide a missing candidate source.
- External comparisons require a `ComparisonParityManifest` and an explicit
  compatibility matrix. Missing inputs produce `PENDING_SOURCE_REPLAY`, never
  an inferred result.
- Quality/latency frontiers are compared only at measured comparable quality.
  `Recall@K` is reported against the exact oracle; `nDCG@K` is reported against
  qrels. They are separate metrics and must not be substituted for one another.
- Report search-kernel, full embedded retrieval, and client/server request
  costs as separate timing layers. Include query encoding, transport,
  serialization and result materialization only in the layers where they occur.

## Ingestion and lifecycle timing

Write measurements separate these observable boundaries:

```text
accepted -> durable_commit -> index_ready -> search_visible
```

`ACK` is not automatically `search_visible`. Bulk build and incremental
insert/update/delete are separate scenarios, and each records durability mode,
batch size, physical write operation, index publication/rebuild time and the
first query that observes the new revision.

Operational replay includes a bounded concurrent scenario with search running
during updates, deletes and rebuild. Its pre-registered acceptance fields are
query/write p95/p99, visibility lag, queue/backlog depth,
revision-generation filtering, and no deleted-record resurrection. A passing
write benchmark without these correctness checks is not a lifecycle result.

## Minimal benchmark matrix

```text
exact scan -> F16/int8/binary/PQ or other codec -> HNSW/ANN -> hybrid -> rerank
```

The matrix is run first on a deterministic synthetic fixture and then on a
versioned local corpus. Held-out or multilingual slices are separate gates,
not silently pooled into the primary score.

Structured-memory and activation profiles use the same comparison discipline:
compare `hybrid`, `hybrid + typed graph expansion`, and optional activation on
the identical corpus, query set, qrels and exact oracle. Report Recall@K,
nDCG@10, MRR, context precision, latency, candidate-expansion cost, and
explanation/provenance coverage separately. Graph or activation output is not
evidence of a quality lift until this downstream benchmark passes; external
reported gains are not acceptance thresholds. The fixtures also require
numeric-first decoding of IDs/revisions/limits, a distinct candidate-to-
canonical-admission boundary, separate evidence/instruction/authority result
types, monotonic narrowing, permutation-stable replay, typed `UNCLEAR` for
insufficient evidence or exhausted shared proof budgets, and provenance for
typed conclusions, omitted evidence and policy/model revisions. These are
planned research contracts, not completed quality claims or thresholds.

## Event-Centric Grounding Gate (EG0)

EG0 evaluates whether event/entity grounding improves retrieval for event and
multi-hop questions without laundering extraction errors into canonical truth.
It is separate from the encoder E0 gate above. No external article, model or
reported benchmark score is a release threshold; the project must run its own
parity-controlled fixture.

Compare at least these arms on the same source revisions, qrels, access policy,
frontier, embedding/model identity and total work budget:

```text
flat lexical/dense chunks
event-grounded units and anchors
event + temporal filtering
event + bounded graph/associative expansion
```

The manifest binds source and extraction revisions, entity/linking policy,
broad `RelationClass` values, concrete `EdgeKind` vocabulary, epistemic/evidence
status policy, temporal policy, query set, exact oracle, candidate/edge
budgets, model revisions and the canonical-hydration procedure. A comparison
whose parity manifest differs remains pending rather than being filled by
prose or a corpus name.

Report separately:

- event extraction coverage and span/anchor fidelity;
- entity-link precision/recall and ambiguous-link rate;
- relation/`EdgeKind` precision, contradiction preservation, epistemic-status handling and unresolved rate;
- Recall@K, nDCG@10, MRR and multi-hop/cross-episode recall;
- candidate/edge work, decoded bytes, latency and context provenance coverage.

Fixtures must include repeated entities in different occurrences, same-name
distinct entities, hard versus soft links, a co-occurrence without support, a
late event correction, conflicting perspectives, stale or inaccessible source
revisions, a disconnected multi-hop answer and an extraction proposal rejected
by admission. Derived extraction output may improve navigation or coverage,
but it is not an independent observation. An unresolved or unknown ancestry
must remain unknown, not count as corroboration.

EG0 is `Docs/tests only` until a reproducible runner and checked-in fixture
exist. A passing event-grounding result does not select a graph backend,
ontology, extraction provider or storage layout.

## Rare-Facet / Multi-Granularity Retrieval Gate

This is a research-only benchmark contract for multi-granularity semantic
retrieval. It evaluates whether document-level aggregation and associative
expansion recover details that ordinary top-chunk retrieval misses. It does not
promote ASMS, MSBSE, SAHI or AR0 to a production API, evidence class,
authority source or storage schema.

### Comparison arms

Run all arms on the same corpus, encoder split, query order, qrels and
source-hydration rules:

1. **BM25 + dense** — the ordinary hybrid baseline.
2. **Max chunk similarity** — the strongest individual chunk control.
3. **Mean/OR aggregation** — simple document-level aggregation controls.
4. **ASMS** — additive semantic membership sketch.
5. **MSBSE** — multi-slot binary semantic document signature.
6. **AR0** — bounded multi-step associative expansion.
7. **ASMS/MSBSE → AR0** — document routing followed by associative drill-down.

The graph-conditioned arms must retain an independent original-query route.
A graph-derived query expansion and a search restricted to a canonical
candidate set are separate arms; they must not be reported as one generic
GraphRAG result. Experimental graph-to-retriever handoff is a harness
contract, not an assumed public PriorRouteCandidates API for every route type.

### Evaluation unit and normalized target

All arms evaluate the same normalized target: a canonical occurrence identity
paired with its logical revision (document, block or segment as applicable).
Projection entries, slots, sketches, graph nodes and duplicate chunks are not
evaluation units. Each returned item is hydrated to the canonical target before
scoring; deduplication occurs by canonical occurrence identity plus revision,
with superseded or stale revisions handled by the declared lifecycle policy.
The report separately records initial seed recall, expanded graph recall and
final hydrated target recall/nDCG. Candidate-set recall is a ceiling for a
conditioned second stage, not final quality.

### Equal-budget and leakage controls

Every run declares the encoder/model revision, chunking, training and held-out
split, source revision, query/qrels manifest and route policy. Entity names,
summaries, graph edges and binary signatures derived from evaluation queries
must not leak into training, fitting or index construction.

For compressed arms, compare equal total bit budgets, including slot/sketch
metadata and required contribution state. Report the full-precision baseline
separately when exact equality is impossible; do not hide extra metadata in the
budget. The resource ledger also includes persistent graph/routing structures,
index build/update/delete/rebuild work, adjacency reads and edges visited,
decoded bytes, memory and bit budgets, and end-to-end latency. Report actual
usage for each arm; equal traversal counts are not required when the declared
resource accounting makes the trade-off explicit. Hold candidate count,
decoded-byte budget, final limit, latency procedure and source-hydration work
constant across comparable arms.

The benchmark includes rare-facet slices where the answer depends on a small
detail, unusual constraint, exception, version, relationship or ordered
sub-step. Aggregate averages must not replace these slices. Candidate-set
recall is an upper bound for any conditioned second-stage recall and must be
reported separately.

### Lifecycle and diagnostic fixtures

Each arm is exercised on add, delete, revision, rebuild and stale-index cases.
A result is valid only if exact source hydration returns the expected canonical
revision after projection or expansion. The report records candidate count,
edges visited, decoded bytes, graph expansion reasons, latency and false
expansion rate.

ASMS delete is correct only when per-chunk contributions or an equivalent
rebuild-safe source are retained. If contributions are unavailable, the
implementation must rebuild or enter an explicit dirty/fail-closed state; it
must not silently subtract an aggregate that cannot be decomposed.

MSBSE slots are retrieval projections. They are not durable identities,
independent evidence, source authority or lifecycle state. Repeated copies,
derived summaries and graph-generated variants do not become independent
evidence merely because they accumulate additional sketch or slot matches.
Retrieval frequency and projection score never upgrade provenance, trust or
action permission.

SAHI remains a deferred comparison lane. It must first demonstrate a
measured advantage over the existing MIH/R4 controls under the same leakage,
budget, hydration and lifecycle rules before it can justify a separate
implementation or index decision.
