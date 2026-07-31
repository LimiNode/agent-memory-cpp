# Operator Guide — Choosing a Memory Model on `agent-memory-cpp`

> Operator-level decision guide. This is **not** a roadmap: the canonical
> architectural decisions live in [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md)
> (in-house ADR-001 through ADR-015, `MemoryProfileSpec`, `MemoryStack`,
> capability matrix, validation, M0/M1/M2 maturity), the canonical physical
> MDBX manifest and DBI budget live in
> [`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md), and the external landscape
> comparison lives in [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md)
> (13+ external architectures, citation framework, design-pattern mining).
>
> This guide exists to translate those roadmaps into actionable "I have
> requirement X, which architecture fits?" decisions for practitioners who
> already decided to use `agent-memory-cpp` as their substrate.

## §1.5. Scope note: real vs planned

These guides are practitioner-facing. Recommendations distinguish real code from documented-but-not-yet-implemented.

**Real, in `src/agent_memory/`:**

- `IRetriever` — single-retriever interface
- `IRetrievalEngine` — retrieval-pipeline facade interface
- `HybridRetrievalEngine` — lexical pipeline + extension hooks (current implementation)
- existing lexical components:
  - ExactLexicalRetriever — deterministic BM25 lexical baseline
    (src/agent_memory/retrieval/ExactLexicalRetriever.hpp,
    introduced by commit `99d6052c`)
  - ExactLexicalIndex (GitHub PR #23)

**Documented but NOT yet in `src/agent_memory/`:**

- All seven MemoryStack factories (`BasicRagStack`, `QAKnowledgeBaseStack`, `AgentLongTermMemoryStack`, `SpeakerAwareChatStack`, `CompiledWikiStack`, `TemporalFactStoreStack`, `FullResearchMemoryStack`)
- `MemoryProfileSpec` (declarative spec — exists in roadmap, not in code)
- `DenseIndexMode::*` including `Hnsw`, `BinaryCandidateFilter`, etc.
- `FactPayload`, `QAPayload`, `CompiledArticlePayload`, `EventPayload`
- `CompactionWorker` + compaction jobs (`SummaryPromotionJob`, `DecayJob`, etc.)
- Graph capabilities (graph index, graph expansion, graph-scope spread)
- Stable runtime MemoryStacks that compose these

A "Target profile: X" recommendation means X is the planned shape; the **current implementation path** is to assemble available retrievers manually using the real interfaces above, or wait for the MemoryStack implementation milestone.

## §1. Purpose

You read [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) and saw
that the external landscape contains 13+ memory architectures (Karpathy 3-layer,
A-MEM, lifemodel dual-layer, Mem0, Letta, Zep Graphiti, Memoria, СВИНОПАС, NOUZ,
Блок фактов, J-Space, RLM, multi-dimensional context graphs). You read
[`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) and saw 7 default
`MemoryStack`s (`BasicRagStack`, `QAKnowledgeBaseStack`, `AgentLongTermMemoryStack`,
`SpeakerAwareChatStack`, `CompiledWikiStack`, `TemporalFactStoreStack`,
`FullResearchMemoryStack`). You now need to decide which stack (or which
external pattern, if your use case demands something outside the seven) to
plan around.

This guide answers that question.

It is organised around three asks:

1. **Capability checklist** — score required capabilities and combine a base
   profile + overlays; decisions are not mutually exclusive.
2. **Architecture-by-architecture guidance** — for each external memory
   architecture, when to reproduce its idea on top of `agent-memory-cpp`, when
   to leave it as an external alternative, and how each one maps to the 7
   default stacks.
3. **Hybrid and migration patterns** — which architectures compose well, and
   how to grow from one stack to another as workload changes.

Non-goals:

- Not a roadmap. ADR-001 through ADR-015 are settled.
- Not a deep dive on any single architecture. Each external architecture has
  its own entry in [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md);
  read that for design and storage details.
- Not a tutorial on `MemoryStack::open()` API. See [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md)
  §7 for the runtime contract and `examples/` for compiled examples (planned).

See [`compression-is-intelligence-roadmap.md`](compression-is-intelligence-roadmap.md) for the conceptual framing of what good memory compression preserves ("user was setting up TTS" vs operational details like "Qwen3-TTS with compile ≈ 9.5 GB VRAM").

## §2. Source attribution policy

This guide synthesises material from several sources. Citations follow the
two-tier pattern:

- `[Source: <public citation>]` — primary citation of a published work, project
  documentation, repository revision, or article. When citing an external
  source, the public link is authoritative.
- `[Source: internal note — no public source available. Path: <path>]` —
  statement comes only from internal research notes, with no equivalent
  public source at the time of writing. Requires additional verification.

Internal notes are discovery aids, not authoritative citations. Public roadmap
statements cite a public source first; internal notes serve only as a marker
of private provenance.

This convention is shared with
[`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) and
[`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md). When the same
fact appears in two documents, both should cite the same public source if one
exists.

### §2.1. PR number disambiguation

This codebase uses the phrase "PR #N" in two unrelated senses:

- **GitHub PR #N** — a real pull request at <https://github.com/.../agent-memory-cpp/pulls>.
  Examples below that refer to specific GitHub PRs use the full phrase
  "GitHub PR #N".
- **Roadmap-label "PR #N"** — a label on an in-house roadmap section (e.g.
  "PR #26", "PR #27", "PR #28" inside the benchmark-tooling roadmap, or
  "PR #29 (BinarySignature)", "PR #30" inside `memory-stacks-roadmap.md`).
  These are roadmap-internal identifiers and do **not** correspond to GitHub
  PR numbers 26/27/28/29/30.

When a sentence in this guide could be read either way, the disambiguation is
made explicit. Default convention: a bare "PR #N" without qualifier refers to
the roadmap label, matching the surrounding guides. GitHub PRs are always
written out fully.

## §3. Capability checklist

Decisions are NOT mutually exclusive. For each workload, score required capabilities and combine a base profile + overlays.

Required capabilities (mark yes/no):

- [ ] **Scope isolation** — multiple users / projects / agents need separate namespaces
- [ ] **Temporal validity** — facts have TTL or decay; stale records should be filtered
- [ ] **Speaker attribution** — multi-author records need per-speaker metadata
- [ ] **Dense semantic search** — required for fuzzy/conceptual matching
- [ ] **Compiled summaries** — required for repeated queries against same topic
- [ ] **Graph expansion** — required for multi-hop reasoning over relational knowledge
- [ ] **Decay** — non-append memory where stale facts should auto-expire
- [ ] **Auditability** — every read must have provenance

