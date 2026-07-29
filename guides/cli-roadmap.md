# cli-roadmap.md

Спецификация `agent-memory-cli` — отдельного target для администрирования, инспекции, миграции и профилирования MemoryStack. CLI не входит в core library, поставляется как отдельный исполняемый файл. Документ конкретизирует ADR-014 из `guides/memory-stacks-roadmap.md` секции 14.

## 1. Purpose

Что описывает:

- Команды CLI, формат вывода, флаги, примеры использования.
- Exit codes и error reporting contract.
- Интеграция с MemoryStack API из `memory-stacks-roadmap.md` секция 7.
- Compaction subcommands (детали в `compaction-roadmap.md` секция 9).
- Runtime service subcommands (cache, indexer).
- Eval pipeline integration (см. `knowledge-base-roadmap.md` секция 9).

Cross-references:

- `guides/memory-stacks-roadmap.md` — MemoryStack API (секция 7), physical manifest ownership (секция 12), Maturity Levels (секция 13), Profile Migration (секция 14).
- `guides/compaction-roadmap.md` — Admin Operations (секция 9).
- `guides/runtime-services-roadmap.md` (future) — Observability / CLI integration (секция 8).
- `guides/knowledge-base-roadmap.md` — Eval pipeline (golden dataset, Recall@K, HybridLiftTarget).

## 2. Build Target

### 2.1. Отдельный executable

```cmake
# CMakeLists.txt (фрагмент)
add_executable(agent-memory-cli
    src/cli/main.cpp
    src/cli/inspect_cmd.cpp
    src/cli/stats_cmd.cpp
    src/cli/check_cmd.cpp
    src/cli/vacuum_cmd.cpp
    src/cli/reindex_cmd.cpp
    src/cli/profile_cmd.cpp
    src/cli/dump_cmd.cpp
    src/cli/eval_cmd.cpp
    src/cli/compaction_cmd.cpp
    src/cli/cache_cmd.cpp
    src/cli/indexer_cmd.cpp
)

target_link_libraries(agent-memory-cli PRIVATE agent-memory-core)
```

`agent-memory-cli` ссылается на `agent-memory-core` (статическая или shared library). Никаких дополнительных зависимостей сверх core.

### 2.2. Maturity Levels

CLI target привязан к Maturity Levels из `memory-stacks-roadmap.md` секция 13:

- **M0**: cli target создан, команды stub (return "not implemented" с exit code 1). Полезен для smoke-тестов CMake build pipeline.
- **M1**: базовые команды работают (inspect, stats, check, dump-unit, dump-components). Compaction subcommands в статусе stub.
- **M2**: полный набор команд (reindex, profile-migrate, run-eval, compaction/cache/indexer subcommands).

Ship-it критерии (M2 per `memory-stacks-roadmap.md` секция 13.3):

- CLI tool покрывает inspect/stats/check/vacuum/reindex/profile-info/profile-migrate.
- Eval pipeline (`run-eval`) запускается через CLI в CI.
- Compaction subcommands проходят integration тесты на golden dataset.

### 2.3. Зависимости

- `agent-memory-core` library (все API — MemoryStack, MemoryProfileSpec, CompactionWorker, PromptCache, AsyncIndexer).
- Стандартный C++17 + `<filesystem>`, `<chrono>`, `<iostream>`.
- Никаких внешних CLI-парсеров (handwritten argument parser) — минимум зависимостей.
- Никаких внешних форматтеров (handwritten JSON output для `--json` режима).

## 3. Command Structure

### 3.1. Global flags

```bash
agent-memory-cli [--path <path>] [--profile <profile_spec>] [--json] [--verbose] [--quiet] [--help] <command> [...]
```

Описание флагов:

- `--path <path>`: путь к MDBX environment. Default: `./agent_memory.mdbx`. Может быть абсолютным или относительным.
- `--profile <spec>`: путь к YAML/JSON spec или inline spec string (формат `name:<built-in>` или `path:/path/to/spec.yaml`). Default: `BasicRag` (если stack открывается с нуля) или профиль из `schema_info` (для existing stack).
- `--json`: вывод в JSON формате (для scriptability, CI/CD integration).
- `--verbose`: verbose logging в `stderr` (debug-level информация от MemoryStack).
- `--quiet`: только exit code + minimal output (только ошибки или critical info). Для CI/CD пайплайнов.
- `--help`: показать help (auto-generated per command).

Порядок: global flags должны идти до subcommand. Subcommand-specific flags идут после subcommand.

### 3.2. Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | General error |
| 2 | Invalid arguments (parser error, missing required flag) |
| 3 | Path not found / not accessible |
| 4 | Profile validation failed |
| 5 | Schema mismatch (migration required) |
| 10 | Compaction error |
| 11 | Eval error |
| 20 | I/O error |

Exit codes стабильный contract: используются в CI/CD скриптах для branching logic.

## 4. Commands

### 4.1. inspect

Назначение: показать общую информацию о MemoryStack (path, profile, capabilities, schema versions, statistics).

```bash
agent-memory-cli inspect [--path <path>] [--profile <spec>]
```

Пример вывода:

```
MemoryStack: /data/agent_memory.mdbx
Profile: agent_ltm (MemoryProfiles::AgentLongTermMemory)
Profile signature: a3f5b9c2e1d4f6a8...

Capabilities:
  LexicalBm25F:        yes
  DenseVectors:        yes
  QAPairs:             no
  TemporalValidity:    yes
  UsageStats:          yes
  Decay:               yes
  SpeakerAttribution:  no
  Compaction:          yes
  PromptCache:         opt
  GraphRelations:      yes
  EmbeddingMigration:  yes
  CompiledArticles:    no
  ConversationMemory:  no

Schema:
  Envelope version:    1
  Component versions:  UsageStats=1, Speaker=0, Temporal=1, EmbeddingMeta=1, CompactionMeta=1
  Resource generation: 42

Statistics:
  Total units:         128,539
  Active:              124,102
  Superseded:          2,890
  Deprecated:          300
  Erased:              0
  Usage cooldown:      1,247
  Components:          128,539 envelopes + 128,539 UsageStats + 89,234 Temporal
  Projections:         342,118 (Original 128,539, QAQuestion 45,221, QAAnswer 40,901, Summary 127,457)
  Embeddings:          128,539 (1 model, 1 projection)
  Compaction jobs:     3 active, 142 completed, 0 failed
  DBI count:           22 of 96 (expanded peak 58, reserved headroom 38)
  MDBX size:           487 MB
```

JSON вариант (через `--json`):

```json
{
  "path": "/data/agent_memory.mdbx",
  "profile": "agent_ltm",
  "profile_signature": "a3f5b9c2e1d4f6a8...",
  "capabilities": {
    "lexical_bm25f": true,
    "dense_vectors": true,
    "qa_pairs": false,
    "temporal_validity": true,
    "usage_stats": true,
    "decay": true,
    "speaker_attribution": false,
    "compaction": true,
    "prompt_cache": "opt",
    "graph_relations": true,
    "embedding_migration": true,
    "compiled_articles": false,
    "conversation_memory": false
  },
  "schema": {
    "envelope_version": 1,
    "component_versions": {
      "usage_stats": 1,
      "speaker": 0,
      "temporal": 1,
      "embedding_meta": 1,
      "compaction_meta": 1
    },
    "resource_generation": 42
  },
  "stats": {
    "total_units": 128539,
    "lifecycle": {
      "active": 124102,
      "superseded": 2890,
      "deprecated": 300,
      "erased": 0
    },
    "usage": {
      "cooldown": 1247
    },
    "components": {
      "envelopes": 128539,
      "usage_stats": 128539,
      "temporal": 89234
    },
    "projections": {
      "total": 342118,
      "by_kind": {
        "original": 128539,
        "qa_question": 45221,
        "qa_answer": 40901,
        "summary": 127457
      }
    },
    "embeddings": {
      "total": 128539,
      "model_count": 1,
      "projection_count": 1
    },
    "compaction": {
      "active_jobs": 3,
      "completed_jobs": 142,
      "failed_jobs": 0
    },
    "dbi_count": 22,
    "dbi_max": 96,
    "dbi_expanded_peak": 58,
    "dbi_reserved_headroom": 38,
    "dbi_minimum_free_slots": 16,
    "mdbx_size_bytes": 510707712
  }
}
```

