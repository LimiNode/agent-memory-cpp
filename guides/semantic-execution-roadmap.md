# Semantic Execution and Optional Model Backends

Status: **Roadmap only**. This guide defines boundaries and acceptance gates;
it does not add an LLM runtime, an HTTP client, or a database integration to
the core library.

## Purpose

Some retrieval workloads need a semantic decision after inexpensive local
filters have reduced the candidate set. Examples include semantic filtering,
task-specific scoring, a cross-record join, or a final rerank. The execution
model is deliberately separate from storage and from model inference:

```text
metadata / lexical / vector filters
    -> bounded candidate set
    -> batched semantic checks
    -> score/filter/join merge
    -> provenance-aware retrieval results
```

This is an execution and integration contract, not a new language model. A
host may use a Qwen, DiffusionGemma, llama.cpp, vLLM, Ollama, or another model
provider, but model output remains a probabilistic or heuristic signal. It is
never an authoritative fact unless the normal curation/provenance path records
it as such.

## What the Quail pattern contributes

Quail is useful here as a reference for mass execution of already-trained
models: planning, batching, cost/selectivity ordering, join anchors, and
model/KV intermediate-state reuse can matter more than adding another model.
Those ideas are execution optimisations for this project, not a Quail
dependency and not a reason to place inference in the core library. Any cache
or provider-specific intermediate state remains deployment-owned and must obey
the revision/provenance rules below. This does not imply universal matching
prefix reuse between all rows or providers.

## Scope and status

| Surface | Status | Meaning |
|---|---|---|
| Existing storage/retrieval/embedding contracts | **Implemented** | Stable dependency-free building blocks remain the source of candidates. |
| `ISemanticBackend` and request/result shapes in this guide | **Contract only** | Design target; no public header is promised by this document. |
| Planner, batching, cancellation, and semantic result provenance | **Roadmap only** | Requires an implementation PR and focused tests. |
| OpenAI-compatible HTTP adapter | **Roadmap only** | Optional infrastructure lane; requires the contract and provider fixtures. |
| Embedded llama.cpp backend | **Not covered** | Separate future target; no implementation is currently promised. |
| SQLite storage adapter | **Roadmap only** | See [`sqlite-adapter-roadmap.md`](sqlite-adapter-roadmap.md). |
| Quail-style execution ideas | **Docs/tests only** | Used as an execution-pattern reference, not as a dependency or API. |

## Dependency boundary

The core `agent_memory` target must remain usable with no network, model
runtime, SQLite, or Python dependency. The intended split is:

```text
agent_memory core
  storage / index / retrieval contracts and deterministic implementations

optional infrastructure
  SQLite adapter
  OpenAI-compatible HTTP semantic backend
  llama.cpp server adapter
  optional embedded llama.cpp backend

host application
  credentials, provider policy, model lifecycle, UI, and response caching
```

Adapters depend inward on core contracts. Core code must not include provider
types, execute network calls, or load a model as a side effect of retrieval.

## Semantic operation model

The first contract should cover four operations without copying SQL syntax:

* `SemanticFilter`: retain or reject a candidate with `true`, `false`, or
  `unknown`.
* `SemanticScore`: return a task-local numeric score plus an optional
  explanation. Range, direction, and meaning are part of result metadata; the
  score is not a probability or calibrated cross-task value unless a separate
  calibration contract says so.
* `SemanticRerank`: score a bounded candidate list for a task and merge with
  deterministic scores.
* `SemanticJoin`: compare two bounded candidate sets and emit typed matches,
  including `unknown` when the provider cannot decide. It has independent
  left/right candidate bounds and a `max_pairs` budget.

A C++-oriented query plan can be expressed as:

```cpp
SemanticQuery query;
query.source("knowledge_units")
     .where(metadata_filter)
     .where(semantic_filter)
     .rerank_with(task)
     .limit(20);
```

