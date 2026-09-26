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
models: planning, batching, prompt-prefix reuse, intermediate-state caching,
and ordering checks by cost and selectivity can matter more than adding another
model. Those ideas are execution optimisations for this project, not a Quail
dependency and not a reason to place inference in the core library. Any cache
or provider-specific intermediate state remains deployment-owned and must obey
the revision/provenance rules below.

## Scope and status

| Surface | Status | Meaning |
|---|---|---|
| Existing storage/retrieval/embedding contracts | **Implemented** | Stable dependency-free building blocks remain the source of candidates. |
| `ISemanticBackend` and request/result shapes in this guide | **Contract only** | Design target; no public header is promised by this document. |
| Planner, batching, cancellation, and semantic result provenance | **Roadmap only** | Requires an implementation PR and focused tests. |
| External HTTP or embedded model adapters | **Not covered** | Adapters may be proposed after the contract and test fixtures exist. |
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
* `SemanticScore`: return a bounded numeric score plus an optional explanation.
* `SemanticRerank`: score a bounded candidate list for a task and merge with
  deterministic scores.
* `SemanticJoin`: compare two bounded candidate sets and emit typed matches,
  including `unknown` when the provider cannot decide.

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
kind, and a final result that can be traced to source revisions.

## Backend contract (design target)

The backend must support both single and batch execution and expose its
capabilities before a plan is accepted:

```cpp
class ISemanticBackend {
public:
    virtual ~ISemanticBackend() = default;
    virtual SemanticBackendCapabilities capabilities() const = 0;
    virtual SemanticResult evaluate(const SemanticRequest&) = 0;
    virtual std::vector<SemanticResult> evaluate_batch(
        const std::vector<SemanticRequest>&) = 0;
};
```

`SemanticRequest` and `SemanticResult` are design names, not implemented API.
The eventual contract must represent:

* operation kind and stable request/task identifier;
* input record id and record revision (or immutable source revision);
* structured output (`true`, `false`, numeric score, or `unknown`);
* model/provider name and revision;
* prompt/instruction hash and generation parameters;
* context/token limits and actual input/output token counts when available;
* latency, retry count, provider error, and cancellation/deadline outcome;
* a deterministic cache-key component and backend capability snapshot.

Unknown and provider failure must not silently become `false`. A planner may
choose a fail-open or fail-closed policy per operation, but that policy is part
of the trace and is tested explicitly.

## Planner and batching rules

The planner is responsible for execution order, not for inventing truth. A
first implementation should:

1. apply exact scope/access/status filters;
2. apply metadata, lexical, and vector filters;
3. cap candidates before any semantic call;
4. group equivalent tasks and batch requests up to provider limits;
5. reuse stable prompt prefixes only when the provider explicitly supports it;
6. enforce concurrency, rate limits, timeout, cancellation, and retry policy;
7. merge semantic results with deterministic scores and emit a trace.

The planner may use measured cost and selectivity estimates to reorder safe
filters. It must retain a deterministic fallback plan and must not execute a
network-backed model call inside a storage transaction.

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
record id + record revision
task id + prompt/instruction hash
provider + model name/revision
generation parameters
result (true / false / score / unknown)
latency + token counts
retry/error metadata
```

An optional semantic-result cache is a derived deployment artifact, never
canonical memory. Its key must include the record revision, provider/model
revision, prompt hash, operation kind, and generation parameters. A cache hit
must be revalidated against current authorization and source revisions before
reuse. This extends the boundary in
[`host-llm-cache-integration.md`](host-llm-cache-integration.md); it does not
turn provider caches into core DBIs.

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
* batch-size, candidate-cap, ordering, and fallback tests;
* provenance and cache-key round-trip tests across record revisions;
* a test proving no semantic call occurs while a storage write transaction is
  held;
* warm/cold and provider-error benchmark reports with candidate counts,
  p50/p95/p99, token counts, and retry totals;
* an explicit policy for sensitive or model-generated inferences, including
  human/curation requirements where applicable.

Until these gates pass, semantic execution remains **Roadmap only** and does
not change M0/M1 ship-it requirements.
