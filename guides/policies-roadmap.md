# policies-roadmap.md

Спецификация политик retrieval/write/decay/speaker для MemoryStack. Документ конкретизирует политики из `guides/memory-stacks-roadmap.md` секции 6.2 с детальными диапазонами, defaults per stack, валидацией и примерами.

> Decay formula — canonical, согласован с memory-stacks-roadmap.md §16 шаг 10. Cooldown и self_echo suppression — отдельные post-filters, не часть decay. dedupe_distance_threshold — cosine distance, lower = stricter dedup.

## 1. Purpose

- Что описывает: DecayPolicy (anti-loop, забывание, soft-suppression), WritePolicy (trigger, dedupe, importance), SpeakerScopePolicy (multi-user фильтрация), RetrievalMode (associative/targeted/hybrid), HybridRetrievalConfig (RRF/weighted/learned).
- Cross-references: memory-stacks-roadmap.md (ADR-008, ADR-009), knowledge-units-roadmap.md (durable lifecycle), knowledge-base-roadmap.md (DecayAwareRetriever, AntiLoopCooldown).

Sensitive affective inferences add policy requirements beyond generic
decay/write rules: provenance, confidence, retention, personalization consent,
and model-training opt-out. These are tracked in
[`affective-memory-roadmap.md`](affective-memory-roadmap.md) ADR-A07 and should
not be folded into `priority_weight` or ordinary usage statistics.

## 1.1. EncryptionPolicy (planned)

`EncryptionPolicy` is an opt-in storage/security policy for local memory stacks.
It is especially relevant for affective memory, private conversation history,
and persisted summaries, but it is not mandatory for every use of
`agent-memory-cpp`.

The policy should describe authenticated encryption-at-rest without binding the
whole project to one implementation too early:

```cpp
enum class KeyProviderKind : uint8_t {
    None,
    MachineLocal,
    UserPassword,
    ExternalKms
};

enum class EncryptionScope : uint8_t {
    Disabled,
    ArtifactsOnly,
    SensitivePayloadValues,
    AllValuePayloads,
    ExternalVolumeEncryption
};

struct EncryptionPolicy {
    EncryptionScope scope = EncryptionScope::Disabled;
    KeyProviderKind key_provider = KeyProviderKind::None;
    std::string aead_scheme = "aes-256-gcm";
    std::string kdf_scheme = "argon2id";
    std::string key_id;
    uint32_t key_version = 0;
    bool require_authenticated_metadata = true;
};

struct EncryptedRecordHeader {
    std::string scheme_id;
    std::string key_id;
    uint32_t key_version = 0;
    ByteArray nonce;
    ByteArray salt;
    ByteArray authentication_tag;
    uint32_t plaintext_size = 0;
    uint32_t schema_version = 0;
};
```

Guidance:

- use AEAD encryption so corrupted or tampered ciphertext is rejected;
- keep key derivation and key storage outside retrieval/indexing code;
- record scheme and key identity in profile metadata for future rotation;
- for AES-GCM, never reuse a nonce with the same key; use a reliable CSPRNG
  96-bit nonce or a proven unique counter/record-identity construction;
- bind ciphertext to its record through Additional Authenticated Data such as
  `scope_id`, `unit_id`, component kind, artifact type, and schema version;
- verify the authentication tag before deserializing plaintext;
- allow several `key_version` values during key rotation;
- use temp-file plus atomic replace for encrypted file-backed artifacts;
- distinguish atomic visibility from durable persistence: durable file-backed
  artifacts normally need temp-file write, file flush, atomic replace, and
  parent-directory flush where the platform exposes it;
- rely on MDBX transactions for database durability, while optionally
  encrypting value payloads before write;
- document which indexes and metadata remain plaintext for each scope;
- do not treat encryption as a substitute for retention, erasure, or consent.

Threat model:

| Threat | Expected protection |
|---|---|
| Copied encrypted artifact or DB value without key material | Protected by AEAD if nonce/key rules hold |
| Copied whole user profile including ordinary files | Protected only if the key is in an OS-backed vault or external provider |
| Code already running as the user | Not protected by local encryption alone |
| Root/admin compromise | Out of scope for local software-only encryption |

