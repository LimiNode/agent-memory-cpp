# CanonicalContent prompt-profile experiment

Status: `draft` / research-only

Source candidate: `main@9eea319f54e1538c550a3b26fd435d22e94f3c57`

This experiment qualifies the existing `CanonicalContent` substrate as a
revision-pinned source for structured prompt documents and agent lessons. It
does not introduce a prompt storage engine, activation authority, runtime
orchestration, MDBX schema, or a production retrieval path. The Raw Markdown
Technical Preview and `HybridRetrievalEngine` are unchanged.

## Executable contract

`examples/canonical_prompt_subtree.cpp` is the small C++17 demonstration. It
creates Role, Review obligations, Tools, and Output blocks, performs the
replace/insert/move sequence, replays the historical Markdown, and exercises
stale, cycle, duplicate-ID, and non-leaf deletion rejection.

`tests/experimental/canonical_prompt_profile_test.cpp` is a private consumer
of the existing public API. It exercises the same contract against
`InMemoryCanonicalContentStore` and, when MDBX is enabled, C1
`MdbxCanonicalContentStore`:

- Role, Obligations, Tools, and Output use stable `ContentBlockId` values;
- exact revision and block reads remain available after edits;
- `ReplaceBlockText`, `InsertBlock`, and `MoveBlock` publish through
  `CanonicalEditRequest` and `ContentChangeSet`;
- deterministic block order and stale optimistic edits are checked;
- structural cycles, repeated IDs, and deletion of a non-leaf are rejected;
- a private prompt manifest uses `DocumentId + exact revision + block ID`;
- required blocks, canonical order, invalid revisions, and non-instructional
  lessons fail closed;
- the active prompt digest is computed from the exact UTF-8 bytes with the
  shared `agent_memory::sha256` implementation.

The consumer intentionally calls `read_revision()` for active assembly. It
does not call `read_current()`, so editing the current document cannot silently
change a pinned baseline. A candidate manifest is data for comparison; it is
not an activation operation.

## Cost experiment

`tools/agent-memory-bench/canonical-content-prompt-benchmark.cpp` emits a raw
TSV observation for 32, 128, 512, and 2048 blocks. The columns are:

`backend`, `blocks`, `read_block_ns`, `read_revision_ns`, `commit_ns`,
`materialize_ns`, `peak_alloc_bytes`, `mdbx_bytes`, and `mdbx_growth_bytes`.

The timing values are a small local comparison, not an SLA. `read_block` is
logically point-addressed but currently materializes the whole immutable
revision in both backends. C1 stores a new full logical body for a block edit;
the MDBX size and growth columns make that write amplification visible after
three edits. The allocation counter is an executable probe of temporary live
bytes around each measured operation, not a portable allocator or RSS claim.

Example invocation after a benchmark build:

```text
agent-memory-canonical-content-prompt-benchmark canonical-prompt.tsv
```

The latency columns are per-call means over five reads or three writes/materializations
from one local run; they are not medians or SLAs. The raw TSV must be retained
with the build provenance before any aggregate is published. No threshold or
performance claim is attached to this experiment.

### Linux MDBX C1 observation

The raw receipt
`guides/experiments/2026-11-17-canonical-prompt-mdbx.raw.tsv` was produced by
the Release benchmark in an Ubuntu 24.04 container with GCC, the pinned
`libmdbx` and `mdbx-containers` submodules, and `AGENT_MEMORY_ENABLE_MDBX=ON`.
Both prompt contract tests and both backends completed successfully. The
observed per-call means were:

| Backend | Blocks | `read_block_ns` | `read_revision_ns` | `commit_ns` | `materialize_ns` | `peak_alloc_bytes` | `mdbx_bytes` |
|---|---:|---:|---:|---:|---:|---:|---:|
| in-memory | 32 | 90,788 | 17,620 | 377,118 | 14,633 | 25,581 | 0 |
| mdbx-c1 | 32 | 49,153 | 36,899 | 4,375,256 | 78,229 | 30,756 | 65,536 |
| in-memory | 128 | 10,167 | 9,006 | 4,362,594 | 23,370 | 100,601 | 0 |
| mdbx-c1 | 128 | 161,876 | 141,018 | 9,527,807 | 323,330 | 117,316 | 65,536 |
| in-memory | 512 | 40,584 | 36,083 | 236,869,802 | 111,864 | 402,036 | 0 |
| mdbx-c1 | 512 | 669,113 | 656,042 | 242,418,364 | 1,422,408 | 468,194 | 16,777,216 |
| in-memory | 2048 | 172,615 | 146,978 | 13,884,585,064 | 470,729 | 1,613,031 | 0 |
| mdbx-c1 | 2048 | 2,571,575 | 2,632,917 | 13,920,189,767 | 11,567,767 | 1,932,736 | 16,777,216 |

These measurements demonstrate the current implementation cost: a logical
point read still materializes the immutable revision, and C1 block edits write
the full logical body. They are a reproducible diagnostic, not an SLA or a
production capacity claim. Windows MDBX was not substituted into this receipt;
the pinned MSVC build remains blocked by the external `mdbx-containers`
`KeyValueTable::retrieve_all<std::map>` template-deduction ambiguity already
recorded in the repository preflight notes.

## Prompt-profile boundary

The private manifest is deliberately smaller than a public API. It records
exact revisions, required/editable block policy, canonical order, and a digest
of the assembled bytes. It does not decide whether a candidate becomes active.
Missing required blocks, wrong revisions, forbidden structural edits, and
configuration mismatches are rejected before assembly.

Extracted lessons remain evidence. They may later be represented as
`KnowledgeUnitKind::Decision` or `KnowledgeUnitKind::Playbook` with normal
provenance and lifecycle metadata, but this experiment does not grant them
control-instruction status or make a universal memory owner.

## Retrieval and Harness² follow-up

This slice is read-only with respect to retrieval. The minimal future binding
is deliberately a join, not a new index:

| Prompt identity | Retrieval identity | Binding rule |
|---|---|---|
| `ContentBlockId` | source-backed `KnowledgeUnit` | one block may point to zero or more source units, but the block ID remains the canonical owner of prompt order |
| `DocumentId + exact revision` | source document revision | a hit is admissible only when its source revision is explicitly compatible with the pinned prompt manifest |
| block text and `ContentChangeSet` | `SearchProjection` | changed text or structure invalidates the affected projection; a parent-text change may conservatively invalidate descendant contextual projections |
| projection revision/fingerprint | projection metadata | retrieval may use a projection only when its revision/fingerprint matches the exact source binding |

The adapter must read the exact manifest revision and may use retrieval only to
attach explanatory/source material. It must not use RAG to discover or decide
which required instructions become active. A parent block edit may invalidate
contextual embeddings of descendants; that is an evaluation question, not a
reason to search for required instructions with RAG.

The compatibility note for Harness² should preserve: baseline harness
identity, candidate prompt revision, task/dataset identity, model/profile,
evaluator evidence, improvement or regression, cost, applicability,
counterexamples, and accepted/rejected change. This note does not claim that
such a harness is implemented here.

## Disposition

Keep this work in a Draft PR. Do not merge, tag, release, activate production
retrieval, or alter canonical persistence schema as part of the experiment.