Capability counts alone do not determine architecture. Three axes govern the choice:

**Mandatory constraints** (hard requirements — exclude an architecture if violated):
- Multi-tenant authorization → flat facts is unsafe; need scope isolation
- Bi-temporal validity → require temporal-aware architecture
- Legal auditability → require append-only audit log + immutable records

**Optional capabilities** (additive — combine if present):
- Decay, dense semantic search, graph expansion, compiled summaries

**Scale / performance characteristics:**
- Traffic level (low / medium / high)
- Corpus size (KB / MB / GB / TB)
- Latency budget (sub-100ms / sub-second / seconds / minutes)
- Cost ceiling (per-query dollars)

A correct architecture selection is a conjunction of one mandatory-constraint profile, zero or more optional capabilities, and a scale profile. The "recommended combinations" below address the common-case conjunctions but each real deployment must verify against the full three-axis matrix.

Recommended combinations (read with the three-axis framing above):

- ≤4 capabilities, low traffic → flat facts (Блок фактов pattern, see `memory-architectures-roadmap.md`)
- 1-2 capabilities, append-only → file wiki (Karpathy 3-layer)
- Dense + autonomous note linking / atomic-note evolution → A-MEM-inspired pattern (atomic notes, LLM-driven linking, schema-less)
- Dense + periodic compiled summaries → CompiledWiki / Self-Evolving Wiki pattern (file-based wiki with periodic LLM-synthesized summaries)
- Dense + Graph expansion → dual-layer (LanceDB + graph + spreading activation)
- Scope isolation + Temporal validity + Auditability → NOUZ / Session-scoped append-only
- Streaming + Anti-loop + Russian morphology → СВИНОПАС
- SaaS / multi-tenant → Mem0 / Letta / Zep with hosted backend
- Multi-scope with overlap of capabilities → compose base + overlays (planned MemoryStack system)

Notes: each base pattern composes with overlays. Caching/dedup is mostly orthogonal to architecture choice; treat as overlay.

### §3.1. Context-window vs retrieval

A model with a 200K context window does NOT guarantee full-context prompting beats retrieval. Trade-offs:

- Input latency and cost scale with context size.
- Long contexts dilute relevant signal (lost-in-the-middle degrades 15-47% at 60-75% fill).
- Privacy: full-context includes sections this request must not see.
- Repeated queries re-pay the full context cost each time.
- Citations, staleness filtering, version control still need retrieval semantics.

**Benchmark full-context against retrieval for the target model on the target corpus**. Use full-context only when quality, latency, cost, and access-control requirements are satisfied simultaneously.

## §4. The 7-tier memory hierarchy (where each architecture fits)

The hierarchy from
`ai-agent-playbook/concepts/ai-agents/Уровни памяти AI-агента — от контекста до fine-tuning.md`
defines seven levels plus one experimental (6.5 = RLM). Every external memory
architecture sits at exactly one or two of these levels; the in-house
`MemoryStack` profile maps directly to the level that fits the workload.

| Tier | Mechanism | When to apply | Stack mapping |
|---|---|---|---|
| 0 | No memory, plain chat | One-shot task | (none) |
| 1 | Saved chat transcript | One task, persistent context | (none — use filesystem) |
| 2 | `AGENTS.md` / `CLAUDE.md` | Global rules for all chats | (none — file in repo) |
| 3 | Skills / plugins | Many rules, want to compose | (none — call external skill) |
| 4 | Provider-managed auto-memory (Claude Code memory, Mem0 auto-extract) | Personal tasks, minimal control | Блок фактов pattern over `BasicRagStack` |
| 5 | RAG / embeddings | Large projects, custom retrieval | `BasicRagStack`, `QAKnowledgeBaseStack` |
| 6 | Fine-tuning | Domain expertise worth ML cost | (out of substrate scope) |
| 6.5 | RLM (Recursive Language Models) | Multi-hop, code-capable LM | External REPL + agent-memory-cpp as substrate |

`AgentLongTermMemoryStack`, `SpeakerAwareChatStack`, `CompiledWikiStack`,
`TemporalFactStoreStack`, and `FullResearchMemoryStack` all sit at level 5 but
specialise on top of it: speaker/temporal/wiki/graph overlay layers are added
on top of the RAG baseline.

External architectures and their tier:

- Karpathy 3-layer — straddles 4 (conversations + facts) and 5 (wiki as
  compiled articles). On agent-memory-cpp: `CompiledWikiStack` covers the wiki
  layer; Mem0 / Letta / Zep comparisons cover the facts layer.
- A-MEM — level 5 with autonomous link generation (LLM-curated evolution).
  On agent-memory-cpp: requires `IQueryTransformer` (planned) for the
  LLM-driven linking step; not first-class.
- lifemodel — level 5 with spreading activation overlay. On agent-memory-cpp:
  use `AgentLongTermMemoryStack` with graph expansion; spreading activation
  depth still requires validation (open question in
  [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) §8).
- NOUZ — level 5 with manual hierarchy; embeddings as observation only. On
  agent-memory-cpp: use `BasicRagStack` for lexical and skip dense; rely on
  human-curated YAML frontmatter as schema.
- Mem0 / Letta / Zep — level 5 with their own retrieval models. On
  agent-memory-cpp: Mem0 ≈ `AgentLongTermMemoryStack` with 4-scope; Zep ≈
  `TemporalFactStoreStack`; Letta is an alternative execution model, not a
  retrieval layer.
- СВИНОПАС — level 5 with anti-loop, real-time ASR ingest. On
  agent-memory-cpp: `SpeakerAwareChatStack` + DecayPolicy + cooldown; the
  fine-tuned extractor is external.
- Memoria — level 5 with weighted-KG user modelling. On agent-memory-cpp:
  `AgentLongTermMemoryStack` with graph expansion and per-edge decay weights.
- Блок фактов — level 4 (no retrieval). On agent-memory-cpp: FactPayload +
  injection-only.
- DLP-bridge ToM — level 4.5 (sidecar). Out of substrate scope; can be wired
  as an external IPC adapter.