The exact types and fluent syntax are intentionally deferred. The important
invariants are candidate bounds, deterministic prefilters, explicit operation
kind, and a final result that can be traced to source revisions. Raw semantic
scores must not be arithmetically added to BM25/vector scores by default;
rank-based fusion (for example RRF), or an explicitly versioned and benchmarked
calibration policy, is the first safe fusion lane.

## Backend contract (design target)

The backend must support both single and batch execution and expose its
capabilities before a plan is accepted. The following is a design target, not
an implemented public API:

```cpp
struct SemanticExecutionContext {
    RequestId request_id;
    Deadline deadline;
    ICancellationToken* cancellation = nullptr;  // C++17 adapter contract
    std::uint32_t max_attempts = 1;
};

class ISemanticBackend {
public:
    virtual ~ISemanticBackend() = default;
    virtual SemanticBackendCapabilities capabilities() const = 0;
    virtual SemanticResult evaluate(
        const SemanticRequest&, const SemanticExecutionContext&) = 0;
    virtual std::vector<SemanticResult> evaluate_batch(
        const std::vector<SemanticRequest>&,
        const SemanticExecutionContext&) = 0;
};
```

`SemanticRequest`, `SemanticResult`, `Deadline`, and `ICancellationToken` are
design names, not implemented API. Batch results must preserve a stable
request-id-to-result mapping and allow per-item partial failure; batch
execution is not implicitly all-or-nothing. The eventual contract must
represent:

* operation kind and stable request/task identifier;
* input record id and record revision (or immutable source revision);
* semantic value (`true`, `false`, numeric score, or `unknown`);
* execution status (`ok`, `timeout`, `cancelled`, `provider_error`, or
  `invalid_response`), kept separate from semantic value;
* model/provider name and revision;
* prompt/instruction hash and generation parameters;
* context/token limits and actual input/output token counts when available;
* latency and retry count;
* a deterministic input fingerprint/cache-key component and backend capability
  snapshot.

Capabilities must explicitly report supported operation kinds, maximum batch
and context sizes, structured-output/schema support, cancellation/deadline
support, usage/token reporting, stable model/deployment identity, explicit or
automatic prefix-cache capability, and deterministic-seed support when
available. OpenAI-compatible servers do not necessarily provide identical
capabilities.

`unknown` with status `ok` is a successful semantic answer. A timeout,
cancellation, provider error, or invalid response has no semantic answer and
must not silently become `false`. A planner may choose a fail-open or
fail-closed admission policy per operation, but that policy is traced as a
separate effective decision and never overwrites the persisted raw value/status.

## Planner and batching rules

The planner is responsible for execution order, not for inventing truth. It
must distinguish a logical plan (the requested operations and semantics) from
the physical execution plan (chosen order, batching, anchors, and fallbacks).
A first implementation should:

1. apply exact authorization, scope, and lifecycle filters;
2. apply metadata, lexical, and vector filters;
3. cap candidates before any semantic call;
4. generate only authorized join pairs and enforce independent left/right and
   `max_pairs` budgets with deterministic truncation;
5. group equivalent tasks and batch requests up to provider limits;
6. reuse model/KV prefixes only when the provider explicitly supports that
   capability;
7. enforce concurrency, rate limits, timeout, cancellation, and retry policy;
8. merge semantic results with rank-based or explicitly calibrated fusion and
   emit a trace.

The planner may use measured cost and selectivity estimates to reorder an
operation only when that operation is pure, side-effect-free, reorder-safe, and
has an order-independent `unknown`/failure policy. Exact authorization, scope,
and lifecycle filters always precede external model calls. The planner must
retain a deterministic fallback plan and must not execute a network-backed
model call inside a storage transaction.

`SemanticJoin` additionally records `max_left_candidates`,
`max_right_candidates`, `max_pairs`, pair-generation policy, optional anchor,
and early-stop policy. Its trace records which pairs were truncated or skipped
and why.

### SQLite/semantic boundary