Machine-local keys should mean OS-backed storage such as Windows DPAPI, macOS
Keychain, Linux Secret Service/keyring, TPM-backed keys, or an equivalent
credential vault. A secret file stored next to the database is not sufficient
for the "whole profile copied" threat.

Encrypted values are not the same as an encrypted database. DBI names, key
ordering, record counts, payload sizes, timestamps, update frequency, access
patterns, and some secondary indexes may remain visible unless the deployment
uses external volume/database encryption or redesigns the index layout.

Password-derived keys should use Argon2id as the preferred KDF, or scrypt where
Argon2id is unavailable. KDF parameters (`memory_cost`, `time_cost`,
`parallelism`, `salt`, and `kdf_version`) are part of the encrypted record or
profile metadata. Plain `SHA-256(password)` is not an acceptable password KDF.

Validation matrix:

| Scope / provider combination | Validity |
|---|---|
| `EncryptionScope::Disabled` | requires `KeyProviderKind::None` |
| `ArtifactsOnly`, `SensitivePayloadValues`, `AllValuePayloads` | require `key_provider != None` |
| `KeyProviderKind::UserPassword` | requires a versioned password KDF and salt |
| `MachineLocal` or `ExternalKms` | must not require a password KDF for normal operation |
| `ExternalVolumeEncryption` | requires `KeyProviderKind::None`; library does not create `EncryptedRecordHeader`; deployment owns encryption and keys |
| any encrypted payload scope with `KeyProviderKind::None` | invalid |

## 1.2. Crypto references

- RFC 5116 — Authenticated Encryption with Associated Data (AEAD):
  https://datatracker.ietf.org/doc/html/rfc5116
- NIST SP 800-38D — GCM and GMAC:
  https://csrc.nist.gov/pubs/sp/800/38/d/final
- RFC 9106 — Argon2 memory-hard function:
  https://datatracker.ietf.org/doc/rfc9106/
- RFC 7914 — scrypt password-based key derivation:
  https://datatracker.ietf.org/doc/html/rfc7914
- RFC 8452 — AES-GCM-SIV nonce-misuse-resistant AEAD, useful if nonce
  uniqueness cannot be guaranteed by design:
  https://datatracker.ietf.org/doc/html/rfc8452

## 2. DecayPolicy

### 2.1. DecayMode

```cpp
enum class DecayMode { None, Linear, Exponential, Logistic };
```

### 2.2. Параметры

| Поле | Тип | Диапазон | Default | Описание |
|---|---|---|---|---|
| mode | DecayMode | None/Linear/Exponential/Logistic | None | тип затухания |
| half_life_ms | double | [0, infinity) | 0 | время half-decay (если mode != None) |
| use_boost | double | [0, 10] | 0 | log1p(use_count) * use_boost |
| cooldown_ms | double | [0, infinity) | 0 | minimum time between re-retrievals |
| self_echo_suppression | double | [0, 1] | 0 | factor для подсвинка (own-speech) |
| cold_threshold | double | [0, 1] | 0 | below this score → archive candidate |
| cooldown_factor | double | [0, 1] | 0.1 | multiplier when in cooldown (default 0.1) |

### 2.3. Формулы scoring

Decay и boost разделены на две независимые компоненты:

- `decay_factor` затухает экспоненциально (по `half_life_ms`) — старые facts становятся менее релевантными.
- `use_boost` применяется через `log1p(use_count)` — `use_count` НЕ затухает (часто используемое остаётся частым).
- Cooldown и self_echo suppression — отдельные мультипликативные post-filters (см. §2.4), не часть decay.

**Canonical apply_decay_and_boost (Exponential — default mode):**

```cpp
// decay_factor (экспоненциально): exp(-elapsed_ms / half_life_ms)
// score = base_score * decay_factor + use_boost * log1p(use_count)

double decay_factor(uint64_t elapsed_ms, double half_life_ms) {
    if (half_life_ms <= 0) return 1.0;
    return std::exp(-double(elapsed_ms) / half_life_ms);
}

double apply_decay_and_boost(
    double base_score,
    const UsageStatsComponent& usage,
    const DecayPolicy& policy,
    uint64_t now_ms) {

    if (policy.mode == DecayMode::None) {
        return base_score + policy.use_boost * std::log1p(double(usage.use_count));
    }
    uint64_t elapsed = (now_ms >= usage.last_used_at_ms)
        ? (now_ms - usage.last_used_at_ms) : 0;
    double factor = decay_factor(elapsed, policy.half_life_ms);
    return base_score * factor + policy.use_boost * std::log1p(double(usage.use_count));
}
```