- J-Space — in-model, not on the substrate.
- RLM — execution model. Pair as external REPL.
- Исполняемый граф контекста — level 5 with live pipeline. Not directly
  in-scope; closest is `AgentLongTermMemoryStack` with high-frequency
  ingestion from external sources.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Уровни памяти AI-агента — от контекста до fine-tuning.md]

## §5. Architecture-by-architecture guidance

For each external architecture: when to use on top of `agent-memory-cpp`,
when NOT to use, and which of the 7 default stacks fits. Citations to the
external architecture's primary source come first; internal notes are
secondary markers.

### §5.1. Karpathy 3-layer (Conversations + Facts + Wiki)

**What it is.** Three layers — SQLite for chat history, Mem0-style facts
with vector search, and a markdown wiki rewritten by a maintainer agent on a
6-hour cron. All three inject into the prompt simultaneously.

[Source: Karpathy talks on LLM-as-OS (2025)]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Трёхслойная память для AI-агентов - метод Карпати.md]

**On `agent-memory-cpp` mapping.** Layer 1 (conversations) → file-system
storage, not in-scope. Layer 2 (facts) → `AgentLongTermMemoryStack` with
`enable_fact_payload = true`, optional dense vectors. Layer 3 (wiki) →
`CompiledWikiStack` with `enable_compiled_article = true` and a
`SummaryPromotionJob`-driven maintainer loop.

**Use it when:** you want cross-agent memory with a maintainer process and
can accept the cost of periodic LLM-driven rewriting (every 6 hours is a
common cadence in the playbook).

**Don't use it when:** your corpus is too small to justify a maintainer cron,
or your facts change too fast for the 6-hour cycle to keep up. In those cases,
drop straight to `BasicRagStack` with `enable_qa_payload = true`.

**Integration hints:**

- Wiki articles correspond to `CompiledArticlePayload` units
  (kind = `CompiledArticle`); see
  [`knowledge-units-roadmap.md`](knowledge-units-roadmap.md) §5.6.
- Maintainer agent = `SummaryPromotionJob` scheduled by `CompactionWorker`;
  see [`compaction-roadmap.md`](compaction-roadmap.md) §4.5.
- Frontmatter `injection_count` becomes a retrieval signal:
  `CompiledArticlePayload.injection_count`. After 60+ days of zero
  injections, mark `status = archived` (lifecycle transition to `Deprecated`).

### §5.2. A-MEM (Zettelkasten-based agentic memory)

**What it is.** Atomic notes in a vector store with LLM-driven link
generation between top-K nearest neighbours and per-note evolution
(LLM rewrites keywords, tags, contextual description of existing notes when
new notes arrive). NeurIPS 2025 paper, 2× F1 vs MemGPT on multi-hop
reasoning, 85-93% token reduction vs MemGPT.

[Source: arXiv:2502.12110 — A-MEM paper (NeurIPS 2025)]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Zettelkasten-based agentic memory — A-MEM pattern.md]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/resources/llm-research/A-MEM - Agentic Memory for LLM Agents (arXiv 2502.12110) - разбор статьи.md]

**Datasets evaluated.** Two benchmarks, оба long-term conversational:

- **LoCoMo** (Maharana et al., 2024) — long-term conversational memory benchmark; диалоги в среднем 9K токенов, до 35 сессий; 7,512 question-answer пар; 5 категорий вопросов (single-hop, multi-hop, temporal reasoning, open-domain, adversarial).
- **DialSim** (Kim et al., 2024) — long-term multi-party диалоги из TV-shows (Friends, TBBT, The Office); 1,300 сессий, ~350K токенов, 1000+ вопросов на сессию.

**Models evaluated.** Шесть foundation models в основном эксперименте: GPT-4o-mini, GPT-4o, Qwen2.5 1.5B, Qwen2.5 3B, Llama 3.2 1B, Llama 3.2 3B. Local deployment через Ollama + LiteLLM для structured outputs. Appendix дополнительно покрывает DeepSeek-R1-32B, Claude 3.0 Haiku, Claude 3.5 Haiku.

**Latency.** GPT-4o-mini на memory operation — **5.4 секунды**; Llama 3.2 1B локально — **1.1 секунды** на ту же операцию. Это ~5× разница, плюс разница в стоимости (commercial API vs self-hosted).

**Ablation study (GPT-4o-mini, LoCoMo benchmark).** Multi-Hop F1:

| Variant | Multi-Hop F1 | Temporal F1 |
|---|---|---|
| w/o Link Generation + Memory Evolution | 9.65 | 24.55 |
| w/o Memory Evolution only | 21.35 | 31.24 |
| **Full A-MEM** | **27.02** | **45.85** |

Без Link Generation и Memory Evolution — почти в 3 раза хуже на multi-hop. Memory Evolution даёт дополнительные ~6 пунктов F1 поверх Link Generation. Это эмпирическое подтверждение того, что **оба** механизма — LG и ME — критичны; нельзя оставить только один.

**Prompt templates.** Paper содержит prompt templates в Appendix B:

- **B.1 Note Construction** — LLM получает interaction и генерирует `keywords`, `tags`, `contextual_description`.
- **B.2 Link Generation** — LLM получает новую note и candidate links → решает, какие установить.
- **B.3 Memory Evolution** — LLM получает existing memory + new memory → решает, какие поля existing memory обновить.
- **B.4 Examples** — Q/A примеры с трассировкой.

Наша интеграция через `IQueryTransformer` adapter — см. §5.2.4 ниже.

**Zettelkasten historical context.** Метод картотеки, разработанный немецким социологом Никласом Луманом (1927-1998). Каждая карточка содержит одну идею и связывается с другими через явные ссылки. Луман использовал Zettelkasten для написания **70+ книг и 400+ академических статей**. Источник: Ahrens S. "How to Take Smart Notes" (2017). Авторы A-MEM прямо ссылаются на этот метод как на источник вдохновения. Key insight для нашего проекта: **atomic notes + linking** как технология для управления знанием, переносимая из бумажной картотеки в LLM-driven memory.

**On `agent-memory-cpp` mapping.** A-MEM's atomic notes → `Note` kind with
generic payload (no dedicated payload; primary_text holds the note body).
Link generation is **not** first-class; it requires the `IQueryTransformer`
contract from [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) §13
(steps R31-R34, M2+). Per-note evolution needs an external LLM call —
a runtime service, not the C++ core.

