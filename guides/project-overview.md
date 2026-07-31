# Project Overview

## Intent

`agent-memory-cpp` is an embedded C++17 toolkit for building memory and
retrieval systems for AI agents. It should provide deterministic, inspectable
local components rather than a hosted service or a generic agent framework.

## Why Local Retrieval Engineering Matters

The project is not trying to save a few gigabytes from a small RAG corpus for
its own sake. A plain float-vector index is often the right answer when the
working set fits in RAM and hosted/vector-service infrastructure is acceptable.

This library exists for the different case: an evidence-bearing local workspace
or agent memory must remain portable, inspectable and usable in one process
with MDBX, bounded RAM, ordinary SSD storage, no required vector-database
service, and resource-level updates. At that point the physical shape of
derived indexes affects cold-start time, page faults, backup/restore cost,
update cost and p99 retrieval latency as much as their nominal byte size.

Compression, quantization, binary routing and segment layouts are therefore
optional capacity tools. They must improve a measured resource or operational
constraint while preserving canonical text/artifacts, provenance and retrieval
quality; they are never a default substitute for a simpler exact or HNSW
baseline that already meets the deployment budget.

## Current Status

The repository is in the project-skeleton stage. The current code provides:

- static library target `agent_memory`;
- public alias `agent_memory::agent_memory`;
- public aggregate header `agent_memory.hpp`;
- `core::LibraryInfo` smoke API;
- dependency-free document, chunk, metadata, and source-kind primitives;
- dependency-free resource revision and manifest value types;
- dependency-free document storage contract;
- dependency-free resource manifest storage contract;
- dependency-free embedding value types, model metadata, and embedder contract;
- dependency-free vector index value types and index contract;
- exact in-memory vector index baseline;
- dependency-free retrieval value types and retriever contract;
- dependency-free retrieval evaluation dataset/run value types and metric
  helpers;
- dependency-free resource indexer orchestration over storage, embedding, and
  vector index contracts;
- dependency-free tokenizer value types and tokenizer contract;
- std-only tokenizer baseline for UTF-8 text, markdown, and code-like text;
- dependency-free lexical search value types for postings, stats, queries, and
  results;
- dependency-free token dictionary contract for token-id allocation and
  document-frequency stats;
- dependency-free lexical index contract for tokenized chunk search;
- exact in-memory BM25 lexical index baseline;
- planned lexical/BM25 retrieval architecture;
- opt-in MDBX dependency wiring for future storage backends;
- optional MDBX-backed document storage adapter;
- optional MDBX-backed resource manifest storage adapter;
- CMake options for tests, examples, warnings, and MDBX wiring;
- smoke/domain/storage/embedding/index tests and one basic example.

## Core Scope

The library scope is limited to:

- memory records and memory strategies;
- ingestion and chunking;
- resource ownership and targeted reindexing;
- persistent storage;
- embedding interfaces and adapters;
- exact and approximate indexes;
- retrieval and ranking;
- knowledge activation/planning contracts for domain maps and playbooks;
- durable cognitive-runtime integration records such as origin, perspective,
  causal context, task/decision/procedure payloads, and reconciliation
  metadata;
- context assembly for downstream agents or LLM calls.

## Non-Goals

Do not add these to the core library:

- autonomous-agent orchestration;
- live cognition, scheduling, focus arbitration, authority enforcement, action
  execution, topology mutation, distributed transport, or consensus;
- browser automation;
- participant simulation;
- TTS/ASR pipelines;
- LLM inference engines;
- hosted vector database services;
- generic prompt-template collections.

Adapters for external systems may be added later, but they must stay outside
core domain logic.

## Planned Source Areas

```text
src/agent_memory/
    core/
    domain/
    storage/
    embedding/
    index/
    retrieval/
    eval/
    compression/
    math/
    ingestion/
    memory/
    context/
    infrastructure/
```

The layout is expected to grow incrementally. Do not create empty directories or
placeholder layers unless a PR needs them.

## Optimization Backlog

Detailed follow-up tasks for compression, optional Eigen/SIMD scoring, vector
encoding, binary signatures, MDBX bucket indexes, and capacity-aware
recall/latency/operability benchmarks are tracked in
`guides/optimization-roadmap.md`.

## Reindexing Backlog

Resource manifests, source revision tracking, targeted reindexing, tombstones,
and compaction for mutable memory are tracked in
`guides/resource-reindexing.md`.

## Artifact And Provenance Backlog

Stable source revisions, immutable artifact bytes, versioned extraction
representations, typed evidence locators, multimodal segments, retention and
backup boundaries are tracked in
[`guides/artifact-provenance-roadmap.md`](artifact-provenance-roadmap.md).
The default path is the library's own catalog, blob storage and indexes; any
external vector database remains an optional derived-index adapter.

## Lexical Search Backlog

BM25, token dictionaries, postings, Unicode tokenization, raw resource stores,
phrase/proximity, fuzzy search, BM25F, graph retrieval, hybrid retrieval, and
planner-guided retrieval are tracked in `guides/lexical-search-roadmap.md`.

## Affective Memory Backlog

Optional affective-agent memory extensions are tracked in
`guides/affective-memory-roadmap.md`. That roadmap defines how this library can
persist affectively meaningful events, appraisal snapshots, goal impacts,
action outcomes, relationship evidence, and sensitive-inference policies
without becoming an emotion engine or autonomous-agent runtime. It also tracks
optional encrypted local persistence and urgency-aware context planning for
live agents that need both privacy and low-latency responses.