**Упрощённые формулы по режимам (применяются через `apply_decay_and_boost`):**

```cpp
// Exponential (canonical default):
score = base_score * exp(-elapsed_ms / half_life_ms)
      + use_boost * log1p(use_count)

// Linear:
score = base_score * max(0, 1 - elapsed_ms / (2 * half_life_ms))
      + use_boost * log1p(use_count)

// Logistic:
score = base_score / (1 + exp((elapsed_ms - half_life_ms) / (half_life_ms * 2)))
      + use_boost * log1p(use_count)
```

**Post-filters (cooldown + self_echo — применяются ПОСЛЕ apply_decay_and_boost):**

```cpp
double apply_post_filters(
    double score,
    const UsageStatsComponent& usage,
    const SpeakerComponent* speaker,
    SpeakerId agent_self_id,
    uint64_t now_ms,
    const DecayPolicy& policy) {

    if (usage.cooldown_until_ms > now_ms) {
        score *= policy.cooldown_factor;  // default 0.1
    }
    if (speaker && speaker->speaker_id == agent_self_id) {
        score *= policy.self_echo_suppression;  // default 0.3
    }
    return score;
}
```

### 2.4. Anti-loop Cooldown

Cooldown реализуется через `UsageStatsComponent`, не через `LifecycleState`. После успешного retrieval вызывается post-hook, который обновляет runtime-метрики без перехода FSM:

```cpp
// Post-retrieval hook (после успешного retrieval):
usage.cooldown_until_ms = now_ms + policy.cooldown_ms;
usage.use_count += 1;
usage.last_used_at_ms = now_ms;
// LifecycleState НЕ меняется.
// envelope.revision НЕ инкрементится (runtime state, не content-bearing).
```

В течение cooldown (`usage.cooldown_until_ms > now_ms`):
- `AntiLoopCooldown` filter пропускает unit'ы с активным cooldown.
- `apply_post_filters` применяет `score *= policy.cooldown_factor` (default 0.1).
- Self-echo suppression применяется через `apply_post_filters` если `SpeakerId == agent_self_id` → `score *= self_echo_suppression` (default 0.3).

### 2.5. Tier-specific decay (future)

В M2 — hot/warm/cold tier-specific policies:
- Hot tier: cooldown 30-60 sec, half-life 1-6 hours.
- Warm tier: cooldown 60 sec, half-life 3-14 days.
- Cold tier: no decay или very slow (30-180 days).

### 2.6. Defaults per Stack

Из memory-stacks-roadmap.md секции 8:

| Stack | DecayPolicy |
|---|---|
| BasicRag | None (no decay) |
| QAKnowledgeBase | None (frequency-based via use_count) |
| AgentLongTermMemory | Exponential, half_life=7d, use_boost=0.35, cooldown=60s, self_echo=0.3, cooldown_factor=0.1, cold_threshold=0.01 |
| SpeakerAwareChat | None (recent-based via temporal ordering) |
| CompiledWiki | None (no decay, статьи обновляются через re-compile) |
| TemporalFactStore | None (bi-temporal) |
| FullResearchMemory | Same as AgentLTM |

### 2.7. Validation Rules

- mode != None требует half_life_ms > 0.
- cooldown_ms >= 0 (если 0 — anti-loop disabled).
- self_echo_suppression в диапазоне [0, 1].
- cold_threshold в диапазоне [0, 1].

## 3. WritePolicy

### 3.1. WriteFlushTrigger

```cpp
enum class WriteFlushTrigger {
    OnEveryEvent,        // immediate write
    OnTimer,             // flush every flush_interval_ms
    OnSizeThreshold,     // flush when buffer >= size_threshold_bytes
    OnImportance,        // flush when importance_score >= importance_threshold
};
```