**Use it when:** you want autonomous memory evolution without a maintainer
cron. The token reduction (1,200 vs 16,910 per operation) matters at scale.

**Don't use it when:** your corpus is small enough for manual curation
(under ~10k units), or when you need a fixed schema (A-MEM is schema-less by
design — type evolution depends on the LLM).

**Integration hints:**

- `Note` kind with `metadata_typed["tags"]` and `metadata_typed["links"]`
  (extensible typed metadata) covers the LLM-extracted keywords and links.
- A-MEM link generation = `IQueryTransformer` adapter running HyDE-like
  expansion on top-K nearest neighbours and writing `graph_edges_by_src`
  / `graph_edges_by_dst` entries.
- A-MEM per-note evolution = a `MergeJob` (M2+) targeting the `Note`
  unit, rewriting its primary_text + tags. Idempotency: rely on
  `envelope.revision` increments per evolution step.

### §5.3. lifemodel dual-layer (LanceDB + spreading activation graph)

**What it is.** Two layers: LanceDB vector store with salience decay, and
a custom graph store (entities + relations) with spreading activation.
Embeddings computed locally via Transformers.js, no API dependency. Plugins
isolated through a `PluginPrimitives` API.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Dual-layer memory retrieval LanceDB и spreading activation graph.md]

**On `agent-memory-cpp` mapping.** The LanceDB vector store maps to
`DenseVectors` capability (BinaryCandidateFilter or Hnsw mode, see
[`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) §6.2). The spreading
activation graph maps to `GraphRelations` capability with iterative
expansion (`GraphRetriever` via `GraphExpansionOptions` from
[`knowledge-base-roadmap.md`](knowledge-base-roadmap.md) §7.5). Salience
decay is `DecayPolicy` with `mode = Exponential` and explicit
`self_echo_suppression`.

**Use it when:** privacy is non-negotiable (no external embeddings API), you
have >100k entities, and you want structural memory on top of similarity
search.

**Don't use it when:** your retrieval patterns are dominated by exact-match
keyword queries (lifemodel is embedding-first). For keyword-heavy workloads,
`BasicRagStack` is closer.

**Integration hints:**

- Salience decay parameters from lifemodel are not public; start with the
  defaults in `AgentLongTermMemoryStack` (half_life = 7 days, use_boost =
  0.35, cooldown = 60s) and tune via golden-dataset benchmarks.
- Spreading activation depth: open question. Start with `max_depth = 2`,
  `min_weight = 0.05`. See open question in
  [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) §8.

### §5.4. NOUZ (Obsidian structured memory)

**What it is.** Local MCP server with strict YAML frontmatter
(`nouz_type`, `nouz_level`, `nouz_core`, `nouz_sign`). Five types, five
levels, three signs. Embeddings via PRIZMA used as second-layer observation
(drift detection), not primary retrieval. Manual human authoring is the
default mode.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/NOUZ — структурированная память для ИИ-агентов в Obsidian.md]

**On `agent-memory-cpp` mapping.** NOUZ's YAML schema can be encoded in
`metadata_typed` on `Note` or `Fact` units. The five levels become a
`metadata_typed["nouz_level"]` typed integer field. Embeddings as
observation only → keep dense capability off; rely on lexical only.

**Use it when:** you already have an Obsidian vault with manual hierarchy
and want to ingest it without disrupting the existing human-curated
structure. NOUZ's value is the schema, not the retrieval speed.

**Don't use it when:** retrieval speed is the bottleneck. NOUZ's
embedding-as-observation model means semantic drift detection, not
semantic retrieval — different optimisation target.

**Integration hints:**

- Map `nouz_type` to `KnowledgeUnitKind` (most kinds map to `Note` or
  `Fact`; some require `Custom` kind with `metadata_typed["payload"]`).
- Skip dense embedding generation by default; add `enable_dense_vectors =
  false` on the `BasicRagStack` configuration.
- Embedding anisotropy warning from NOUZ's author: raw cosine is unreliable
  on anisotropic distributions. Calibration is open question
  ([`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) §8
  question 7).

### §5.5. Mem0 / Letta / Zep Graphiti (memory frameworks)

**Mem0 (2026 version).** Open-source memory layer with multi-signal
retrieval (semantic + BM25 + entity matching fused by score). Two
deployment modes: library (local Qdrant in `/tmp/qdrant`) or self-host
(Postgres + pgvector). Four scope IDs: `user_id`, `agent_id`, `run_id`,
`app_id`.

[Source: github.com/mem0ai/mem0 — Mem0 open-source memory layer]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/tools/ai-agents/Mem0 — open-source memory layer для LLM-приложений (документация).md]

**On `agent-memory-cpp` mapping.** Mem0's multi-signal retrieval ≈
`HybridRetrievalEngine` with lexical + dense + graph (see
[`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) §7, real type).
Scope IDs map to `ScopeId` values; the four scope dimensions become four
prefixes or four parallel scopes.

**Use it when:** multi-tenant SaaS, persistent agents, structured facts with
known entity types.

**Don't use it when:** you have a custom schema that doesn't fit Mem0's
predefined entity types. `agent-memory-cpp` is more flexible on schema.

**Letta (formerly MemGPT).** Stateful agents with LM-managed page-in / page-out
between main context (fast) and external context (slow). Letta repository
split: V1 legacy SDK deprecated; `letta-code` is the active stack.

[Source: github.com/letta-ai/letta — Letta (formerly MemGPT)]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/tools/ai-agents/Letta (бывш. MemGPT) — stateful agents с advanced memory.md]

**On `agent-memory-cpp` mapping.** Letta is a different execution model
(LM-managed paging) that doesn't fit agent-memory-cpp's scope-aware key
model. Use Letta as an alternative stack when paging semantics matter more
than retrieval semantics. **Not** a substrate pattern.

**Use it when:** you want LM-managed paging and accept the MemGPT-class
token cost (16,910 tokens per question baseline).

**Don't use it when:** you need scope isolation across users. Letta
delegates paging to the LM, which doesn't know about secondary indexes or
our MDBX layout.

**Zep Graphiti.** Temporal knowledge graph with real-time incremental
updates (no batch recompute). Bi-temporal schema. Four backends: Neo4j,
FalkorDB, AWS Neptune, Kuzu.

[Source: github.com/getzep/graphiti — Zep Graphiti temporal knowledge graph]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/tools/ai-agents/Zep Graphiti — temporal knowledge graph для AI-агентов (документация).md]

**On `agent-memory-cpp` mapping.** Temporal graph ≈ `TemporalFactStoreStack`
with `GraphRelations` capability. Real-time incremental updates map to our
on-write compaction flow (`MultiTableWriter` in
[`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) §3, §12.7). Bi-temporal
schema: our `TemporalComponent` currently holds `valid_from_ms` /
`valid_until_ms` only (single temporal axis); bi-temporal extension is an
open question in [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) §8.