Реализация: открывает MemoryStack, читает `schema_info` DBI, сканирует `knowledge_units` для статистики lifecycle (с sampling для больших стеков), вызывает `stack.stats()`.

### 4.2. stats

Назначение: показать детальные runtime метрики (latency, cache hit rate, retrieval stats, compaction).

```bash
agent-memory-cli stats [--path <path>] [--period <duration>]
```

Параметры:

- `--period <duration>`: окно наблюдения в формате `1h`, `24h`, `7d`, `30m`. Default: `1h`.

Пример:

```
Period: last 1 hour
Retrievals:        1,247
Avg latency:       32 ms
p50:               18 ms
p95:               89 ms
p99:               245 ms
Cache hits:        847 (67.9%)
Cache misses:      400
AsyncIndexer:      0 pending, 1,180 processed (avg batch 47)
Compaction jobs:   3 active (Decay=2, Dedupe=1), 142 completed
Anti-loop skips:   234 (18.8% of retrievals)
```

Реализация: читает `RetrievalTrace` aggregates, `PromptCache` stats, `AsyncIndexer` queue depth, `CompactionWorker` stats. Все из существующих runtime counters (lock-free atomic в core).

### 4.3. check

Назначение: проверить целостность MDBX environment, schema consistency, profile drift.

```bash
agent-memory-cli check [--path <path>] [--repair] [--deep]
```

Параметры:

- `--repair`: пытается исправить мелкие inconsistencies (rebuild secondary indexes, удалить orphan keys).
- `--deep`: полная проверка (deep scan всех unit'ов, проверка projections, валидация indexes).

Что проверяется:

- MDBX integrity (env open, no corruption через `mdbx_env_stat`).
- Schema versions совпадают с profile (через `schema_info` DBI).
- Profile signature matches (drift detection).
- DBI consistency (нет orphan keys в secondary indexes).
- Components и projections exist для всех units (только при `--deep`).
- Indexes consistent (postings correspond to units).

Пример:

```
Checking /data/agent_memory.mdbx...
MDBX env: OK
Schema versions: OK
Profile signature: OK (matches agent_ltm)
DBI count: 22 (within configured 96 limit; expanded peak 58)
Orphan components: 0
Orphan projections: 0
Index consistency: OK
Deep check completed in 12.4 sec
All checks passed.
```

С `--repair`:

```
Checking /data/agent_memory.mdbx...
MDBX env: OK
Schema versions: OK
Profile signature: OK (matches agent_ltm)
DBI count: 22 (within configured 96 limit; expanded peak 58)
Orphan components: 3 found
  Rebuilding metadata_filters...
  Rebuilt 3 entries.
Orphan projections: 0
Index consistency: OK
All checks passed after repair.
```

Exit codes: `0` если всё OK, `5` если schema mismatch, `1` для других ошибок.

### 4.4. vacuum

Назначение: compact MDBX environment, удалить Erased units, освободить место.

```bash
agent-memory-cli vacuum [--path <path>] [--aggressive]
```

Параметры:

- `--aggressive`: расширенный режим (также архивирует units in usage cooldown > 30 days и удаляет Superseded > 90 days).

Примеры:

Default mode (только Erased):

```
Before: 487 MB
After:  487 MB (0.0% reduction)
Erased units removed: 0
Time: 1.2 sec
```

Aggressive mode:

```
Before: 487 MB
After:  412 MB (15.4% reduction)
Erased units removed: 0
Usage cooldown archived: 1,247
Superseded expired: 1,820
Time: 4.2 sec
```

Реализация: использует `ArchiveColdJob` из `compaction-roadmap.md` секция 4.4 с `physical_erase = true` для aggressive mode.

### 4.5. reindex

Назначение: перестроить secondary indexes (BM25F postings, dense vectors, graph edges, temporal indexes).

```bash
agent-memory-cli reindex [--path <path>] [--index <name>] [--model <id>] [--scope <scope_id>]
```

Параметры:

- `--index <name>`: имя индекса для перестройки (см. ниже). Default: `all`.
- `--model <id>`: target embedding model id (для dense index). Default: текущая модель из `embedding_meta`.
- `--scope <scope_id>`: ограничить перестройку одним scope. Default: все scopes.

Index names:

- `bm25`: перестроить `inverted_token_to_unit`, `field_to_postings`.
- `dense`: перестроить `embedding_vectors` (используя указанный `--model`).
- `graph`: перестроить `graph_edges_by_src`, `graph_edges_by_dst`.
- `temporal`: перестроить `temporal_unit_index`.
- `metadata`: перестроить `metadata_filters`.
- `all`: все indexes (default).

Пример:

```bash
agent-memory-cli reindex --index bm25
```

Вывод:

```
Rebuilding bm25 index...
Scanned: 128,539 units
Tokens: 2,847,392
Postings: 18,472,901
Time: 47.2 sec
Done.
```

Реализация: для каждого index — открывает существующий DBI, очищает, переиндексирует из primary DBI через scan. Не модифицирует `knowledge_units`.

### 4.6. profile-info

Назначение: показать полную спецификацию профиля (capabilities, policies, defaults).

```bash
agent-memory-cli profile-info [--profile <spec>]
```

Если `--profile` не указан — показывает профиль из `schema_info` текущего стека.

Пример:

```
Profile: agent_ltm (MemoryProfiles::AgentLongTermMemory)
Capabilities:
  LexicalBm25F:        yes
  DenseVectors:        yes
  GraphRelations:      yes
  UsageStats:          yes
  TemporalValidity:    yes
  FactPayload:         yes
  EmbeddingMeta:       yes
  Compaction:          yes
  Decay:               yes (via UsageStats)

DecayPolicy:
  mode:                       Exponential
  half_life_ms:               604,800,000 (7 days)
  use_boost:                  0.35
  cooldown_ms:                60,000 (60 sec)
  self_echo_suppression:      0.3
  cold_threshold:             0.01

WritePolicy:
  trigger:                    OnTimer
  flush_interval_ms:          30,000 (30 sec)
  importance_threshold:       0.4
  dedupe_distance_threshold:  0.15
  allow_supersede:            true
  allow_merge:                true
  allow_episode_compaction:   true

HybridRetrievalConfig:
  fusion:                     RRF
  rrf_k_constant:             60.0
  retriever_weights:          [1.0, 1.0, 1.5, 0.5, 1.0]
  candidate_pool_size:        200

ContextBudget:
  total:                      4,096 tokens
  qa:                         512
  chunk:                      2,048
  graph:                      512
  summary:                    512
  evidence:                   256

Schema:
  envelope_schema_version:    1
  component_versions:         {UsageStats: 1, Temporal: 1, EmbeddingMeta: 1, CompactionMeta: 1}
```

Реализация: открывает MemoryStack (или читает spec file), выводит `MemoryProfileSpec` через formatted output.

### 4.7. profile-migrate

Назначение: мигрировать данные из одного профиля в другой (major migration через новую БД).

```bash
agent-memory-cli profile-migrate \
    --source <old.mdbx> \
    --target <new.mdbx> \
    --target-profile <spec> \
    [--validate] \
    [--batch-size N]
```

Параметры:

- `--source <old.mdbx>`: путь к исходному MDBX environment.
- `--target <new.mdbx>`: путь к новому MDBX environment (создаётся).
- `--target-profile <spec>`: spec для target профиля (built-in имя или path).
- `--validate`: запускает golden dataset после migration, откатывает если HybridLiftTarget не достигнут.
- `--batch-size N`: размер батча для migration (default: 1000).

Алгоритм:

1. Открыть source stack с source profile (auto-detect через `schema_info`).
2. Открыть target stack с target profile (создаёт новый env).
3. Scan all units в source через `source.units().scan_all()`.
4. Для каждого unit: derive components (capability-aware), generate default projections (Original, QAQuestion/QAAnswer/Summary per kind), create in target через `target.create_or_get_unit(CreateUnitRequest{...})`.
5. Verify golden dataset на target (если `--validate`).
6. Atomic swap (rename файлов) при `--auto-swap`.

Пример:

```bash
agent-memory-cli profile-migrate \
    --source /data/old.mdbx \
    --target /data/new.mdbx \
    --target-profile agent_ltm \
    --validate
```

Вывод:

```
Migrating /data/old.mdbx (basic_rag) → /data/new.mdbx (agent_ltm)...
Source units: 45,221
Migrated:     45,221 (100%)
Failed:       0
Time: 287 sec
Target size: 612 MB
Validation: passed (Recall@10 on golden = 0.847, baseline = 0.692, lift = 1.22x)
Done.
```

С провалом validation:

```
Migrating /data/old.mdbx (basic_rag) → /data/new.mdbx (agent_ltm)...
Source units: 45,221
Migrated:     45,221 (100%)
Failed:       0
Time: 287 sec
Target size: 612 MB
Validation: FAILED (Recall@10 on golden = 0.682, baseline = 0.692, lift = 0.99x)
Target HybridLiftTarget: 1.20x — NOT MET
Rolling back (removing /data/new.mdbx)...
Exit code: 1
```

См. `memory-stacks-roadmap.md` секция 14.2 для conceptual алгоритма. Детальная реализация — на базе `MemoryStack::scan_all()` + `create_or_get_unit()` в loop.

### 4.8. dump-unit / dump-components

Назначение: показать содержимое unit / components (для debug и inspection).

```bash
agent-memory-cli dump-unit --id <unit_id> [--path <path>]
agent-memory-cli dump-components --id <unit_id> [--path <path>]
```

Параметры:

- `--id <unit_id>`: id unit'а (формат `kind:scope:hash` или просто `hash`).
- `--path <path>`: путь к MDBX environment. Default: `./agent_memory.mdbx`.

Пример:

```bash
agent-memory-cli dump-unit --id qa:user:yaroslav:abc123def456...
```

Вывод:

```
Unit: qa:user:yaroslav:abc123def456...

Envelope:
  kind:                QAPair
  scope_id:            user:yaroslav
  primary_text:        "What is the capital of France?"
  display_text:        "Q: What is the capital of France?\nA: Paris"
  lifecycle_state:     Active
  priority_weight:     1.0
  sources:             1 SourceRef
  created_at_ms:       1700000000000
  updated_at_ms:       1700000050000

Components:
  QAPayload:           question_variants=[...], answer="Paris", category="geography"
  UsageStats:          use_count=12, last_used_at_ms=1700100000000, cooldown_until_ms=0
  Temporal:            valid_from_ms=1700000000000, valid_until_ms=0

Projections:
  Original (rev 1)
  QAQuestion (rev 1)
  QAAnswer (rev 1)
```

`dump-components` — показывает только components без envelope/projections (compact output).

### 4.9. run-eval

Назначение: запустить evaluation pipeline на golden dataset.

```bash
agent-memory-cli run-eval \
    --path <path> \
    --dataset <path> \
    [--report <output>] \
    [--intent <intent_class>] \
    [--threshold <hybrid_lift>]
```

Параметры:

- `--path <path>`: путь к MDBX environment для evaluation.
- `--dataset <path>`: путь к golden dataset (YAML формат).
- `--report <output>`: путь к output report (HTML или JSON).
- `--intent <intent_class>`: запустить только для указанного intent class (QALookup, FactLookup, GraphLookup, NoAnswer, TemporalValidityLookup, SupersedenceChain, CooldownRespect, SpeakerFilter, CompactionHandoff, CrossDomainCoverage, ProcedureActivation, SameEventDifferentPerspectives, KnowledgeAtSequence, CausalWhy, DecisionAlternatives, ProcedureDegradation, PartitionAppendMerge, PartitionConflictPreserved, PerspectiveLeakage, EvidenceDrillDown, DerivedErasePropagation, ReplayDeterminism).
- `--threshold <hybrid_lift>`: target HybridLift ratio (default 1.20).

Пример:

```bash
agent-memory-cli run-eval \
    --path test.mdbx \
    --dataset tests/golden.yaml \
    --report eval_report.html
```

Вывод:

```
Running eval on golden_dataset.yaml...
Total cases: 50
Passed:      47
Failed:      3

Metrics:
  Recall@1:  0.78
  Recall@5:  0.92
  Recall@10: 0.96
  MRR:       0.847
  NDCG@10:   0.879
  ContextPrecision: 0.823
  NoAnswerAccuracy: 0.94
  CitationFidelity: 0.91
  Latency p50:  18 ms
  Latency p95:  89 ms
  Latency p99:  245 ms

Hybrid lift: Recall@10(hybrid) = 0.96, BM25-only = 0.81, ratio = 1.19x
Target: 1.20x — FAILED by 0.01

Per intent:
  QALookup:    18 cases, Recall@10 = 1.00
  FactLookup:  15 cases, Recall@10 = 0.93
  GraphLookup: 12 cases, Recall@10 = 0.92
  NoAnswer:    5 cases, accuracy = 0.94

Report written to /data/eval_report.html
```

Exit codes:

- `0` если все metrics в пределах target (включая HybridLiftTarget).
- `11` если metrics не достигают target.
- `1` если dataset невалиден или path недоступен.

См. `knowledge-base-roadmap.md` секция 9 для деталей по golden dataset формату и метрикам.

### 4.10. compaction subcommands

Compaction admin operations (детали в `compaction-roadmap.md` секция 9.1).

```bash
agent-memory-cli compaction status
agent-memory-cli compaction enqueue <kind> [--scope <scope_id>] [--params <json>]
agent-memory-cli compaction list [--limit N] [--status <pending|running|done|dead|cancelled>]
agent-memory-cli compaction cancel <job_id>
agent-memory-cli compaction retry <job_id>
agent-memory-cli compaction stats
agent-memory-cli compaction handoff list
agent-memory-cli compaction handoff inspect <job_id>
```

Описание:

- `compaction status`: показывает CompactionWorker stats (active, queued, completed, failed) + current handoff (если есть).
- `compaction enqueue <kind>`: enqueue ручного job (kind = Decay, Dedupe, Merge, ArchiveCold, SummaryPromotion, EmbeddingRecompute). `--params` — JSON с kind-specific params.
- `compaction list`: показывает последние N job states.
- `compaction cancel <job_id>`: cancel pending job (не running).
- `compaction retry <job_id>`: re-enqueue Dead job.
- `compaction stats`: snapshot `CompactionStats`.
- `compaction handoff list`: список handoff records.
- `compaction handoff inspect <job_id>`: полная dump `CompactionHandoff` (goal, plan_steps, constraints, progress).

Пример:

```bash
agent-memory-cli compaction enqueue Decay --scope user:yaroslav --params '{"min_score_threshold":0.05}'
```

Вывод:

```
Enqueued DecayJob: job_id=0192f3a4-...
Scope: user:yaroslav
Params: {"min_score_threshold":0.05, "batch_size":1000}
```

Exit codes: `10` для compaction errors.

### 4.11. cache subcommands

PromptCache admin operations (см. `runtime-services-roadmap.md` секция 8 future).

```bash
agent-memory-cli cache stats
agent-memory-cli cache clear [--scope <scope_id>]
agent-memory-cli cache invalidate --key <key>
```

Описание:

- `cache stats`: показывает PromptCache hit/miss counters, size, eviction rate.
- `cache clear [--scope]`: очищает весь cache или для указанного scope.
- `cache invalidate --key <key>`: удаляет конкретную запись.

### 4.12. indexer subcommands

AsyncIndexer admin operations (см. `runtime-services-roadmap.md` секция 8 future).

```bash
agent-memory-cli indexer status
agent-memory-cli indexer flush
agent-memory-cli indexer clear [--scope <scope_id>]
```

Описание:

- `indexer status`: показывает pending count, processed count, avg batch size, last flush.
- `indexer flush`: форсирует flush всех pending batches.
- `indexer clear [--scope]`: очищает indexer queue (все или для scope).

## 5. Output Formats

### 5.1. Human-readable (default)

Форматированный текст с секциями, таблицами, выравниванием. Без emoji (per project conventions).

Используется по умолчанию. Читается интерактивно через терминал.

### 5.2. JSON (--json)

Все команды поддерживают `--json` для scriptability. Формат стабильный, документирован через JSON Schema (отдельный файл `docs/cli-json-schema.json`).

Используется для CI/CD, парсинга в скриптах, интеграции с мониторингом.

### 5.3. Quiet mode (--quiet)

Только exit code + minimal output (только ошибки). Для CI/CD пайплайнов где output не нужен.

Example:

```bash
agent-memory-cli check --path data.mdbx --quiet
echo "Exit code: $?"  # 0 if OK
```

## 6. Examples

### 6.1. Daily maintenance

```bash
# Inspect current state
agent-memory-cli inspect

# Check integrity
agent-memory-cli check --deep

# Vacuum (если есть Erased)
agent-memory-cli vacuum

# Stats
agent-memory-cli stats --period 24h
```

### 6.2. Migration

```bash
# Backup
cp old.mdbx old.mdbx.bak

# Migrate
agent-memory-cli profile-migrate \
  --source old.mdbx \
  --target new.mdbx \
  --target-profile agent_ltm \
  --validate

# Swap
mv old.mdbx old.mdbx.deprecated
mv new.mdbx old.mdbx
```

### 6.3. Eval CI

```bash
# Run on PR
agent-memory-cli run-eval \
  --path test.mdbx \
  --dataset tests/golden.yaml \
  --report eval_report.html \
  --json > eval_results.json

# Check threshold
if [ $(jq '.hybrid_lift_ratio' eval_results.json) \< 1.20 ]; then
  echo "Hybrid lift below target"
  exit 1
fi
```

### 6.4. Compaction management

```bash
# Check status
agent-memory-cli compaction status

# Manual decay sweep
agent-memory-cli compaction enqueue Decay --scope user:yaroslav

# Inspect in-progress handoff
agent-memory-cli compaction handoff inspect session-1234

# Retry failed job
agent-memory-cli compaction retry 0192f3a4-abcd-...
```

### 6.5. Index rebuild after schema change

```bash
# Rebuild bm25 index after tokenization change
agent-memory-cli reindex --index bm25

# Rebuild dense vectors with new model
agent-memory-cli reindex --index dense --model text-embedding-3-large
```

## 7. Implementation Notes

### 7.1. Аргумент-парсинг

Handwritten простой парсер (без boost::program_options, без CLI11). Поддержка:

- `--flag value` (separated)
- `--flag=value` (joined)
- `--bool-flag` (presence = true)
- `--bool-flag true|false` (explicit value)
- Позиционные аргументы (после global flags, до subcommand flags)
- `--help` (auto-generated per command через reflection или hardcoded)

Парсер — в `src/cli/arg_parser.{h,cpp}`. Один файл, ~300 строк.

### 7.2. Error reporting

Все ошибки через `std::cerr` с structured format:

```
Error: <category>: <message>
Hint: <suggestion>
```

Где `<category>` = `ArgumentError`, `PathError`, `ProfileError`, `SchemaError`, `CompactionError`, `EvalError`, `IOError`. Exit code соответствует категории (см. секция 3.2).

В `--json` режиме ошибки сериализуются как:

```json
{
  "error": true,
  "category": "SchemaError",
  "message": "...",
  "exit_code": 5,
  "hint": "..."
}
```

### 7.3. Threading

CLI — single-threaded. Открывает MemoryStack через `MemoryStack::open(path, spec)`, выполняет команду, закрывает через RAII. Никакой background processing в CLI процессе.

Исключение: `compaction enqueue` создаёт job, worker (внутри MemoryStack) может начать processing в background thread. Но CLI процесс не дожидается завершения — возвращает job_id немедленно.

### 7.4. Profile parsing

`--profile <spec>` принимает три формы:

1. Built-in name: `basic_rag`, `qa_kb`, `agent_ltm`, `speaker_chat`, `compiled_wiki`, `temporal_facts`, `full_research`.
2. Path to YAML/JSON file: `/path/to/spec.yaml`.
3. Inline JSON: `{"name":"custom","enable_lexical_bm25f":true,...}`.

Parser определяет форму по prefix или содержимому (file existence check).

### 7.5. Logging

`--verbose` включает debug-level logging в `stderr` от MemoryStack, CompactionWorker, retrieval layer.

`--quiet` подавляет всё, кроме ошибок.

Default — только warnings и errors в `stderr`.

## 8. Implementation Order

Per `memory-stacks-roadmap.md` секция 16:

| Шаг | Что | Maturity |
|---|---|---|
| 16.0 | CLI target skeleton (main.cpp, arg parser, global flags, help system) | M0 |
| 16.1 | inspect, stats, dump-unit, dump-components | M1 |
| 16.2 | check, vacuum | M1 |
| 16.3 | reindex (bm25, metadata) | M1 |
| 16.4 | profile-info, profile-migrate | M2 |
| 16.5 | run-eval | M2 |
| 16.6 | compaction subcommands | M2 |
| 16.7 | reindex (dense, graph, temporal) | M2 |
| 16.8 | cache, indexer subcommands | M2 |
| 16.9 | --json support для всех commands | M2 |

### 8.1. Step 16.0 details

- CMake target `agent-memory-cli`.
- `main.cpp`: parse global flags, dispatch to command handler.
- `arg_parser.{h,cpp}`: handwritten parser.
- Help system: per-command help text в hardcoded strings (не auto-generated).
- Exit codes centralized в `exit_codes.h`.

### 8.2. Step 16.1 details

- `inspect_cmd.cpp`: open stack, read schema_info, scan units для lifecycle stats.
- `stats_cmd.cpp`: read runtime counters (lock-free atomic).
- `dump_cmd.cpp`: read unit by id, format envelope + components + projections.

### 8.3. Step 16.2 details

- `check_cmd.cpp`: MDBX env stat, schema validation, profile signature check, optional deep scan.
- `vacuum_cmd.cpp`: trigger ArchiveColdJob с physical_erase.

### 8.4. Step 16.3 details

- `reindex_cmd.cpp`: clear + rebuild secondary indexes (bm25 first, metadata simple).

### 8.5. Step 16.4 details

- `profile_cmd.cpp`: format MemoryProfileSpec, read existing stack spec.
- Migration logic: scan source + write to target с capability-aware component derivation.

### 8.6. Step 16.5 details

- `eval_cmd.cpp`: load golden dataset, run cases, compute metrics, generate report.
- HTML report generation: simple template (без external template engine).

## 9. References

Internal documents:

- `guides/memory-stacks-roadmap.md` — секция 7 (MemoryStack API), 12 (physical manifest ownership), 13 (Maturity Levels), 14 (Profile Migration Strategy), 16 (Implementation Order, шаг 16); ADR-014 (CLI как отдельный target).
- `guides/compaction-roadmap.md` — секция 9 (Admin Operations через CLI).
- `guides/knowledge-base-roadmap.md` — eval pipeline (golden dataset, metrics, HybridLiftTarget).
- `guides/runtime-services-roadmap.md` (future) — секция 8 (Observability / CLI integration для PromptCache, AsyncIndexer).
- `guides/mdbx-containers-extension-tz.md` — TypeDiscriminatedTable, MultiTableWriter для profile-migrate.

External references (ai-agent-playbook):

- `tools/rag/OpenWiki — CLI documentation for agents.md` — TUI pattern reference.
- `tools/rag/Graphify — инструмент GraphRAG.md` — CLI для graph indexing.