### 3.2. Параметры

| Поле | Тип | Диапазон | Default | Описание |
|---|---|---|---|---|
| trigger | WriteFlushTrigger | — | OnEveryEvent | условие flush |
| flush_interval_ms | uint64 | [0, infinity) | 0 | интервал для OnTimer |
| size_threshold_bytes | uint64 | [0, infinity) | 0 | порог для OnSizeThreshold |
| importance_threshold | double | [0, 1] | 0 | skip writes below |
| dedupe_distance_threshold | double | [0.0, 2.0] | 0.15 | skip/merge if cosine_distance <= threshold. Cosine distance, не L2. Default 0.15. |
| allow_supersede | bool | — | true | разрешить supersede operation |
| allow_merge | bool | — | true | разрешить merge similar units |
| allow_episode_compaction | bool | — | true | разрешить episode compaction |

### 3.2.1. MemoryMutationPolicy (M2+)

`WritePolicy` answers mechanical questions: when to flush, which importance
threshold to use, how close is "duplicate enough", and whether supersede/merge
operations are allowed. It should not be the only semantic mutation contract.

M2+ profiles should add an explicit mutation policy:

```cpp
enum class MemoryMutationPolicy : uint8_t {
    AppendOnly,
    Supersede,
    Merge,
    Mutable,
    Immutable,
    OperatorConfirmed
};
```

Default guidance:

- raw resources, episodes and audit logs: `AppendOnly`;
- factual claims and temporal facts: `Supersede`;
- near-duplicate derived notes or summaries: `Merge`;
- curated cards or high-risk model records: `OperatorConfirmed`;
- compliance/audit records: `Immutable`;
- application-owned low-risk state: `Mutable`, with normal
  `KnowledgeUnitEnvelope::revision` increments for retrieval-visible changes.

The detailed lifecycle-governance contract is in
[`memory-lifecycle-governance-roadmap.md`](memory-lifecycle-governance-roadmap.md)
AM-18.

### 3.3. WriteGate behavior

```cpp
class WriteGate {
    GateDecision evaluate(const CreateUnitRequest& req);

    // 1. importance threshold check
    if (req.importance_score < policy.importance_threshold)
        return GateDecision::Skip("below importance threshold");

    // 2. dedupe check (vector distance)
    auto nearest = embedding_store.search(req.embedding, limit=1);
    if (nearest && nearest[0].distance <= policy.dedupe_distance_threshold)
        return GateDecision::Deduplicate(nearest[0].unit_id);

    // 3. supersede check (bi-temporal)
    if (policy.allow_supersede)
        if (auto superseded = check_supersede(req))
            return GateDecision::Supersede(superseded, req);

    // 4. flush trigger
    switch (policy.trigger) {
        case OnEveryEvent: return GateDecision::Accept;
        case OnTimer: buffer(); if (timer_elapsed()) flush(); break;
        case OnSizeThreshold: buffer(); if (size() >= threshold) flush(); break;
        case OnImportance: if (req.importance_score >= threshold) flush(); break;
    }

    return GateDecision::Buffer;
};
```

### 3.4. Defaults per Stack

| Stack | WritePolicy |
|---|---|
| BasicRag | OnEveryEvent, importance=0, dedupe=0 |
| QAKnowledgeBase | OnEveryEvent, importance=0, dedupe=0.05 |
| AgentLongTermMemory | OnTimer 30s, importance=0.4, dedupe=0.15, supersede=true, merge=true |
| SpeakerAwareChat | OnTimer 5s, importance=0.2 |
| CompiledWiki | OnEveryEvent (manual write), importance=0 |
| TemporalFactStore | OnEveryEvent, supersede=true, merge=false |
| FullResearchMemory | Same as AgentLTM + dedupe=0.1 |

### 3.5. Validation Rules

- OnTimer требует flush_interval_ms > 0.
- OnSizeThreshold требует size_threshold_bytes > 0.
- OnImportance требует importance_threshold > 0.
- importance_threshold в диапазоне [0, 1].
- dedupe_distance_threshold в диапазоне [0, 2] (cosine distance).

## 4. SpeakerScopePolicy