**Use it when:** you need temporal reasoning over customer history with
long time-series. Zep's bi-temporal model is mature; ours is single-axis.

**Don't use it when:** you need full bi-temporal today (our
`TemporalComponent` would need extension first). Or when you can use Zep
directly — don't reinvent.

### §5.6. Memoria (weighted-KG user modelling)

**What it is.** Two-component hybrid: dynamic session-level summarisation
plus weighted knowledge graph user modelling. Industry-grade personalisation
focus (AIML Systems 2025).

[Source: arXiv:2512.12686 — Memoria paper]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/resources/llm-research/Memoria - Scalable Agentic Memory Framework - конспект.md]

**On `agent-memory-cpp` mapping.** Weighted KG ≈ `GraphRelations` capability
with per-edge weights and `DecayPolicy` per edge. Session summarisation ≈
`MergeJob` over `ConversationEpisodePayload` units
(see [`compaction-roadmap.md`](compaction-roadmap.md) §4.3).

**Use it when:** you're building personalised conversational AI and want
edge-weight decay to model evolving preferences. The weighted-KG approach
scales better than flat-facts retrieval as preferences grow.

**Don't use it when:** your domain doesn't have persistent user traits to
model (transactional, no-personality workloads).

### §5.7. Блок фактов (lightweight flat facts)

**What it is.** Plain text or YAML in the system prompt; no retrieval,
just injected. Predicted behaviour encoding for a persona or user.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Блок фактов vs RAG-память.md]

**On `agent-memory-cpp` mapping.** Use `FactPayload` (see
[`knowledge-units-roadmap.md`](knowledge-units-roadmap.md) §5.3) with
`enable_fact_payload = true` on `BasicRagStack`. Skip dense vectors and
graph. Inject top-N facts as a `raw/` block into the prompt before each
agent turn.

**Use it when:** the complete fact block satisfies the prompt-injection
criteria below — bytes budget, access-control, no unresolved temporal or
conflict semantics, and lower cost than filtering.

**Don't use it when:** any of the prompt-injection criteria fail. RAG
becomes necessary when bytes budget is exceeded, per-request access
control is required, or facts have temporal/conflict semantics that
injection alone cannot enforce.

#### §5.7.1. Prompt-injection criteria (no retrieval)

Full prompt injection (no retrieval) is reasonable ONLY when the complete block:

- fits the reserved prompt budget (typically 4K-8K tokens for stable facts);
- is safe to expose for this specific request without bypassing per-user filtering;
- has no unresolved temporal or conflict semantics;
- remains cheaper than filtering (else use retrieval).

Pure count thresholds (e.g., "≤100 facts") are misleading. The right thresholds are bytes, risk characteristics, and update velocity — not object count.

### §5.8. СВИНОПАС (live-agent memory with anti-loop)

**What it is.** Custom "дабазч" storage (trash-can-like) plus entity graph
plus embeddings. Fine-tuned "Гном Гномыч" extractor. Embedding-based
retrieval plus a "Строитель" with anti-loop scoring plus spreading activation
on a subgraph. Cooldown on recently-used facts plus decay by use-count.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/rag-knowledge/Внешняя память LLM-агентов — система СВИНОПАС.md]

**On `agent-memory-cpp` mapping.** Anti-loop scoring is `DecayPolicy`
plus `AntiLoopCooldown` filter (see
[`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) §10, step 10). Spreading
activation on subgraph is `GraphRetriever` with `GraphExpansionOptions`.
Cooldown is `UsageStatsComponent.cooldown_until_ms`. The Гном Гномыч
fine-tuned extractor is external.

**Use it when:** live-streaming with ASR noise and anti-loop requirement
is critical (voice assistants, live chat agents).

**Don't use it when:** your workload is batch retrieval. Cooldown was
designed for voice-streamed chat; on batch Q&A the cooldown curve is wrong
(see [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) §8
question 8).

### §5.9. Dual-layer (LanceDB + spreading activation)

See §5.3 (lifemodel dual-layer) — this is the same family.

### §5.10. J-Space (in-model active memory)

**What it is.** Subset of the model's residual stream (~k≤25 J-lens
vectors, ≤10% variance) that the model "sees" as the contents of its current
thinking. Functional analogue of global workspace from neuroscience of
consciousness. Not storage; active state during forward pass.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Сравнение подходов организации памяти и контекста LLM.md]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Global Workspace и J-Space — функциональный аналог сознания в LLM.md]

**On `agent-memory-cpp` mapping.** None. J-Space is in-model, not on
substrate. We cannot store or retrieve from a model's residual stream.

**Use it when:** you are designing prompts and context assembly to nudge
intermediate computation. Not a retrieval layer concern.

### §5.11. RLM (Recursive Language Models)

**What it is.** Python REPL where the context is an external variable.
Root LM writes code to investigate; sub-RLM for fragments. Near-infinite
context (10M+ tokens, 1000+ documents); completeness-critical.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Сравнение подходов организации памяти и контекста LLM.md]

**On `agent-memory-cpp` mapping.** Pair as external REPL adapter: Python
REPL loads MDBX state through the C++ ABI, runs RLM-style exploration.
agent-memory-cpp provides the persistent storage; RLM provides the
on-demand code-driven retrieval.

**Use it when:** completeness-critical multi-hop reasoning over 10M+ tokens
and you have a code-capable LLM available. RLM is expensive (each query
can spawn multiple sub-LM calls).

**Don't use it when:** you don't have a code-capable model, or your
corpus fits within RAG-level retrieval. RAG wins on cost and predictability
when context size allows.

### §5.12. DLP-bridge / ToM sidecar

**What it is.** Theory-of-Mind sidecar: separate LLM call per request
returning structured JSON (intent, constraints, style). TTL on claims,
explicit status field, mental_hypothesis with confidence and evidence_event_ids.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/DLP-подобная архитектура профилирования для AI-агента.md]

**On `agent-memory-cpp` mapping.** Not first-class. Closest analog is
`IntentRouter` (planned, M2+) which classifies intent before retrieval.
ToM-sidecar is an external IPC call returning structured JSON, then merged
into the prompt by a `ContextBuilder` extension.

**Use it when:** you want persistent user modelling with evidence chain
and explicit confidence scoring. The evidence chain aligns with our
`SourceRef` model (see [`knowledge-units-roadmap.md`](knowledge-units-roadmap.md) §3).

**Don't use it when:** your use case is generic RAG over documents. ToM
sidecar is for personal-assistant scenarios, not corpus search.

### §5.13. Multi-dimensional context graph (AST + Git + sessions + ...)

**What it is.** Runtime multi-node graph: AST + Git history + AI sessions +
tickets + feature flags + clients + logs + metrics + obligations + policy.
AI navigates it as an operator.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Исполняемый граф контекста - AST Git sessions logs obligations.md]

**On `agent-memory-cpp` mapping.** Partial. `GraphRelations` capability
covers the graph substrate (DBI layout in
[`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) §12.3). But the
node-type heterogeneity (AST + Git + sessions + tickets + obligations) is
**not** a feature of agent-memory-cpp; each node type would need a custom
kind and payload.

