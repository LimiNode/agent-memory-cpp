# Graphiti Context Graph Reference

Date: 2026-07-27

Context: roadmap expansion after Graphiti/Zep review in
`C:\Users\User\.codex\attachments\dfe7f516-e425-40a3-9b6b-6348958a4f6c\pasted-text.txt`.

## Question

Which Graphiti/Zep ideas should become `agent-memory-cpp` roadmap contracts,
and which belong only to managed Zep infrastructure or ADELIA/runtime?

## Sources Checked

- getzep/graphiti repository: https://github.com/getzep/graphiti
- Graphiti README context graph description: temporal facts, episodes,
  provenance, hybrid retrieval and the Zep vs Graphiti boundary.
- Zep scaling note: https://blog.getzep.com/scaling-agent-memory-zep-30x/
- Graphiti 20K/MCP 1.0 note:
  https://blog.getzep.com/graphiti-hits-20k-stars-mcp-server-1-0/
- Graphiti releases for the search-filter hardening and MCP version bump:
  https://github.com/getzep/graphiti/releases

## Interpretation

Graphiti is a strong reference for temporal context graph semantics:

- episodes are first-class raw source records;
- facts and relations are derived records with validity windows;
- old facts are invalidated or superseded rather than destructively rewritten;
- retrieval combines semantic, keyword and graph traversal;
- deterministic entity/edge dedupe should run before LLM fallback;
- LLM-facing query APIs require typed filters and schema allowlists.

Zep's managed platform adds infrastructure that is not automatically part of
Graphiti OSS: proprietary context graph engine, managed governance, dashboards,
SLA, low-latency production retrieval and physically separated services. For
`agent-memory-cpp`, the useful lesson is logical separation of contracts and
indexes first; physical service separation should wait for benchmarks.

## Roadmap Result

Extended `guides/memory-lifecycle-governance-roadmap.md`:

- AM-13 now includes optional recorded/invalidated sequence fields for replay
  and historical perspective queries.
- AM-14 now includes `DerivationComponent` so derived records retain episode
  and runtime-source provenance.
- AM-19 adds deterministic-first entity resolution.
- AM-20 adds typed query/MCP safety rules.
- AM-21 adds logical index separation and an LLM-free baseline read path.

## Limitations

No Graphiti benchmark was run. This note captures architectural contracts and
security implications only. Exact thresholds for entropy, MinHash/LSH, Jaccard
or dense scoring must be re-derived and benchmarked in our corpus before they
become production defaults.

## Follow-up Checks

- Add entity-resolution fixtures with exact, heuristic, ambiguous and rejected
  cases.
- Add MCP/tool-call tests proving raw strings cannot become schema labels,
  table names or index identifiers.
- Add Graphiti-style temporal graph eval once AM-13 storage and retrieval
  queries exist.
