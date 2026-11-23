# Reference current-message pagination qualification

- Date: 2026-11-24 (Europe/Moscow). Source baseline: Phase 3A
  `1c337cfbadd113c03390a1b8b17be6d835ee1fb1`, with new qualification-only
  fixtures; store/codec implementation unchanged.
- Question: does paginating Current repeat the fold, and what does that cost?
- Status: executed synthetic directional screen, not a throughput SLA or a
  production maximum-size qualification. Owner: conversation reference store.

## Setup and command

Windows, Intel Xeon E5-2696 v3 2.30 GHz, GNU 15.2 C++17. Library built by Ninja
in Debug, JSON/MDBX/research OFF; benchmark harness compiled with `-O2` and
linked to that Debug library. Each cell below is a separate process, one
enumeration, no warm-up or repeated-run statistics. Process peak working set
is measured with `GetProcessMemoryInfo`, including setup and library overhead;
it is not just result size or incremental fold memory.

From the worktree, using the configured GNU compiler and shared build root:

```powershell
g++ -std=c++17 -O2 -I src tools/agent-memory-bench/conversation_store_pagination_benchmark.cpp E:/_repoz/agent-memory-cpp/.build/conversation-phase3a-gnu/libagent_memory.a -lpsapi -o E:/_repoz/agent-memory-cpp/.build/conversation-phase3a-gnu/pagination.exe
E:/_repoz/agent-memory-cpp/.build/conversation-phase3a-gnu/pagination.exe 2048 16
```

The fixture has N independent 64-byte text roots and no corrections/restores.
For each N, compare a full read, pages of 64 and pages of 16 at one pinned
commit frontier. Every returned root/content is checked against the ordinal
oracle and the frontier must remain unchanged. `visited_records` and
`decoded_bytes` below are actual API counters summed across all pages, not
analytical estimates. No artifact body is read.

## Observed results

| N | Page items | Pages | Decodes | Decoded bytes | Enumeration us | Process peak bytes |
| --- | --- | --- | --- | --- | --- | --- |
| 128 | 128 | 1 | 128 | 20,864 | 2,149 | 5,505,024 |
| 128 | 64 | 2 | 256 | 41,728 | 3,767 | 4,993,024 |
| 128 | 16 | 8 | 1,024 | 166,912 | 13,610 | 4,874,240 |
| 512 | 512 | 1 | 512 | 83,456 | 8,451 | 6,737,920 |
| 512 | 64 | 8 | 4,096 | 667,648 | 55,074 | 5,722,112 |
| 512 | 16 | 32 | 16,384 | 2,670,592 | 253,516 | 5,623,808 |
| 2048 | 2048 | 1 | 2048 | 333,824 | 31,668 | 10,985,472 |
| 2048 | 64 | 32 | 65,536 | 10,682,368 | 872,226 | 8,671,232 |
| 2048 | 16 | 128 | 262,144 | 42,729,472 | 3,225,571 | 8,581,120 |

All nine oracle checks passed. Timing is directional under mixed optimization
and single-run noise; it does not rank production backends.

## Interpretation and boundary

The expected repeated fold is confirmed: for N selected occurrences and P
output pages, this fixture decodes N*P records. The ordered-map fold has
O(P*N log N) bookkeeping, plus selected revision searches and any explicit
restore-content decodes; peak temporary fold storage is O(N), not O(page size).
The bounded input budget applies to each call, not an entire multi-page session.
Page size limits returned output, not total fold work or RSS.

The reference backend remains useful for bounded conformance fixtures, not
advertised as a scalable long-dialogue serving backend. Its default storage
limit is 4096 occurrences; input/output budgets can refuse a fold, so that
limit is not a promise that every stored history is readable under every local
budget. This screen exercises up to 2048 roots, not maximum-size frames/history.
No cache is added: retaining fold state in cursors would add memory/lifetime and
policy invariants without changing the reference contract's purpose.

Future MDBX qualification must compare pinned projections/checkpoints or indexed
folds with this oracle, preserve exact revision/restore/ambiguity semantics and
current authorization, and measure cold/warm reads, invalidation and storage.
An output index must not bypass full freshness validation. Those decisions
belong to Phases 4/5, not this qualification update. More repetitions, edit-heavy
histories and full-size memory screens remain open; no optimization is required
or accepted by these directional timings alone.