**Use it when:** you are extending agent-memory-cpp with adapter ingest
for these node types (via `IResourceStore` adapter pattern from
[`architecture.md`](architecture.md) "Planned Resource Reindexing Direction").

**Don't use it when:** your graph is small enough to fit Zep Graphiti
directly. Don't reinvent.

## §6. Cross-architecture comparison table

Compressed view of the architectures above. Latency and cost figures are
**typical baselines from the cited sources**; transfer to your workload is
not validated. Use "approximately" when reading numbers whose transfer is
unvalidated.

| Architecture | Storage medium | Retrieval model | Update pattern | Latency | Cost |
|---|---|---|---|---|---|
| **Karpathy 3-layer** | SQLite + Mem0 + markdown wiki | LLM-maintained keyword filter + `findRelevant()` | Wiki-maintainer 6h cron | injection + topical facts | low-moderate |
| **A-MEM** | Vector store (atomic notes) + kv attributes | cosine + LLM-judged links | LLM evolves note attributes | approximate 1,200 tok/Q | low (under $0.0003 per op) |
| **lifemodel dual-layer** | LanceDB + custom graph | spreading activation + cosine | salience decay | moderate (plugin-managed) | none external |
| **NOUZ** | Local MCP + YAML frontmatter + PRIZMA | Manual navigation + embedding drift detection | Manual tiering | moderate (manual) | none |
| **Mem0 (2026)** | Local Qdrant or Postgres+pgvector | Semantic + BM25 + entity fused | Async background extraction + dedupe | moderate | moderate (~6,956 tok/Q on LoCoMo) |
| **Letta / MemGPT** | LM-managed paging | LM tool calls | LM-driven | high | high (~16,910 tok/Q baseline) |
| **Zep Graphiti** | Temporal KG (Neo4j / FalkorDB / Neptune / Kuzu) | Hybrid semantic + keyword + graph | Real-time incremental | low (no batch) | moderate |
| **Memoria** | Session summary + weighted KG | KG traversal + entity weighting | Incremental | moderate | moderate |
| **Блок фактов** | Plain text / YAML in prompt | None (injection) | Manual or "remember X" tool | near-zero | trivial |
| **СВИНОПАС** | Custom "дабазч" + entity graph | Embedding + Строитель + spreading activation | Cooldown + decay | dual: 20-50 ms associative + 3-4 s targeted | custom fine-tune cost |
| **J-Space** | In-model residual stream | Emergent (forward pass) | None (emergent) | inherent | inherent |
| **RLM** | Python REPL with corpus as variable | Root LM writes code, sub-RLM for fragments | None (LM re-derives) | variable (LM-call count) | high |
| **DLP-bridge / ToM** | event_log + memory_claim + mental_hypothesis | ToM sidecar LLM call | TTL + status field | per-request sidecar cost | high (per-request LLM) |
| **Исполняемый граф** | Multi-node graph (AST + Git + sessions + …) | Live-pipeline indexing; AI slices per task | Live update on new commits/sessions/etc. | per-task slice | depends on adapters |
| **Vault Audit AI** | Obsidian notes + note-index.json + salience decay | MapReduce audit + clustering + MOC | LLM atomisation + spaced repetition | local via Ollama | low |
| **agent-memory-cpp defaults** | MDBX (envelope + components + projections + Graph + Dense) | `HybridRetrievalEngine` (real type) with RRF, decay, anti-loop | on-write cheap + on-schedule heavy (`CompactionWorker`) | p95 ≈ 50 ms M0, decay/hybrid overhead M1 | storage + occasional LLM for SummaryPromotion |

> **Real** (verified by `git ls-files 'src/agent_memory/**/*.hpp'`):
> `HybridRetrievalEngine`, `IRetriever`, `IRetrievalEngine` exist as real types
> in `src/agent_memory/retrieval/`. `MemoryStack`, `MemoryProfileSpec`,
> `CompactionWorker` are documented but not yet present in
> `src/agent_memory/`; treat them as roadmap names that the rest of the
> codebase refers to until the implementation PRs land. See
> [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) and
> [`compaction-roadmap.md`](compaction-roadmap.md) for the planned
> contracts.

## §7. Hybrid patterns

Different architectures compose. Some compositions are tested by the
playbook; some are sketched but unvalidated.

### §7.1. Karpathy file-wiki + A-MEM atomic-note overlay

Karpathy's wiki provides structured articles (the "what"). A-MEM's
atomic-note overlay provides autonomous link generation and per-note
evolution (the "how they connect"). Together: structured synthesis plus
schema-less evolution.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Трёхслойная память для AI-агентов - метод Карпати.md]
<br>[Source: arXiv:2502.12110 — A-MEM paper (NeurIPS 2025)]

