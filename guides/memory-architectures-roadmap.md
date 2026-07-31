# Memory Architectures Roadmap

> C++17 compliance: кодовые сниппеты используют `const std::vector<T>&` вместо `std::span` и явные конструкторы вместо designated initializers. Сравнительные таблицы архитектур построены на материале из `ai-agent-playbook` и поданы как ориентир для capability-aware проектирования, а не как обязательство по совместимости.

## Source attribution policy

Этот гайд синтезирует материал из нескольких источников. Цитаты следуют двухуровневому паттерну:

- `[Source: <public citation>]` — основная цитата на опубликованную работу, документацию проекта, ревизию репозитория или статью. При цитировании внешнего источника публичная ссылка авторитетна.
- `[Source: internal note — no public source available. Path: <path>]` — утверждение взято только из внутренних исследовательских заметок без эквивалентного публичного источника на момент написания. Требует дополнительной проверки.

Внутренние заметки — это discovery aids, а не авторитетные цитаты. Публичные roadmap-заявления цитируют публичный источник первым; внутренние заметки служат лишь маркером частного происхождения.

Документ фиксирует **спектр внешних memory-архитектур** для LLM-агентов и сопоставляет их с ролями, которые `agent-memory-cpp` готов принять на себя. Цель — дать набор решений «когда что выбирать», а не канонизировать один источник истины. Сравнения построены на цитатах из первичных конспектов в `ai-agent-playbook`.

Этот roadmap — компас для выбора архитектуры. Реализационные слои
(envelope/components/projections, profiles, decay-aware scoring) живут в
[`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) (in-house стеки для
`agent-memory-cpp`); единственный canonical physical MDBX manifest and DBI
budget живут в [`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md)
§5.5/§5.5.1. Этот документ смотрит наружу: какие внешние архитектуры
конкурируют или дополняют наш подход.

Related roadmaps:

- [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) — внутренний манифест:
  ADR'ы, `MemoryProfileSpec`, `MemoryStack`, capability matrix, validation,
  M0/M1/M2 maturity.