### 4.1. SpeakerScope enum

```cpp
enum class SpeakerScope {
    Self = 0,        // сам агент
    Owner = 1,       // владелец / автор
    Cohost = 2,      // со-ведущий / модератор
    Audience = 3,    // зритель / обычный user
    Unknown = 4,     // speaker не идентифицирован
};
```

### 4.2. Параметры

| Поле | Тип | Default | Описание |
|---|---|---|---|
| include_self | bool | true | фильтровать собственную речь агента |
| include_owner | bool | true | фильтровать владельца |
| include_cohost | bool | true | фильтровать со-ведущего |
| include_audience | bool | false | фильтровать зрителей |
| attribute_quote | bool | true | сохранять speaker_id в SourceRef |

### 4.3. SpeakerFilter в RetrievalPlan

```cpp
struct SpeakerScopePolicy { ... };

// В RetrievalPlan:
std::optional<SpeakerScopePolicy> speaker_filter;

// Если задан, retrieval исключает units где speaker_scope не в списке include_*
```

### 4.4. Defaults per Stack

| Stack | SpeakerScopePolicy |
|---|---|
| SpeakerAwareChat | self=true, owner=true, cohost=true, audience=false, quote=true |
| AgentLongTermMemory | self=true, owner=true, cohost=true, audience=true (для stream-аудитории), quote=true |
| Другие | N/A (SpeakerAttribution не включён) |

> Self-echo suppression применяется через `apply_post_filters` при decay scoring (см. §2.3). `SpeakerScopePolicy` отвечает только за фильтрацию retrieval по scope (include_self/owner/cohost/audience); мультипликативное подавление собственной речи — зона ответственности `DecayPolicy`.

## 5. RetrievalMode

### 5.1. Modes

```cpp
enum class RetrievalMode {
    Associative,    // быстрый канал ~50ms (СВИНОПАС-style)
    Targeted,       // медленный канал ~4s (Сторож-style)
    Hybrid,         // оба канала + fusion
    Disabled,       // retrieval отключён (для no-answer scenarios)
};
```

### 5.2. Channels

**Associative channel:**
- Быстрый lexical/vector retrieval.
- Anti-loop cooldown filter.
- Returns top-K in 50ms.
- Используется в real-time chat / streaming.

**Targeted channel:**
- Полный retrieval с graph expansion + temporal filter + QA lookup.
- Может занимать 3-4 секунды.
- Используется для целевого поиска по запросу агента.

**Hybrid:**
- Parallel execution обоих каналов.
- RRF fusion результатов.
- Default mode для AgentLongTermMemory.

**Disabled:**
- Для no-answer scenarios (Anna AI urgency=9+).
- LLM отвечает без retrieval.
- Используется когда контекст не нужен.

### 5.3. Channel timeout в RetrievalPlan

```cpp
struct RetrievalPlan {
    double associative_timeout_ms = 50.0;
    double targeted_timeout_ms = 4000.0;
    // ...
};
```

## 6. HybridRetrievalConfig

### 6.1. FusionStrategy

```cpp
enum class FusionStrategy { RRF, WeightedMax, Learned, Planner };
```

### 6.2. RRF formula

```
score(unit) = sum_r (weight_r / (k_constant + rank_r))
```

где `rank_r` — позиция unit в result list retriever'а r (1-indexed), `weight_r` — per-retriever вес.

### 6.3. Параметры

| Поле | Тип | Default | Описание |
|---|---|---|---|
| fusion | FusionStrategy | RRF | стратегия fusion |
| rrf_k_constant | double | 60.0 | k в RRF formula |
| retriever_weights | vector<double> | {1.0, ...} | вес per retriever |
| candidate_pool_size | size_t | 200 | top-K до fusion |

### 6.4. Defaults per Stack (RRF weights)

| Stack | Retriever weights (lexical, vector, qa, graph, temporal) |
|---|---|
| BasicRag | {1.0, 1.0} |
| QAKnowledgeBase | {0.5, 0.8, 2.0, 0, 0.3} |
| AgentLongTermMemory | {1.0, 1.0, 1.5, 0.5, 1.0} |
| SpeakerAwareChat | {1.0, 0, 0, 0, 1.0} |
| CompiledWiki | {1.0, 1.0, 0, 0.3, 0} |
| TemporalFactStore | {1.0, 0, 0, 0.7, 2.0} |
| FullResearchMemory | {1.0, 1.0, 1.5, 0.5, 1.0} |