**On `agent-memory-cpp`:** `CompiledWikiStack` (wiki layer) +
`AgentLongTermMemoryStack` (atomic notes + graph) in two parallel scopes.
Wiki article updates trigger atomic-note reindexing via the manifest
(`DerivedRecordKind` entries, see
[`knowledge-units-roadmap.md`](knowledge-units-roadmap.md) §7).

### §7.2. Karpathy + spreading activation dual-layer

Wiki articles as anchor nodes; spreading activation graph underneath for
navigating from article to supporting facts.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Трёхслойная память для AI-агентов - метод Карпати.md]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Dual-layer memory retrieval LanceDB и spreading activation graph.md]

**On `agent-memory-cpp`:** `CompiledWikiStack` with `GraphRelations`
enabled. Wiki article retrieval expands to supporting `Fact` or `Chunk`
units through bounded BFS (`GraphExpansionOptions.max_depth = 2`,
`min_weight = 0.05`).

### §7.3. Karpathy + RLM

Wiki for static synthesis (offline, periodic). RLM for on-demand
exploration (online, per-query).

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Трёхслойная память для AI-агентов - метод Карпати.md]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Сравнение подходов организации памяти и контекста LLM.md]

**On `agent-memory-cpp`:** Wiki layer as `CompiledWikiStack`; RLM as
external Python REPL adapter that reads from the same MDBX store via
C++ ABI. RLM replaces the retrieval-stage of the Wiki layer for
completeness-critical queries.

### §7.4. Блок фактов + RAG

Stable persona facts in the prompt (Блок); evolving KB content in RAG.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Блок фактов vs RAG-память.md]

**On `agent-memory-cpp`:** Two scopes under one stack. Persona facts are
`Fact` units in a `persona` scope; KB content is `Chunk` units in a `kb`
scope. Prompt assembly: inject persona facts directly, retrieve from KB.

### §7.5. Mem0 + Zep temporal

Long-term facts plus temporal ordering. Mem0's multi-signal retrieval +
Zep's bi-temporal graph.

[Source: github.com/mem0ai/mem0 — Mem0 open-source memory layer]
<br>[Source: github.com/getzep/graphiti — Zep Graphiti temporal knowledge graph]

**On `agent-memory-cpp`:** `AgentLongTermMemoryStack` with
`enable_temporal_validity = true`. Our `TemporalComponent` is single-axis
(`valid_from_ms` / `valid_until_ms`); bi-temporal requires extension (open
question, see [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) §8).

### §7.6. Wiki + Codebase Graph (structural + narrative memory)

Wiki layer for "why" decisions; symgraph / code graph for "what calls what".

[Source: github.com/langchain-ai/openwiki — OpenWiki continuous documentation]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Автоматическая документация как память агента — OpenWiki и подходы.md]

**On `agent-memory-cpp`:** `CompiledWikiStack` (narrative) +
`AgentLongTermMemoryStack` (structural via `GraphRelations`). For
codebase-as-graph, extend with `IResourceStore` adapter that ingests
AST-derived node/edge records. See
[`architecture.md`](architecture.md) "Planned Resource Reindexing Direction"
and [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) for the
Bounded BFS + schema introspection pattern.

### §7.7. Self-Evolving Memory (Cole Medin) + DecayPolicy

Compounding knowledge loop with anti-loop cooldown to prevent wiki bloat.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Self-Evolving Memory для Claude Code на основе LLM Knowledge Base.md]

**On `agent-memory-cpp`:** `CompiledWikiStack` with daily `SummaryPromotionJob`
plus `DecayPolicy` (`half_life = 7d`, `self_echo_suppression = 0.3`). Anti-мусор
mechanics (injection_count + archive after 60 days) become
`CompiledArticlePayload.status = archived` + `LifecycleState::Deprecated`.

## §8. Migration patterns

Realistic growth path. Most workloads start small and grow. Each step is a
separate change with its own rollback path.

### §8.1. chat-only → + AGENTS.md

No memory stack. Add `AGENTS.md` to the repo. Operator-level rule storage.
Cost: zero. Latency: zero.

### §8.2. + skill pool

Move repeated patterns from `AGENTS.md` into discrete skills. Each skill
becomes a tool call from the agent. Still no retrieval stack.

### §8.3. + file-wiki

Add `raw/` + `wiki/` + `index.md` structure (the LLM Wiki pattern).
See [`usage-llm-wiki.md`](usage-llm-wiki.md) for the full setup.
On `agent-memory-cpp`: this is the "files-primary" path; can be
`CompiledWikiStack` later, but the early file-based version doesn't need
the C++ stack yet.

### §8.4. + RAG

Add `BasicRagStack` for retrieval over the `raw/` corpus. The file-wiki
stays as the human-readable mirror; RAG is the agent-side retrieval path.
In-place minor upgrade: open the existing MDBX file with
`MemoryProfiles::BasicRag()` after writing one Chunk unit per raw file.

### §8.5. + atomic notes

Add `Note` kind (or `Fact` kind) for evolving knowledge. Switch to
`AgentLongTermMemoryStack` (in-place minor upgrade: add `enable_usage_stats`,
`enable_decay`, `enable_fact_payload`). Begin tracking usage and applying
decay.

### §8.6. + graph overlay