- [`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md) §5.5/§5.5.1
  — sole canonical physical MDBX manifest and DBI budget.
- [`knowledge-base-roadmap.md`](knowledge-base-roadmap.md) — retrieval flow, decay-aware scoring, evaluation pipeline.
- [`lexical-search-roadmap.md`](lexical-search-roadmap.md) — BM25/BM25F первая линия, в которой наш стек пересекается с BM25-семейством из playbook.
- [`optimization-roadmap.md`](optimization-roadmap.md) — vector/binary/ANN optimization поверх retrieval primitives.

## §1. Purpose

Этот гайд существует для трёх целей:

1. **Comparison framework.** Свести в одну таблицу 13+ внешних memory-архитектур и сопоставить их по единому набору осей: storage medium, retrieval model, evolution mechanism, schema-driven vs schema-less, isolation, token efficiency, best-for workload.
2. **Adoption guidance.** Для каждой архитектуры пометить, что из неё `agent-memory-cpp` может либо (a) переиспользовать через нашу envelope/components/projections модель, либо (b) исследовать как runtime-сервис, либо (c) признать альтернативой, не встраиваемой в substrate.
3. **Design pattern mining.** Выделить паттерны, которые повторяются у разных архитектур (floating subgraph, spreading activation, salience decay, two-stage indexing, overlay graph on vector) и пометить, какие из них уже реализованы или запланированы в нашем стеке.

Non-goals:

- Не выбирать «лучшую» архитектуру. Спектр — это спектр: разные ниши, разные компромиссы.
- Не описывать внутренние классы `agent-memory-cpp`. Это ADR'ы и stack-спецификации, они в `memory-stacks-roadmap.md`.
- Не каталогизировать vector DB или graph DB. ANN-выбор — в [`optimization-roadmap.md`](optimization-roadmap.md).

See [`compression-is-intelligence-roadmap.md`](compression-is-intelligence-roadmap.md) for the conceptual backbone (prediction ↔ compression, "what good compression preserves") and [`vector-db-engineering-roadmap.md`](vector-db-engineering-roadmap.md) for backing-vector-store operational matrix.

## §2. Architecture Comparison

Таблица сравнения построена по материалам из следующих источников:

- `ai-agent-playbook/concepts/ai-agents/Трёхслойная память для AI-агентов - метод Карпати.md` (3-layer Karpathy + 4-й слой Obsidian)
- `ai-agent-playbook/concepts/ai-agents/Уровни памяти AI-агента — от контекста до fine-tuning.md` (7-tier hierarchy)
- `ai-agent-playbook/concepts/ai-agents/Блок фактов vs RAG-память.md` (lightweight flat facts)
- `ai-agent-playbook/concepts/ai-agents/Dual-layer memory retrieval LanceDB и spreading activation graph.md` (lifemodel plugin pattern)
- `ai-agent-playbook/concepts/ai-agents/Zettelkasten-based agentic memory — A-MEM pattern.md` (A-MEM, NeurIPS 2025)
- `ai-agent-playbook/concepts/ai-agents/Self-Evolving Memory для Claude Code на основе LLM Knowledge Base.md` (Cole Medin + Karpathy compiler analogy)
- `ai-agent-playbook/concepts/NOUZ — структурированная память для ИИ-агентов в Obsidian.md` (structured frontmatter-based memory)
- `ai-agent-playbook/concepts/rag-knowledge/Внешняя память LLM-агентов — система СВИНОПАС.md` (live-agent memory with anti-loop)
- `ai-agent-playbook/concepts/ai-agents/Исполняемый граф контекста - AST Git sessions logs obligations.md` (multi-dimensional context graph)
- `ai-agent-playbook/concepts/llm-research/Сравнение подходов организации памяти и контекста LLM.md` (Long context → Mem0 → MemGPT → Zep → RLM → A-MEM spectrum)
- `ai-agent-playbook/concepts/ai-agents/DLP-подобная архитектура профилирования для AI-агента.md` (ToM sidecar pattern, evidence chain)
- `ai-agent-playbook/resources/llm-research/Memoria - Scalable Agentic Memory Framework - конспект.md` (Memoria weighted-KG user modeling)
- `ai-agent-playbook/tools/ai-agents/Mem0 — open-source memory layer для LLM-приложений (документация).md` (Mem0 OSS components)
- `ai-agent-playbook/tools/ai-agents/Letta (бывш. MemGPT) — stateful agents с advanced memory.md` (Letta deprecated V1 SDK, active `letta-code`)
- `ai-agent-playbook/tools/ai-agents/Zep Graphiti — temporal knowledge graph для AI-агентов (документация).md` (Graphiti bi-temporal, 4 backends)
- `ai-agent-playbook/resources/llm-research/A-MEM - Agentic Memory for LLM Agents (arXiv 2502.12110) - разбор статьи.md` (original A-MEM paper)

| Архитектура | Storage medium | Retrieval model | Evolution mechanism | Schema | Isolation | Token efficiency | Best for |
|---|---|---|---|---|---|---|---|
| **Карпати 3-layer** (Conversations / Mem0 / Wiki) | SQLite для conversations; Mem0-style vector store для facts; markdown-файлы для wiki | LLM-maintained keyword filter + `findRelevant()` на wiki | Wiki-maintainer агент периодически переписывает markdown-статьи | Wiki: YAML frontmatter + `injection_count` | Per-bot (3-й слой multi-agent) | Wiki injection + topical facts | Multi-agent systems с cross-session continuity [Source: Karpathy talks on LLM-as-OS (2025)] <br>[Internal note: ai-agent-playbook/concepts/ai-agents/Трёхслойная память для AI-агентов - метод Карпати.md] |
| **A-MEM** (Zettelkasten agentic) | Atomic notes в vector store (Qdrant/Weaviate/pgvector в production, pgvector для прототипа) + kv-store для атрибутов | Embedding cosine + LLM-judged links между top-K ближайших notes | Per-note evolution: LLM обновляет keywords/tags/contextual_description существующих notes | Schema-less (LLM решает типы связей) | Per-collection; нет явных scope IDs в paper | 85-93% reduction vs MemGPT — ~1,200 vs 16,910 tokens per operation; <$0.0003 per memory op | Long-term personal assistants + multi-hop reasoning (2x F1 vs MemGPT) [Source: arXiv:2502.12110 — A-MEM paper (NeurIPS 2025)] <br>[Internal note: ai-agent-playbook/concepts/ai-agents/Zettelkasten-based agentic memory — A-MEM pattern.md] |
| **Self-Evolving Memory (Cole Medin)** | Markdown + Obsidian vault (`concepts/`, `connections/`, `daily-logs/`, `index.md`) | Session-start loads `agents.md + index.md`; agent navigates файловую систему | Background "compiler" agent daily-flushes daily-logs в wiki | Loose YAML frontmatter + статусы `active`/`archived` | Vault-level | Нет индекса → нет embedding cost | Internal session-logs synthesis, особенно для coding agents [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Self-Evolving Memory для Claude Code на основе LLM Knowledge Base.md] |
| **СВИНОПАС** (live-agent memory) | Кастомное «датабазч» хранилище (trash-can-like) + граф сущностей + embeddings; fine-tuned экстрактор «Гном Гномыч» | Embedding-based retrieval + "Строитель" с anti-loop scoring + spreading activation на подграфе | Cooldown на recently-used факты + decay по use-count | Entity graph с именованными рёбрами + lemma/alias resolution для русского | Per-stream/per-agent | Два канала: быстрые ассоциации (20-50 мс) + фоновый targeted поиск (3-4 сек) | Live-streaming агенты с ASR-шумом и anti-loop requirement [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/rag-knowledge/Внешняя память LLM-агентов — система СВИНОПАС.md] |
| **NOUZ** (Obsidian structured memory) | Локальный MCP-сервер с YAML frontmatter (`nouz_type`, `nouz_level`, `nouz_core`, `nouz_sign`) + SQLite embeddings в PRIZMA | Manual navigation + embedding-based drift detection (не primary retrieval) | Нет auto-evolution; manual hierarchy с пятью уровнями + три режима (LUCA/PRIZMA/SLOI) | Strict YAML schema (5 типов, 5 уровней, 3 знака) | Vault-level | Embeddings только как second-layer наблюдение; manual tiering для navigation | Личные Obsidian-базы с человеческим authoring + selective embedding assist [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/NOUZ — структурированная память для ИИ-агентов в Obsidian.md] |
| **Mem0** (2026 version) | OSS: local Qdrant в `/tmp/qdrant` (library) или Postgres+pgvector (self-host) + SQLite history | Multi-signal: semantic + BM25 + entity matching fused by score | Async background extraction + dedupe via embeddings | Predefined entity types | 4 scope IDs: `user_id`, `agent_id`, `run_id`, `app_id` | 6,956 tok/query на LoCoMo benchmark | Multi-tenant SaaS, persistent agents с structured facts [Source: github.com/mem0ai/mem0 — Mem0 open-source memory layer] <br>[Internal note: ai-agent-playbook/tools/ai-agents/Mem0 — open-source memory layer для LLM-приложений (документация).md] |
| **Letta / MemGPT** (stateful agents) | Page-in/page-out между main context (быстрая) и external context (медленная) | LM-managed paging tool calls | LM-maintained page-in/page-out | Schema-less, LM-driven | Per-agent | MemGPT baseline (16,910 tok/Q — высокая цена) | Persistent chat-агенты с очень длинной историей [Source: github.com/letta-ai/letta — Letta (formerly MemGPT)] <br>[Internal note: ai-agent-playbook/tools/ai-agents/Letta (бывш. MemGPT) — stateful agents с advanced memory.md] |
| **Zep / Graphiti** (temporal graph) | Temporal knowledge graph (Neo4j / FalkorDB / AWS Neptune / Kuzu) | Hybrid semantic + keyword + graph | Real-time incremental updates (no batch recompute) | Bi-temporal schema + custom entity/edge types | Multi-graph namespacing | Temporal reasoning first-class | Customer history с long time-series [Source: github.com/getzep/graphiti — Zep Graphiti temporal knowledge graph] <br>[Internal note: ai-agent-playbook/tools/ai-agents/Zep Graphiti — temporal knowledge graph для AI-агентов (документация).md] |
| **Memoria** (weighted KG user modeling) | Two-component hybrid: dynamic session-level summarization + weighted knowledge graph user-modeling | KG traversal + entity/edge weighting | Incremental capture — граф растёт по мере поступления данных | Weighted edges для traits/preferences/behaviors | Per-user (через KG weights) | Within token constraints — explicit design goal | Personalized conversational AI с industry focus (AIML Systems 2025) [Source: arXiv:2512.12686 — Memoria paper] <br>[Internal note: ai-agent-playbook/resources/llm-research/Memoria - Scalable Agentic Memory Framework - конспект.md] |
| **Memoria и DLP-bridge** (ToM sidecar) | `event_log` (immutable) + `memory_claim` (typed) + `mental_hypothesis` + `interaction_baseline` | ToM sidecar LLM-call per request → JSON для PromptBuilder | TTL на claims + explicit status field; mental_hypothesis с confidence + evidence_event_ids | Typed claims: preference/constraint/goal/skill/project/style/correction | Per-user_id | Optional JSON to sidecar | Continuous user-modeling для лучшего обслуживания (≠ profiling для манипуляции) [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/DLP-подобная архитектура профилирования для AI-агента.md] |
| **Блок фактов** (lightweight flat) | Plain text или YAML в system prompt | Никакого retrieval — просто инжектируется | Ручное или через tool-call «запомни X» | Никакой (flat list строк или YAML) | Нет | Минимальная: только prompt tokens | ≤100 фактов о персонаже/пользователе; predicted behaviour [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Блок фактов vs RAG-память.md] |
| **J-Space** (in-model active memory) | Inside residual stream модели (не хранилище, а active workspace) | Эмерджентно через обучение + CoT externalization | Нет (emergent state во время forward pass) | Нет | Per-forward-pass | Не замена storage — это active state | Что модель прямо сейчас держит в уме (active working memory) [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Сравнение подходов организации памяти и контекста LLM.md] |
| **RLM (Recursive Language Models)** | Python REPL — контекст = внешняя переменная | Root LM пишет код для исследования; sub-RLM для фрагментов | Нет (LM каждый раз re-derives strategy) | Любая структура, обрабатываемая программно | Per-REPL-session | Comparable к long-context — variable, зависит от LM | Near-infinite context (10M+ токенов, 1000+ документов); completeness-critical [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Сравнение подходов организации памяти и контекста LLM.md] |
| **Исполняемый граф контекста (Nemytchenko)** | Multi-node graph: AST + Git history + AI-сессии + тикеты + фича-флаги + клиенты + логи + метрики + obligations + policy | Live-pipeline индексация; AI-сбор среза под задачу | Новые commits/sessions/tickets/etc. → live update | Нет единой schema; каждый node-type имеет свою | Per-task surface | N/A — это модель данных, а не retrieval | Продакшн-система с runtime-графом, который AI обходит как оператор [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Исполняемый граф контекста - AST Git sessions logs obligations.md] |
| **Vault Audit AI** (LLM-driven vault) | Obsidian notes + `note-index.json` + salience decay | MapReduce-аудит + кластеризация + MOC из кластеров | LLM-атомизация существующих заметок + spaced repetition | Plugin-managed, JSON-based | Plugin-level | Локальный RAG через Ollama | Existing Obsidian vault, который хочется «проиндексировать» без миграции [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Dual-layer memory retrieval LanceDB и spreading activation graph.md] |
| **lifemodel dual-layer** | LanceDB (vector store с salience decay) + custom graph store (entities + relations + spreading activation); Transformers.js локально | Spreading activation + cosine (hybrid) | Salience decay + plugin-managed evolution | Plugin-level (PluginPrimitives API) | Plugin-level | Без API-зависимости полностью (локальные embeddings) | Privacy-first agents >100K entities с structural memory [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Dual-layer memory retrieval LanceDB и spreading activation graph.md] |

Несколько архитектур попадают сразу в несколько строк (например, Mem0 появляется и как Слой 2 у Карпати, и как самостоятельный memory framework). Для таких случаев таблица показывает режим работы по умолчанию, а не альтернативные реализации.

## §3. Decision Tree

Когда выбирать какую архитектуру — короткое дерево по результатам сравнения выше (плюс ссылки на сравнительные материалы в playbook):

```text
Запрос на новую memory capability?

├─ Фиксированный круг ≤100 фактов, no retrieval
│    → Блок фактов (system prompt / YAML)
│       [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Блок фактов vs RAG-память.md]
│
├─ Massive long-context (10K-200K), known structure
│    → Long-context frontier models без memory layer
│       [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Сравнение подходов организации памяти и контекста LLM.md]
│
├─ Live-streaming с ASR-шумом + anti-loop requirement
│    → СВИНОПАС (anti-loop + spreading activation)
│       [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/rag-knowledge/Внешняя память LLM-агентов — система СВИНОПАС.md]
│
├─ Personal Obsidian с manual hierarchy + selective embedding
│    → NOUZ (LUCA/PRIZMA/SLOI) или Vault Audit AI (LLM-driven)
│       [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/NOUZ — структурированная память для ИИ-агентов в Obsidian.md]
│
├─ Personal assistant, evolving preferences, no schema
│    → A-MEM (Zettelkasten-based)
│       [Source: arXiv:2502.12110 — A-MEM paper (NeurIPS 2025)] <br>[Internal note: ai-agent-playbook/concepts/ai-agents/Zettelkasten-based agentic memory — A-MEM pattern.md]
│
├─ Multi-tenant SaaS, persistent memory, structured facts
│    → Mem0 (semantic + BM25 + entity, 4 scopes)
│       [Source: github.com/mem0ai/mem0 — Mem0 open-source memory layer] <br>[Internal note: ai-agent-playbook/tools/ai-agents/Mem0 — open-source memory layer для LLM-приложений (документация).md]
│
├─ Customer history с temporal ordering
│    → Zep / Graphiti (temporal knowledge graph)
│       [Source: github.com/getzep/graphiti — Zep Graphiti temporal knowledge graph] <br>[Internal note: ai-agent-playbook/tools/ai-agents/Zep Graphiti — temporal knowledge graph для AI-агентов (документация).md]
│
├─ Persistent chat-agent с очень длинной историей (LM-managed paging)
│    → Letta (new letta-code stack) или MemGPT
│       [Source: github.com/letta-ai/letta — Letta (formerly MemGPT)] <br>[Internal note: ai-agent-playbook/tools/ai-agents/Letta (бывш. MemGPT) — stateful agents с advanced memory.md]
│
├─ Industry-grade personalization с weighted-KG user modeling
│    → Memoria (session summarization + weighted KG)
│       [Source: arXiv:2512.12686 — Memoria paper] <br>[Internal note: ai-agent-playbook/resources/llm-research/Memoria - Scalable Agentic Memory Framework - конспект.md]
│
├─ User-side modelling с evidence chain (DLP-bridge pattern)
│    → memory_claim / mental_hypothesis / ToM sidecar
│       [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/DLP-подобная архитектура профилирования для AI-агента.md]
│
├─ Production кодовая база с live pipeline + multi-dimensional backlog
│    → Исполняемый граф контекста (AST + Git + sessions + obligations)
│       [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Исполняемый граф контекста - AST Git sessions logs obligations.md]
│
├─ Multi-agent cross-session wiki + 6-hour maintaic cron
│    → Карпати 3-layer + Wiki-maintainer
│       [Source: Karpathy talks on LLM-as-OS (2025)] <br>[Internal note: ai-agent-playbook/concepts/ai-agents/Трёхслойная память для AI-агентов - метод Карпати.md]
│
├─ Privacy-first, no API, >100K entities, structural memory
│    → lifemodel dual-layer (LanceDB + custom graph + Transformers.js)
│       [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Dual-layer memory retrieval LanceDB и spreading activation graph.md]
│
├─ Completeness-critical, multi-hop, code-capable model
│    → RLM (Recursive Language Models)
│       [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Сравнение подходов организации памяти и контекста LLM.md]
│
└─ Минимальный уровень: AGENTS.md / skills / RAG
     [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Уровни памяти AI-агента — от контекста до fine-tuning.md]
```

Дерево не охватывает corner cases. Часто в одной системе живут 2-3 архитектуры одновременно (например, RAG как baseline + Блок фактов для персонажа + RLM для completeness-critical задач).

## §4. Memory-Tier Reference Stack

Иерархия уровней памяти по `Уровни памяти AI-агента — от контекста до fine-tuning.md` расширяется до восьми уровней на 2026 год (новый уровень 6.5 — RLM):

| Уровень | Механизм | Когда применять | Проблема перехода |
|---|---|---|---|
| 0 | Нет памяти — обычный чат | Разовая задача, решить и закрыть | Нет |
| 1 | Сохранённый чат | Нужен контекст в одной задаче | Не переносится между сессиями, не масштабируется |
| 2 | Стартовый файл (AGENTS.md / CLAUDE.md) | Правила нужны всегда во всех чатах | Файл разрастается, контекст засоряется шумом |
| 3 | Skills | Слишком много правил для одного файла | Дисциплина написания и актуализации |
| 4 | Авто-memories (provider-managed) | Личные задачи, минимальный контроль | Не контролируемое качество, непредсказуемый выбор |
| 5 | RAG / embeddings | Большие проекты, нетиповые задачи | Инфраструктура, настройка, стоимость |
| 6 | Fine-tuning | ML-специалисты, уникальная доменная экспертиза | Высокая стоимость, требует экспертизы |
| 6.5 | RLM (Recursive Language Models) | Multi-hop reasoning, code bases, completeness-critical | Непредсказуемая стоимость, нужен code-capable LLM |

Эта иерархия даёт coarse-grained look-up: «если моя задача влезает в уровень N и нет повода переходить на N+1, оставайся на N». Перепрыгивать уровни вверх обычно **плохая идея**: стоимость растёт непропорционально выигрышу [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Уровни памяти AI-агента — от контекста до fine-tuning.md].

Внутренние классификации из playbook (chasing_nlp):

```text
краткосрочная     → текущий чат/сессия (обрезается при переполнении)
долгосрочная      → предпочтения, решения, стиль (требует retrieval + контроль)
рабочая           → tool results текущей задачи (очищается после задачи)
эпизодическая     → события с timestamp (log, но не синтез)
семантическая     → общие знания (в весах, RAG или Wiki-слое)
```

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Уровни памяти AI-агента — от контекста до fine-tuning.md]

Эта schema совместима с иерархией уровней выше: краткосрочная ≈ сохранённый чат, долгосрочная/семантическая ≈ RAG/LLM-Wiki/structured memory, рабочая/эпизодическая ≈ state store, trace и run artifacts внутри harness.

## §5. Three Cross-Cutting Axes

При сравнении архитектур полезно смотреть на три ортогональные оси. Эти оси фигурируют в разных комбинациях у разных архитектур, и помогают понять, что именно мы переиспользуем.

### 5.1. Schema-less vs Schema-driven

| Полюс | Кто контролирует структуру | Архитектуры | Trade-off |
|---|---|---|---|
| Schema-less | LLM решает типы сущностей, рёбер, иерархию на лету | A-MEM, RLM, parts of NOUZ (через human override) | Flexibility высокая, но качество атрибутов зависит от базовой LLM |
| Schema-driven | Фиксированная таксономия (entity types, temporal schema, YAML frontmatter) | Mem0, Zep, Memoria, NOUZ (when explicit), Карпати YAML frontmatter | Predictable structure, но schema evolution требует миграций |

Наш стек — **ADR-driven schema**: `KnowledgeUnitKind` enum фиксирован, payload types per kind фиксированы, но `metadata_typed` допускает flexible extension без миграции (`guides/knowledge-units-roadmap.md`). Это ближе к полюсу schema-driven с escape hatch.

### 5.2. Append-only vs Decay-and-remove

| Полюс | Что происходит с хранилищем | Архитектуры | Trade-off |
|---|---|---|---|
| Append-only | Новые facts добавляются; старые остаются | Карпати Wiki, A-MEM (но с per-note evolution) | Полная история, растёт индекс |
| Decay | Salience/cooldown уменьшают retrieval weight; редко физически удаляются | lifemodel, СВИНОПАС (cooldown + decay по use-count), Карпати wiki (archive при `injection_count=0` и age>60d) | Эффективный retrieval, но возможна потеря долгосрочного контекста |
| Hybrid | Score-based decay + UI/archive transition | Mem0 (separate decay), NOUZ SLOI tier (5 уровней) | Контролируемо, но нужны политики для каждой transition |

Наш стек реализует hybrid (`policies-roadmap.md` + `compaction-roadmap.md`): decay меняет retrieval score, не удаляет записи; lifecycle FSM даёт `Active` / `Superseded` / `Deprecated` / `Erased`.

### 5.3. Index-primary vs Files-primary

| Полюс | Что первично | Архитектуры | Trade-off |
|---|---|---|---|
| Index-primary | Vector DB / graph store — источник истины; raw текст — производный | Mem0, Zep, A-MEM, lifemodel | Быстрый retrieval, но ре-индексация при изменении данных |
| Files-primary | Markdown / files — источник истины; индекс — пересобираемый кэш | Self-Evolving Memory (Cole Medin), NOUZ, Карпати Wiki | Простой rollback через git, но медленнее retrieval пока индекс не построен |

Наш стек — **index-primary с отдельным raw resource store**: `IResourceStore` хранит raw bytes, индексы в MDBX пересобираются через targeted reindexing (см. [`resource-reindexing.md`](resource-reindexing.md) и `lexical-search-roadmap.md` §Targeted Reindexing). Это компромисс: hot path работает по индексу, raw поддерживается для re-derivation и для новых chunker'ов.

## §6. Architectural Patterns Emerging from Comparison

Несколько паттернов повторяются в нескольких архитектурах. Эти повторения — подсказка, что они решают фундаментальные проблемы memory layer и могут быть реализованы в нашем стеке.

### 6.1. Floating subgraph

**Где:** СВИНОПАС (ограничение области поиска текущим контекстом) [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/rag-knowledge/Внешняя память LLM-агентов — система СВИНОПАС.md], lifemodel (spreading activation от seed-узла) [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Dual-layer memory retrieval LanceDB и spreading activation graph.md], Исполняемый граф (live-pipeline narrowing под задачу) [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Исполняемый граф контекста - AST Git sessions logs obligations.md].

**Контраст:** A-MEM (читает все notes через top-K) — работает, пока граф небольшой. На больших графах floating subgraph обязателен.

**У нас:** `GraphExpansionOptions` в `guides/knowledge-base-roadmap.md` §7.5 даёт `max_depth`, `max_edges`, `budget_tokens`, `allowed_edge_kinds`, `min_weight`. Это **именно** floating subgraph, реализуемый как bounded BFS retrieval view (не отдельная stored copy).

### 6.2. Spreading activation

**Где:** lifemodel (от seed-узла активация распространяется по соседним рёбрам с убывающим весом) [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Dual-layer memory retrieval LanceDB и spreading activation graph.md], A-MEM (links с LLM-judged relationship types → effective spreading через multi-hop links).

**У нас:** реализуемо через `GraphRetriever` + iterative expansion с `min_weight` pruning. Конкретные параметры decay-per-hop, radius activation ещё нужно валидировать.

### 6.3. Salience decay / anti-loop

**Где:** lifemodel (salience decay параметры не раскрыты в статье) [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Dual-layer memory retrieval LanceDB и spreading activation graph.md], СВИНОПАС (cooldown + decay по use-count + формулы релевантности) [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/rag-knowledge/Внешняя память LLM-агентов — система СВИНОПАС.md], Карпати wiki archive (`injection_count=0` и `last_updated > 60d` → archive) [Source: Karpathy talks on LLM-as-OS (2025)] <br>[Internal note: ai-agent-playbook/concepts/ai-agents/Трёхслойная память для AI-агентов - метод Карпати.md].

**У нас:** `DecayPolicy` (см. `policies-roadmap.md` §2.3, цитируется в `knowledge-base-roadmap.md` §7.4) — exponential decay + log use_boost + cooldown_factor + self_echo_suppression. Default `AgentLTM`: half_life=7d, use_boost=0.35, cooldown=60s, self_echo=0.3.

### 6.4. Two-stage indexing

**Где:** Mem0 (semantic + BM25 + entity, fused by score) [Source: github.com/mem0ai/mem0 — Mem0 open-source memory layer] <br>[Internal note: ai-agent-playbook/tools/ai-agents/Mem0 — open-source memory layer для LLM-приложений (документация).md], Карпати (raw conversations + Mem0-facts + wiki-articles) [Source: Karpathy talks on LLM-as-OS (2025)] <br>[Internal note: ai-agent-playbook/concepts/ai-agents/Трёхслойная память для AI-агентов - метод Карпати.md], Memoria (session summary + weighted KG) [Source: arXiv:2512.12686 — Memoria paper] <br>[Internal note: ai-agent-playbook/resources/llm-research/Memoria - Scalable Agentic Memory Framework - конспект.md].

**У нас:** two-stage projection model: `envelope.primary_text` (короткий seed для BM25F) + `SearchProjection`s (multi-projection retrieval views). Это и есть two-stage в нашем стеке.

### 6.5. Overlay graph on vector

**Где:** lifemodel [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Dual-layer memory retrieval LanceDB и spreading activation graph.md], Zep Graphiti (semantic + keyword + graph-based retrieval) [Source: github.com/getzep/graphiti — Zep Graphiti temporal knowledge graph] <br>[Internal note: ai-agent-playbook/tools/ai-agents/Zep Graphiti — temporal knowledge graph для AI-агентов (документация).md], Mem0 (semantic + BM25 + entity) [Source: github.com/mem0ai/mem0 — Mem0 open-source memory layer] <br>[Internal note: ai-agent-playbook/tools/ai-agents/Mem0 — open-source memory layer для LLM-приложений (документация).md].

**У нас:** ADR-006 фиксирует GraphStorage как DBI внутри одного MDBX env (не внешний graph DB). Это позволяет хранить overlay edges рядом с векторами и доставать через один cursor walk. Конкретный retrievers' contracts — в `knowledge-base-roadmap.md` §7.2 (`GraphRetriever`).

### 6.6. ToM sidecar / observation model

**Где:** DLP-bridge pattern [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/DLP-подобная архитектура профилирования для AI-агента.md] — отдельный sidecar LLM-call возвращает structured JSON (intent, constraints, style).

**У нас:** паттерн ближе к `IntentRouter` (`knowledge-base-roadmap.md` §7.6), но без user-profiling/policy layer. ToM-sidecar — потенциальная фича для M2+ через optional IPC к Python worker (см. секцию 7 ниже).

### 6.7. Atomic notes + LLM-driven linking

**Где:** A-MEM [Source: arXiv:2502.12110 — A-MEM paper (NeurIPS 2025)] <br>[Internal note: ai-agent-playbook/concepts/ai-agents/Zettelkasten-based agentic memory — A-MEM pattern.md], NOUZ (manual linking) [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/NOUZ — структурированная память для ИИ-агентов в Obsidian.md], Карпати Wiki (frontmatter-driven hierarchy).

**У нас:** не реализовано из коробки. `graph_edges_by_src` / `graph_edges_by_dst` DBIs (`guides/memory-stacks-roadmap.md`) дают substrate, но автоматическая link generation и per-note evolution — потенциальные runtime services (M2+).

## §7. Adoption Ladder for `agent-memory-cpp`

Какие архитектуры мы можем воспроизвести через нашу envelope/components/projections модель, какие требуют внешней координации, какие признаём альтернативами.

### 7.1. Drop-in на наш substrate (M0/M1)

Эти архитектуры естественно ложатся на наш стек — capability flags уже предусмотрены ADR или близки к существующим `MemoryProfileSpec`.

> **Real** (есть в code): `HybridRetrievalEngine` (`src/agent_memory/retrieval/HybridRetrievalEngine.hpp`). **Proposed API sketch — not implemented.** `HybridRetriever` и `GraphRetriever` в этой таблице — illustrative orchestrator-имена для описания архитектурного паттерна; реальный тип — `HybridRetrievalEngine`. Capability-флаги (`Lexical + DenseVectors + GraphRelations`) — также proposed, не зафиксированы в ADR.

| Архитектура | Что подходит | Capability соответствие |
|---|---|---|
| **Блок фактов (flat)** | Через `Fact kind` + `FactPayload` + короткий `primary_text` | `enable_fact_payload = true` (M0 готов) |
| **Mem0 multi-signal** | Через `HybridRetriever` (BM25F + Dense + Graph) + scope-aware secondary indexes | `Lexical + DenseVectors + GraphRelations` capability combo |
| **Memoria weighted KG** | Через `GraphRetriever` с edge weights + `DecayPolicy` per-edge | `GraphRelations + DecayAntiLoop` |
| **A-MEM (частично)** | Through atomic `Note kind` + graph edges; но per-note evolution — runtime service | Не auto: нужна background LLM-call (M2+) |
| **Карпати Wiki (частично)** | Through `CompiledArticle kind` + `CompiledArticlePayload` + `GraphRetriever` | `enable_compiled_article = true` |

### 7.2. Требует новый runtime-сервис (M2+)

Паттерны, которые требуют background worker / IPC к внешнему inference. Это не часть ядра `agent-memory-cpp`, но может быть реализовано через `IWriteGate`/`IAsyncIndexer`/`IQueryTransformer` contract'ы из `memory-stacks-roadmap.md` §13 (Runtime Services).

| Архитектура | Требуется | Статус в нашем стеке |
|---|---|---|
| **A-MEM link generation** | LLM-call для autonomous связывания | `IQueryTransformer` контракт запланирован |
| **A-MEM memory evolution** | LLM-call для обновления атрибутов существующих notes | Тот же hook |
| **СВИНОПАС anti-loop** | Cooldown queue + decay scorer на retrieval path | Уже есть `DecayPolicy` + cooldown factor; нужно явно включить для live-streaming workloads |
| **DLP-bridge ToM sidecar** | Optional IPC к external LLM для user-modeling | Не запланировано; кандидат на extension package |
| **Wiki-maintainer (Карпати)** | Background worker, переписывающий `CompiledArticle` units | CompactionWorker может быть расширен (`compaction-roadmap.md`) |
| **Contextual Retrieval** | Haiku-уровень LLM для обогащения chunks контекстом | Кандидат на `AsyncIndexer` / pre-indexing hook |
| **Docling / multimodal** | External PDF/DOCX/PPTX/XLSX/images/audio parser adapter | `IResourceStore` adapter pattern — готово |

### 7.3. Альтернативы вне нашего стека

| Архитектура | Почему альтернатива |
|---|---|
| **J-Space** | In-model emergent state. Не хранилище, а active workspace во время forward pass — не реализуемо на нашем substrate. [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Сравнение подходов организации памяти и контекста LLM.md] |
| **RLM** | Контекст в Python REPL с root LM, которая пишет код для исследования. Sub-LM вызовы + REPL execution — другой execution model. [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Сравнение подходов организации памяти и контекста LLM.md] |
| **Исполняемый граф контекста** | Runtime multi-node graph (AST + Git + AI-сессии + obligations + …) с N-мерной backlog surface — модель данных, не retrieval layer. [Source: `ai-agent-playbook/concepts/ai-agents/Исполняемый граф контекста - AST Git sessions logs obligations obligations.md`] |
| **Letta / MemGPT** | LM-managed page-in/page-out — отдельная модель memory, не совместимая с нашим scope-aware key model (LM должен «знать» про secondary indexes, MDBX layout). [Source: github.com/letta-ai/letta — Letta (formerly MemGPT)] <br>[Internal note: ai-agent-playbook/tools/ai-agents/Letta (бывш. MemGPT) — stateful agents с advanced memory.md] |
| **СВИНОПАС (full)** | Live-streaming ASR + Гном Гномыч fine-tuned extractor + кастомный датабазч — full proprietary stack. Часть (anti-loop, spreading activation) переиспользуема, часть — нет. [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/rag-knowledge/Внешняя память LLM-агентов — система СВИНОПАС.md] |

Эти архитектуры не конкуренты нашему стеку, а соседи по ландшафту. Они могут работать **поверх** нашего стека как разные уровни memory или orchestration layer'ы.

## §8. Open Questions

Что мы пока не знаем:

1. **Salience decay tuning.** Какие параметры decay rate + threshold дают лучший retrieval accuracy vs storage cost для нашей DBIs? (workload-зависимо, нужны benchmarks).
2. **A-MEM per-note evolution на MDBX.** Per-note evolution требует atomic updates существующих notes + regenerations of links. Как это ложится на нашу `MultiTableWriter` transaction и `envelope.revision` increments?
3. **Spreading activation depth.** Какой depth оптимален для разных workloads? lifemodel не публикует конкретные параметры [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/ai-agents/Dual-layer memory retrieval LanceDB и spreading activation graph.md].
4. **Cyrillic anti-loop.** СВИНОПАС специально использует лемматизацию/стемминг для русского + alias resolution. Нужно ли это для нашего стека на уровне archetype или на уровне per-stack (например, для Russian-language `BasicRag`)? [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/rag-knowledge/Внешняя память LLM-агентов — система СВИНОПАС.md]
5. **Bi-temporal validation.** Решение: расширять как M2+ AM-13 через отдельный
   `BiTemporalComponent` и range indexes; M1 `TemporalComponent` остаётся
   одноосевым. См. `guides/memory-lifecycle-governance-roadmap.md`.
6. **ToM sidecar vs IntentRouter.** Решение по boundary: ToM/user-modeling
   остаётся ADELIA/runtime sidecar; `agent-memory-cpp` хранит evidence-backed
   units, policies and retrieval traces. См.
   `guides/memory-lifecycle-governance-roadmap.md` §10.
7. **Embedding anisotropy problem.** NOUZ явно признаёт, что сырой cosine обманчив из-за анизотропии embeddings [Source: internal note — no public source available. Path: ai-agent-playbook/concepts/NOUZ — структурированная память для ИИ-агентов в Obsidian.md]. Нужна ли calibration layer для нашего dense retrieval? Mem0 обходит это через multi-signal scoring; возможно, мы автоматически получаем benefit из `HybridRetriever` fusion (см. §7.1: `HybridRetriever` — proposed API sketch; реальный тип — `HybridRetrievalEngine`).
8. **Anti-loop для не live-chat workloads.** Cooldown механизм СВИНОПАС разработан для voice-streamed chat. Как он работает для batch retrieval workloads (one-shot Q&A)? Возможно, нужен different decay curve.

## §9. References

Все source notes перечислены с абсолютными путями к playbook (для traceability) и кросс-ссылками на существующие `agent-memory-cpp/guides/`.

### 9.1. External (ai-agent-playbook)

**Memory hierarchies & 3-layer (Karpathy):**

- `ai-agent-playbook/concepts/ai-agents/Трёхслойная память для AI-агентов - метод Карпати.md` — 3-layer memory + 4-й слой Obsidian, wiki-maintainer, anti-мусор механика.
- `ai-agent-playbook/concepts/ai-agents/Уровни памяти AI-агента — от контекста до fine-tuning.md` (internal note — no public source available) — 7-tier hierarchy + 6.5 RLM + chasing_nlp classification.

**Schema-less & evolution:**

- **arXiv:2502.12110 — A-MEM paper (NeurIPS 2025).** Private provenance: `ai-agent-playbook/concepts/ai-agents/Zettelkasten-based agentic memory — A-MEM pattern.md` (internal note) — A-MEM pattern: atomic notes + LLM-driven linking + memory evolution.
- **arXiv:2502.12110 — A-MEM paper (NeurIPS 2025).** Private provenance: `ai-agent-playbook/resources/llm-research/A-MEM - Agentic Memory for LLM Agents (arXiv 2502.12110) - разбор статьи.md` (internal note) — A-MEM original paper (NeurIPS 2025).

**Structured / frontmatter / Obsidian-native:**

- `ai-agent-playbook/concepts/NOUZ — структурированная память для ИИ-агентов в Obsidian.md` (internal note — no public source available) — LUCA/PRIZMA/SLOI tiers, embedding anisotropy problem.
- `ai-agent-playbook/concepts/ai-agents/Self-Evolving Memory для Claude Code на основе LLM Knowledge Base.md` (internal note — no public source available) — Cole Medin + Karpathy compiler analogy + Compounding Knowledge Loop.

**Lightweight / flat:**

- `ai-agent-playbook/concepts/ai-agents/Блок фактов vs RAG-память.md` (internal note — no public source available) — Блок фактов (system prompt, no retrieval, ≤100 facts).

**Vector + graph overlay:**

- `ai-agent-playbook/concepts/ai-agents/Dual-layer memory retrieval LanceDB и spreading activation graph.md` (internal note — no public source available) — lifemodel dual-layer, salience decay, spreading activation, plugin-изоляция.

**Live-agents & anti-loop:**

- `ai-agent-playbook/concepts/rag-knowledge/Внешняя память LLM-агентов — система СВИНОПАС.md` (internal note — no public source available) — Гном Гномыч, граф сущностей, anti-loop формулы.

**User modelling / ToM:**

- `ai-agent-playbook/concepts/ai-agents/DLP-подобная архитектура профилирования для AI-агента.md` (internal note — no public source available) — ToM sidecar, evidence chain, ethical boundaries.

**Multi-node context graph:**

- `ai-agent-playbook/concepts/ai-agents/Исполняемый граф контекста - AST Git sessions logs obligations.md` (internal note — no public source available) — multi-dimensional backlog, N-D surface.

**Memory frameworks (comparison & docs):**

- `ai-agent-playbook/concepts/llm-research/Сравнение подходов организации памяти и контекста LLM.md` (internal note — no public source available) — Long context → Naive RAG → MemGPT → Mem0 → Zep → RLM → A-MEM спектр; J-Space как in-model layer.
- **github.com/mem0ai/mem0 — Mem0 open-source memory layer.** Private provenance: `ai-agent-playbook/tools/ai-agents/Mem0 — open-source memory layer для LLM-приложений (документация).md` (internal note) — Mem0 OSS components, two deployment modes (library / self-host).
- **github.com/letta-ai/letta — Letta (formerly MemGPT).** Private provenance: `ai-agent-playbook/tools/ai-agents/Letta (бывш. MemGPT) — stateful agents с advanced memory.md` (internal note) — Letta repository split (V1 legacy vs letta-code active), SDKs.
- **github.com/getzep/graphiti — Zep Graphiti temporal knowledge graph.** Private provenance: `ai-agent-playbook/tools/ai-agents/Zep Graphiti — temporal knowledge graph для AI-агентов (документация).md` (internal note) — Graphiti bi-temporal, 4 backends (Neo4j/FalkorDB/Neptune/Kuzu).

**Industry:**

- **arXiv:2512.12686 — Memoria paper.** Private provenance: `ai-agent-playbook/resources/llm-research/Memoria - Scalable Agentic Memory Framework - конспект.md` (internal note) — Memoria weighted-KG + session summarization.

### 9.2. In-house (agent-memory-cpp/guides/)

- [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) — манифест: ADR'ы, `MemoryProfileSpec`, `MemoryStack`, physical manifest ownership, M0/M1/M2 maturity, runtime services.
- [`knowledge-base-roadmap.md`](knowledge-base-roadmap.md) — envelope + components + projections + decay-aware scoring + retrieval contracts.
- [`lexical-search-roadmap.md`](lexical-search-roadmap.md) — BM25/BM25F, postings, projection-aware indexing, Cyrillic morphology gated.
- [`optimization-roadmap.md`](optimization-roadmap.md) — vector/binary/ANN optimization layer.
- [`policies-roadmap.md`](policies-roadmap.md) — DecayPolicy, WritePolicy, SpeakerScopePolicy.
- [`compaction-roadmap.md`](compaction-roadmap.md) — CompactionWorker, decay jobs.
- [`runtime-services-roadmap.md`](runtime-services-roadmap.md) — PromptCache, AsyncIndexer, WriteGate.
- [`memory-stacks-roadmap.md` §13 Runtime Services](memory-stacks-roadmap.md) — `IQueryTransformer`, `IRetrievalEvaluator` контракты для LLM-augmented retrieval.
- [`mdbx-containers-extension-tz.md` §5.5](mdbx-containers-extension-tz.md) — canonical graph_edges / embedding_meta DBI shapes.
- [`resource-reindexing.md`](resource-reindexing.md) — targeted reindexing protocol.
- [`research-reading-map.md`](research-reading-map.md) — research references backing the project.