### 6.5. Other Fusion Strategies

- **WeightedMax:** max(score_r * weight_r) вместо sum — выбирает лучший retriever.
- **Learned:** ML-модель учит веса на основе feedback (M2+).
- **Planner:** RLM-style recursive planner — LLM-driven выбор retriever'ов (M2+ research).

See [`memory-routing-roadmap.md`](memory-routing-roadmap.md) for two routing paradigms plus one routing-adjacent profiling layer (RFF-density pre-filter, Adaptive RAG no-LLM, DLP-style profiling axes) and how they integrate with the MDBX layer.

## 7. Defaults Summary Table

Сводная таблица: stack → (Decay, Write, Speaker, RetrievalMode, Hybrid).

| Stack | Decay | Write | Speaker | Mode | Hybrid weights |
|---|---|---|---|---|---|
| BasicRag | None | OnEveryEvent | N/A | Hybrid | {1.0, 1.0} |
| QAKnowledgeBase | None | OnEveryEvent | N/A | Targeted | {0.5, 0.8, 2.0, 0, 0.3} |
| AgentLongTermMemory | mode=Exponential, half_life_ms=604800000 (7d), use_boost=0.35, cooldown_ms=60000 (60s), self_echo_suppression=0.3, cooldown_factor=0.1, cold_threshold=0.01 | OnTimer 30s, importance=0.4, dedupe_distance_threshold=0.15 (cosine_distance), supersede=true, merge=true | full | Hybrid | {1.0, 1.0, 1.5, 0.5, 1.0} |
| SpeakerAwareChat | None | OnTimer 5s | chat | Targeted | {1.0, 0, 0, 0, 1.0} |
| CompiledWiki | None | OnEveryEvent | N/A | Targeted | {1.0, 1.0, 0, 0.3, 0} |
| TemporalFactStore | None | OnEveryEvent | N/A | Targeted | {1.0, 0, 0, 0.7, 2.0} |
| FullResearchMemory | Exp/7d | OnTimer 30s | full | Hybrid | {1.0, 1.0, 1.5, 0.5, 1.0} |

## 8. Validation Rules (cross-cutting)

Все политики валидируются при `MemoryStack::open(spec)`:

| Правило | Если нарушено |
|---|---|
| DecayPolicy.mode != None → UsageStats=true | "DecayPolicy requires UsageStatsComponent" |
| SpeakerPolicy → SpeakerAttribution=true | "SpeakerPolicy requires SpeakerAttribution" |
| WritePolicy.OnTimer → flush_interval_ms > 0 | "Invalid WritePolicy" |
| WritePolicy.OnImportance → importance_threshold > 0 | "Invalid WritePolicy" |
| HybridRetrievalConfig → retriever_weights.size() == enabled retrievers count | "Weights size mismatch" |
| ContextBudget → sum(per_block) <= total_tokens | "ContextBudget overflow" |
| DecayPolicy.cooldown_factor в [0, 1] | "Invalid DecayPolicy.cooldown_factor" |
| WritePolicy.dedupe_distance_threshold в [0.0, 2.0] | "Invalid WritePolicy.dedupe_distance_threshold" |

## 9. References

- `guides/memory-stacks-roadmap.md` — секции 6.2, 6.3, 8 (defaults per stack).
- `guides/knowledge-units-roadmap.md` — durable lifecycle states; cooldown/soft suppression is usage policy state.
- `guides/knowledge-base-roadmap.md` — DecayAwareRetriever, AntiLoopCooldown.
- `guides/compaction-roadmap.md` — как политики взаимодействуют с compaction jobs.
- ai-agent-playbook: concepts/rag-knowledge/Внешняя память LLM-агентов — система СВИНОПАС.md — anti-loop / cooldown / подсвинок.
- ai-agent-playbook: resources/llm-research/Memory for Autonomous LLM Agents survey — конспект.md — write-read-manage loop, learned forgetting.