Add `GraphRelations` capability. Major migration: new MDBX file via
`agent-memory-cli profile-migrate` (planned CLI; see
[`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) §14.2). Connect
atomic notes through LLM-generated edges (A-MEM-style) or manual edges
(NOUZ-style).

### §8.7. + compiled articles (wiki synthesis)

Add `CompiledArticles` capability and `enable_compaction = true`.
`SummaryPromotionJob` inside `CompactionWorker` becomes the
wiki-maintainer (Karpathy-style). Validation rule: CompiledArticles
requires Compaction (see [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md)
§10 row 7).

### §8.8. + bi-temporal (optional)

Extend `TemporalComponent` with observed_at_ms for bi-temporal semantics.
Not in the planned M1/M2 scope; consider only when Zep-class temporal
ordering becomes a hard requirement.

### §8.9. + RLM adapter (optional)

External Python REPL adapter that reads MDBX state via C++ ABI and runs
RLM-style exploration. Pure runtime extension; no changes to the C++ stack.

## §9. References

### §9.1. Public sources cited

- **arXiv:2502.12110** — "A-MEM: Agentic Memory for LLM Agents" (NeurIPS 2025).
  Zettelkasten-based atomic notes + LLM-driven linking + per-note evolution.
- **arXiv:2512.12686** — "Memoria: Scalable Agentic Memory Framework"
  (AIML Systems 2025). Weighted KG + session summarisation for
  industry-grade personalisation.
- **github.com/mem0ai/mem0** — Mem0 open-source memory layer. Multi-signal
  retrieval (semantic + BM25 + entity), 4-scope isolation, two deployment modes.
- **github.com/letta-ai/letta** — Letta (formerly MemGPT). LM-managed
  page-in/page-out; `letta-code` is the active stack, V1 legacy SDK
  deprecated.
- **github.com/getzep/graphiti** — Zep Graphiti temporal knowledge graph.
  Real-time incremental updates, bi-temporal schema, 4 backends.
- **github.com/langchain-ai/openwiki** — OpenWiki continuous documentation
  pipeline. CLI + TUI + GitHub Actions daily PR for AI-first codebase docs.
- **Karpathy talks on LLM-as-OS (2025)** — 3-layer memory pattern
  (conversations + facts + wiki with maintainer cron). No canonical arXiv
  or blog post; the public attribution is the talks themselves and
  practitioner write-ups (see internal note for transcription provenance).

### §9.2. Internal notes cited

- `ai-agent-playbook/concepts/ai-agents/Трёхслойная память для AI-агентов - метод Карпати.md`
  — Karpathy 3-layer memory + 4-й слой Obsidian; Anna_AI 4-tier variant.
- `ai-agent-playbook/concepts/ai-agents/Уровни памяти AI-агента — от контекста до fine-tuning.md`
  — 7-tier hierarchy + 6.5 RLM + chasing_nlp classification.
- `ai-agent-playbook/concepts/ai-agents/Блок фактов vs RAG-память.md`
  — Блок фактов (system prompt, no retrieval, ≤100 facts).
- `ai-agent-playbook/concepts/ai-agents/Dual-layer memory retrieval LanceDB и spreading activation graph.md`
  — lifemodel dual-layer, salience decay, spreading activation.
- `ai-agent-playbook/concepts/ai-agents/Zettelkasten-based agentic memory — A-MEM pattern.md`
  — A-MEM re-usable architectural pattern.
- `ai-agent-playbook/concepts/ai-agents/Self-Evolving Memory для Claude Code на основе LLM Knowledge Base.md`
  — Cole Medin + Karpathy compiler analogy + Compounding Knowledge Loop.
- `ai-agent-playbook/concepts/ai-agents/NOUZ — структурированная память для ИИ-агентов в Obsidian.md`
  — NOUZ LUCA/PRIZMA/SLOI tiers; embedding anisotropy.
- `ai-agent-playbook/concepts/rag-knowledge/Внешняя память LLM-агентов — система СВИНОПАС.md`
  — СВИНОПАС anti-loop + spreading activation + Гном Гномыч.
- `ai-agent-playbook/concepts/ai-agents/DLP-подобная архитектура профилирования для AI-агента.md`
  — ToM sidecar + evidence chain + ethical boundaries.
- `ai-agent-playbook/concepts/ai-agents/Исполняемый граф контекста - AST Git sessions logs obligations.md`
  — Multi-dimensional backlog surface.
- `ai-agent-playbook/concepts/llm-research/Сравнение подходов организации памяти и контекста LLM.md`
  — Long context → Naive RAG → MemGPT → Mem0 → Zep → RLM → A-MEM spectrum;
  J-Space as in-model layer.
- `ai-agent-playbook/concepts/llm-research/Global Workspace и J-Space — функциональный аналог сознания в LLM.md`
  — J-Space theoretical background.
- `ai-agent-playbook/concepts/ai-agents/Автоматическая документация как память агента — OpenWiki и подходы.md`
  — OpenWiki + symgraph composition for coding agents.
- `ai-agent-playbook/resources/llm-research/Memoria - Scalable Agentic Memory Framework - конспект.md`
  — Memoria weighted-KG + session summarisation deep dive.
- `ai-agent-playbook/tools/ai-agents/Mem0 — open-source memory layer для LLM-приложений (документация).md`
  — Mem0 OSS components, two deployment modes, LoCoMo benchmark.
- `ai-agent-playbook/tools/ai-agents/Letta (бывш. MemGPT) — stateful agents с advanced memory.md`
  — Letta repository split + SDKs.
- `ai-agent-playbook/tools/ai-agents/Zep Graphiti — temporal knowledge graph для AI-агентов (документация).md`
  — Graphiti bi-temporal, 4 backends.
- `ai-agent-playbook/resources/llm-research/A-MEM - Agentic Memory for LLM Agents (arXiv 2502.12110) - разбор статьи.md`
  — A-MEM original paper breakdown.

### §9.3. In-house guides

- [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) — ADR-001 through
  ADR-015, `MemoryProfileSpec`, `MemoryStack`, capability matrix, validation,
  M0/M1/M2 maturity, runtime services.
- [`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md) —
  canonical physical MDBX manifest and DBI budget.
- [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) —
  comparison framework, adoption ladder, design-pattern mining.
- [`knowledge-base-roadmap.md`](knowledge-base-roadmap.md) — retrieval flow,
  `HybridRetrievalEngine` (real), `DecayAwareRetriever`, evaluation pipeline.
- [`knowledge-units-roadmap.md`](knowledge-units-roadmap.md) — per-kind
  payload components (QAPayload, FactPayload, ChunkPayload, ConversationEpisode,
  CompiledArticle), lifecycle FSM.
- [`lexical-search-roadmap.md`](lexical-search-roadmap.md) — BM25/BM25F,
  projections, Cyrillic morphology.
- [`optimization-roadmap.md`](optimization-roadmap.md) — vector/binary/ANN.
- [`policies-roadmap.md`](policies-roadmap.md) — DecayPolicy, WritePolicy,
  SpeakerScopePolicy.
- [`compaction-roadmap.md`](compaction-roadmap.md) — `CompactionWorker` (planned),
  job types, handoff structure.
- [`usage-llm-wiki.md`](usage-llm-wiki.md) — operator guide for the LLM Wiki
  pattern on top of `agent-memory-cpp`.
- [`architecture.md`](architecture.md) — 4-layer model + maturity levels.