Never expose a function such as `SELECT llm_filter(text) FROM chunks` as the
canonical integration. Storage should return a bounded candidate table; the
semantic executor performs batched calls outside the database transaction; the
result store/cache records provenance; the planner then materializes the final
retrieval result. This avoids blocked transactions, unbatchable calls, mixed
SQL/network failures, and ambiguous retry semantics.

## Provenance and cache rules

Every persisted semantic decision must bind at least:

```text
operation kind + semantic input fingerprint
task id + exact rendered instruction/system/template hash
output schema/parser revision
provider + model name/revision
host-declared immutable deployment fingerprint, when provider identity is not stable
generation parameters
raw semantic value (true / false / score / unknown)
execution status (ok / timeout / cancelled / provider_error / invalid_response)
latency + token counts
retry/error metadata
```

`SemanticInputFingerprint` is operation-specific:

* Filter/Score: record id and revision.
* Join: both left/right ids and revisions, plus orientation/canonical-pair
  semantics.
* Rerank: candidate-set identity and revisions, candidate ordering when it is
  rendered into the request, initial deterministic scores/fusion-policy
  revision when they are rendered, and the candidate-set policy revision.

An optional semantic-result cache is a derived deployment artifact, never
canonical memory. A backend without a stable model/deployment identity must
declare durable cache reuse unsupported or unsafe. Every cache hit must be
revalidated against current authorization, security scope, and source
revisions. Cross-scope reuse is forbidden by default unless an explicit
security partition and revalidation contract permits it. This extends the
boundary in
[`host-llm-cache-integration.md`](host-llm-cache-integration.md); it does not
turn provider caches into core DBIs.

## Scope and authorization

Exact scope, ACL, lifecycle, and other deny-by-default filters run before
semantic candidate or pair generation. A semantic cache cannot bypass current
authorization. A join must never form a cross-scope or otherwise unauthorized
pair merely because both records are present in a local candidate set; the
admission and security partition are part of the trace and cache identity.

## Optional backend lanes

### OpenAI-compatible HTTP (default deployment lane)

Implement an adapter against a narrow HTTP abstraction compatible with common
servers such as vLLM, llama.cpp server, Ollama, and LM Studio. The host owns
base URL, credentials, model selection, TLS, rate limits, and request policy.
The adapter must support structured results, batching where the provider does,
timeouts, cancellation, bounded retries, and complete provenance. No provider
SDK belongs in the core target.

### llama.cpp server adapter

Treat a llama.cpp HTTP server as one implementation of the HTTP lane. Do not
couple the semantic contract to llama.cpp tensor or sampler types.

### Embedded llama.cpp (optional)

An embedded backend is a separate target controlled by
`AGENT_MEMORY_ENABLE_LLAMA_CPP`. It is off by default, never a required
dependency of `agent_memory`, and has a separate build/test path. Its design
must document model loading lifetime, GPU/backend/platform constraints,
threading, memory footprint, license implications, and shutdown behavior.

## Acceptance gates before implementation claims

An implementation PR must provide:

* deterministic fake backend fixtures for `true`, `false`, score, `unknown`,
  timeout, retry, and cancellation;
* batch-size, candidate-cap, stable request/result mapping, partial failure,
  ordering, and fallback tests;
* provenance and operation-specific cache-key round-trip tests across record,
  join-side, candidate-set, ACL, parser, and deployment revisions;
* tests proving timeout/cancel/provider-error are not persisted as `false` and
  that fail-open/fail-closed is an independent traced decision;
* tests proving unsafe operator reorder is rejected and join pair budgets are
  deterministic;
* a test proving no semantic call occurs while a storage write transaction is
  held;
* warm/cold and provider-error benchmark reports with candidate counts,
  p50/p95/p99, token counts, and retry totals;
* an explicit policy for sensitive or model-generated inferences, including
  human/curation requirements where applicable.

Until these gates pass, semantic execution remains **Roadmap only** and does
not change M0/M1 ship-it requirements.
