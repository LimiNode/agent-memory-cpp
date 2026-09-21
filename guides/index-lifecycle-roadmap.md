# Index Lifecycle Roadmap

Status: `Proposal`, owned by the storage/index boundary. Derived indexes are
rebuildable views; canonical resources, knowledge units, payloads, and source
provenance remain the source of truth.

## Required lifecycle states

```text
unpublished -> building -> published -> stale -> rebuilding -> retired
```

Each index generation records the corpus/profile generation, configuration
digest, builder version, and publication timestamp. Readers pin one generation
for a request and never mix postings or vectors from different generations.

## Mutation rules

- insert/update publishes new derived entries only after the canonical write is
  durable;
- update creates a new projection generation when identity or model inputs
  change;
- delete/tombstone filtering is applied at candidate materialization and during
  rebuild;
- targeted reindexing is preferred for one resource revision; full rebuild is
  an explicit maintenance operation;
- compaction and rebuild use bounded batches and resumable checkpoints, never
  one unbounded write transaction.

## Required checks before implementation

1. reopen after publish and after an interrupted build;
2. old generation remains readable until the new one is published;
3. stale postings cannot hydrate a deleted or superseded unit;
4. restart resumes or safely discards a checkpoint;
5. rebuild is deterministic from canonical storage and its manifest;
6. map growth, page limits, backpressure, and read-transaction lifetime are
   observable.

HNSW, compressed vectors, graph adjacency, lexical postings, and learned sparse
   views must each document their tombstone, update, serialization, and rebuild
   policy before entering a shipping milestone.
