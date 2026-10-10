# Advanced Binary Techniques Roadmap

> Этот гайд синтезирует три research-level заметки о binary/semantic retrieval техниках, которые расширяют существующие roadmap за пределы roadmap-label PR #29 (BinarySignature). Все три источника — внутренние протоколы исследований (`tmp/...md`); они явно помечены как "research prototype" или "research-direction only". Это **не production roadmap** — это каталог будущих implementation candidates на M3+ горизонте.

> **Scope note.** Никакие символы этого гайда не реализованы в `src/agent_memory/` на момент написания (verified via `git ls-files 'src/agent_memory/**/*.hpp'`). Все C++ сниппеты — `> **Proposed API sketch — not implemented.**` или `> **Planned API — not yet implemented.**` в зависимости от степени проработки. Класс `BinaryCode256` и хелпер `hamming()` в SAHI-заметке — illustrative sketch для production-feasibility обсуждения, не импортируемый публичный API.

## §1. Purpose

Этот гайд существует для четырёх целей:

1. **Catalog research-stage techniques.** Свести в одну карту три binary/semantic retrieval техники: Semantic Anchor Hamming Index (заметка 1), Accumulative Semantic Membership Sketch (заметка 2), и Multi-slot Binary Semantic Document Signature (заметка 3). Все три написаны как внутренние research-протоколы и **ещё не** имеют ни implementation lane, ни ADR.
2. **Compare against existing in-code surfaces.** Для каждой техники явно показать её положение относительно implemented `BinarySignature` primitives and planned bucket/index integration ([`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) §1), RFF-density pre-filter ([`memory-routing-roadmap.md`](memory-routing-roadmap.md) §4), и MinHash + LSH ([`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) Pattern 1, related to roadmap-label PR #1 in that guide's roadmap numbering).
3. **Open-question registry.** Каждая техника несёт свой список hypothesis vs validated result; этот гайд фиксирует, что именно осталось недоказанным и какие benchmarks нужны до принятия техники в roadmap.
4. **Implementation ladder as M3-research.** Все три техники находятся за пределами текущих M0/M1/M2 deliverables; ни одна не планируется к ship-it в ближайших циклах. Это reference document для будущих planning waves.

Non-goals:

- Не дублировать детальную спецификацию `BinarySignatureEncoderRegistry` (это в [`optimization-roadmap.md`](optimization-roadmap.md) §"Binary Signature Index Tasks").
- Не описывать базовые binarization-методы (sign-based, LSH, autoencoder, PQ, RotSQ — это в [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) §3).
- Не специфицировать новую архитектуру retrieval (этому посвящены [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) и [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md)).
- Не давать workload recommendations без benchmark — все три техники заявляют illustrative numbers в своих заметках; эти числа начинаются как hypothesis, не production contracts.

Этот гайд — **research preview**, не implementation ticket queue. Принятие любой из трёх техник в roadmap требует отдельной planning wave: benchmark с реальными retrieval/memory workloads, ADR, и поэтапной implementation ladder (M3-research → возможно M4+ candidate).

Связанные roadmap'ы:

- [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) — roadmap-label PR #29 (BinarySignature) как текущая binary-surface; этот гайд покрывает техники **за её пределами**.
- [`memory-routing-roadmap.md`](memory-routing-roadmap.md) — RFF-density pre-filter (Sivertsen & Eidheim 2026) и Adaptive RAG (Marina et al., 2025; Jeong et al., 2024) как mature routing; SAHI и ASMS — это **будущие** кандидаты.
- [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) — Pattern 1 MinHash + LSH как candidate filter; MSBSE/MSHDI — расширение для document-level гранулярности.
- [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) — typology 13 memory architectures; три техники этого гайда — retrieval primitives, не полные architectures.
- [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) — Naive → Contextual → ... → RLM progression; все три binary techniques — alternatives/additions внутри одной ячейки progression.
- [`chunkers-roadmap.md`](chunkers-roadmap.md) — chunking strategies, на которых three techniques строят свои document representations.
- [`research-reading-map.md`](research-reading-map.md) — указатель на внутренние заметки и внешние источники.

## §2. Source attribution policy

Этот гайд синтезирует материал из внутренних research-заметок. Цитаты следуют двухуровневому паттерну:

- `[Source: <public citation>]` — основная цитата на опубликованную работу, документацию проекта, ревизию репозитория или статью. При цитировании внешнего источника публичная ссылка авторитетна.
- `[Source: internal note — no public source available. Path: tmp/<file>.md]` — утверждение взято только из внутренней research-заметки без эквивалентного публичного источника. Требует дополнительной проверки.

Внутренние заметки — это discovery aids, не авторитетные цитаты. Когда утверждение в заметке ссылается на внешнюю работу (paper, repo, blog post), публичная ссылка предпочтительна; internal note используется как указатель на частное происхождение, не как самостоятельный evidence.

Все три source notes написаны на русском языке; этот гайд сохраняет русскую техническую нотацию (математические формулы, лейблы методов) там, где перевод был бы нагружен терминологическим шумом, но переходит на английский для структурных обозначений (section headings, table headers, code identifiers).

Все три заметки явно помечены как **research-stage, not production-ready**. Этот гайд не превращает их в production contracts. Утверждения в форме "показано", "доказано" или "гарантируется" в исходных заметках трактуются как **author's hypothesis pending benchmark**, не как validated fact.

PR-номера в этом гайде относятся к **GitHub PR #N** в нашем репозитории, не к roadmap-label "PR #N" в [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) или других гайдах. Disambiguation обязательна при ссылке на конкретный delivery, а не research direction.

## §3. Three research techniques

### §3.1. Semantic Anchor Hamming Index (SAHI)

> **Source.** Внутренняя research-заметка, status "research prototype" в её собственных терминах. Автор явно указывает, что SAHI покрывает hypothesis pending benchmark; ни одна из её experiment matrices (главы 22-26) не валидирована на наших workloads.

> [Source: internal note — no public source available. Path: tmp/Semantic Anchor Hamming Index.md]

#### 3.1.1. What it is

Semantic Anchor Hamming Index (SAHI) — это retrieval index поверх бинарных кодов фиксированной длины `B ∈ {128, 256}`. Идея: пусть корпус `X = {x_1, ..., x_N}` представлен бинарными кодами `b_i ∈ {0, 1}^B`, и пусть дан набор **anchor-вопросов** `A = {a_1, ..., a_M}` с теми же кодами `c_j ∈ {0, 1}^B`. Расстояние Хэмминга — `d_H(u, v) = popcount(u ⊕ v)`.

Для каждого anchor `a_j` строится sorted posting list `P_j = [(d_{j,1}, i_1), ...]` упорядоченный по `d_H(c_j, b_i)` по возрастанию. Хранение лучше делать **bucketed**: чанки сгруппированы по расстоянию в buckets `0 ... B`, offsets массив; binary search не нужен, последовательное чтение bucket'а дешевле.

Две реализации: **SAHI-P** (posting lists only) и **SAHI-G** (postings + global Hamming graph для beam traversal). Для глобальной версии anchors применяются ко всему корпусу; для иерархической версии anchors специфичны для каждого документа.

Query algorithm SAHI-P (1A — semantic top-postings):

```text
1. Compute query code b_q
2. Find A nearest anchors j ∈ J_q by d_H(b_q, c_j)
3. For each anchor j ∈ J_q, take top-L chunk IDs from P_j
4. Union C(q) = ∪_{j∈J_q} C_j
5. For each candidate x_i, compute exact d_H(b_q, b_i)
6. Return top-K by smallest distance
```

В режиме 1B (metric band search) вместо фиксированных top-L читается полоса `d_H(x, a_j) ∈ [δ_j - ρ, δ_j + ρ]`, где `δ_j = d_H(q, a_j)`. При полном dense postings треугольное неравенство гарантирует, что полоса возвращает **полный candidate superset** — ни один чанк с `d_H(q, x) ≤ ρ` не пропущен, поскольку anchor лежит в полосе ⇒ query и x находятся по одну сторону от медианы. Однако полоса может содержать **false positives** (анкоры, чьи чанки имеют `d_H(q, x) > ρ`). Поэтому **финальная exact Hamming distance проверка `d_H(q, x) ≤ ρ` обязательна** для каждого candidate чанка, возвращённого из полосы. Полный radius search (в смысле "возвращается ровно множество chunks с `d_H(q, x) ≤ ρ`") — это **complete superset + exact distance check**, не "exact without rerank". Sparse postings (когда индексированы только `R` бакетов на чанк) ломают completeness property; SAHI exact-radius требует dense postings.

Query algorithm SAHI-G (variant 2):

```text
1. SAHI-P seeds = S starting chunks
2. Maintain min-heap candidates, max-heap best, visited set
3. Best-first/beam traversal по Hamming-графу
4. Final union = posting candidates ∪ graph-discovered candidates
5. Exact Hamming rerank on union
6. Optional float reranker
```

Граф **глобальный**: `d_H(b_i, b_l)` не зависит от anchor. Хранить per-anchor neighbors было бы дублированием одних и тех же связей.

#### 3.1.2. Use cases

SAHI полезен, когда:

- **Бинарные коды чанков уже есть** (roadmap-label PR #29 (BinarySignature) или LSH baseline).
- **Нужен sub-linear search** поверх бинарного представления без перепроектирования encoder'а.
- **Body of work — search-heavy, low-write** (например, indexing компилированных wiki-страниц, редко меняющихся).
- **Acceptable failure mode**: семантически близкий, но не точный top-K (является ANN candidate generator).
- **G готов обслуживать fixed-degree Hamming graph** (16-64 соседа на чанк): это даёт существенный recall boost, но требует N × G_d × 4 bytes памяти.

Не подходит, когда:

- Требуется **точный top-K** без семантических anchors (SAHI 1B возможен как exact-radius search, но только при dense postings, подходящем anchor set, и обязательной финальной exact Hamming distance проверке `d_H(q, x) ≤ ρ` — band даёт complete superset с false positives, не «точный без rerank»).
- **Workload — update-heavy** (tombstones + compaction оправданы только при N >> delta churn).
- **Гетерогенный корпус с плохим semantic anchor coverage** (100 плохо подобранных anchors покрывают корпус хуже, чем несколько тысяч geometric pivots).

#### 3.1.3. Comparison vs existing techniques in our project

| Техника | Где | Сравнение с SAHI |
|---|---|---|
| **roadmap-label PR #29 (BinarySignature)** (bucket index) | [`optimization-roadmap.md`](optimization-roadmap.md) §"Binary Bucket Index Tasks" | roadmap-label PR #29 (BinarySignature) = bucket prefilter на коротких signatures, scope-aware. SAHI = retrieval index на длинных 128/256-bit кодах с semantic-interpretable anchors. Это **разные гранулярности**, не конкуренты. SAHI мог бы работать поверх roadmap-label PR #29 (BinarySignature) как второй routing-уровень (postings читают buckets второго уровня). |
| **`DenseIndexMode::BinaryCandidateFilter`** | [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) §5 | Текущая planned binary-surface фильтрует candidates для float rerank. SAHI — end-to-end binary retrieval с optional float rerank. Это **более широкий** scope, требует нового index contract. |
| **RFF-density pre-filter** | [`memory-routing-roadmap.md`](memory-routing-roadmap.md) §4 | RFF = routing over partitions в embedding space. SAHI = routing over documents в Hamming space через semantic questions. RFF использует Euclidean/Laplacian kernels; SAHI — Hamming triangle inequality. Complementary, не substitutes. |
| **Adaptive RAG (no LLM)** | [`memory-routing-roadmap.md`](memory-routing-roadmap.md) §5 | Adaptive RAG = feature-based classifier решает «нужен ли retrieval». SAHI = retrieval сам. Отношение: Adaptive RAG сидит выше; SAHI вызывается только когда classifier одобрил retrieval. |
| **DLP-style axes** | [`memory-routing-roadmap.md`](memory-routing-roadmap.md) §6 | DLP-style = user profiling, не retrieval; семантически не конкурирует с SAHI. Cross-link via policy layer (booster/suppressor). |
| **MinHash + LSH** | [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) Pattern 1 | MinHash = candidate filter по Jaccard на shingles (text-side). SAHI = candidate filter по Hamming на бинарных embeddings (vector-side). Разные метрики; не конфликтуют. |
| **ColBERT / late interaction** | [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) §5 | ColBERT = multi-vector tokenizer-level. SAHI = single binary code per chunk + anchors. Разные granularity; см. также MSBSE §3.3 ниже. |
| **RAG pipelines** | общая архитектура | SAHI мог бы заменить BM25+dense step в hybrid retrieval для некоторых workloads; потенциальная RAG-front, не обязательно full replacement. |

#### 3.1.4. Public citations

> **Author's note.** Author of the internal note specifically anchors SAHI on Document Expansion by Query Prediction (Nogueira et al.) for the "questions as index coordinates" idea, on Multi-Index Hashing (Norouzi et al.) for the exact kNN baseline, and on Extreme Pivots / metric-inequality literature (Bustos & Sklar) for the triangle-inequality lower bound. These three citations are repeated below as the foundational anchors; all other SAHI structure is **author's proposal pending benchmark**.

[Source: arXiv:1904.08375 — Nogueira, Yang, Cho, Lin, "Document Expansion by Query Prediction", 2019] <br>
[Source: arXiv:1307.2982 — Norouzi, Punjani, Fleet, "Fast Exact Search in Hamming Space with Multi-Index Hashing", NIPS 2012 (extended)] <br>
[Source: https://iaeng.org/publication/IMECS2008/IMECS2008_pp437-445.pdf — "New Distance Lower Bounds for Efficient Proximity Searching"] <br>
[Source: https://dl.acm.org/doi/10.1007/978-3-642-41062-8_12 — Bustos, Sklar, "Extreme Pivots for Faster Metric Indexes" (book chapter)] <br>
[Source: internal note — no public source available. Path: tmp/Semantic Anchor Hamming Index.md]

#### 3.1.5. Open questions / research challenges

SAHI-автор явно перечисляет следующие нерешённые вопросы:

1. **Semantic vs geometric pivots.** Гипотеза "geometric pivots работают так же или лучше, и интерпретируемость anchors — единственное их преимущество" требует прямого experiment (тест 1 в ablation-list автора). Если geometric побеждает, anchor-set можно выбирать дешевле.
2. **Top-L vs metric band.** При правильно подобранной полосе `[δ - τ, δ + τ]` полоса адаптируется к query-anchor расстоянию; может работать лучше фиксированного L (тест 2 автора).
3. **Union vs voting.** Если кандидаты должны встретиться в V postings, recall падает (меньше кандидатов), precision растёт (меньше шума). Это гипотеза автора, не измерена.
4. **Dense vs sparse postings.** Sparse (`R` anchors per chunk) экономит память в `M/R` раз ценой потери строгого metric guarantee. Strict tradeoff нуждается в benchmark.
5. **Граф реально помогает?** SAHI-G vs SAHI-P при одинаковом candidate budget должен показать +3-5 пп Recall@10, иначе граф лишний. Это центральная hypothesis SAHI-G.
6. **Float reranker нужен?** Binary top-K vs binary top-100 + float rerank top-10 — последний precision tier, может быть дороже, чем оправдано.
7. **Leakage в benchmark.** Если anchor questions генерируются из тех же документов, что и test queries, индекс фактически знает тестовые запросы заранее. Нужен явный holdout между anchor-generation и retrieval-test.

#### 3.1.6. Integration path with `agent-memory-cpp` MDBX layer

**High-level sketch only — not a proposed ADR.** SAHI как candidate technique не имеет PR/ADR, lane, или planned DBI. Описанное ниже — намёк на возможную будущую integration path, не фиксация структуры.

DBI table placement при hypothetical implementation:

- `sahi_postings` (DBI) — anchor-bucketed chunk IDs. Schema: `[anchor_id : uint16][bucket_distance : uint16][chunk_id : uint32]`. Bucketed layout даёт O(1) к offset-array без binary search. `bucket_distance` хранится как 16-bit unsigned integer (`uint16_t`), поскольку 256-bit коды могут давать Hamming distance до 256 включительно (8-bit `uint8_t` обрезал бы значения ≥ 256).
- `sahi_graph` (DBI) — фиксированная степень `G_d`, `[chunk_id : uint32][neighbor_chunk_id : uint32]`. На каждый chunk — `G_d` outgoing edges.
- `sahi_anchors` (DBI) — `[anchor_id : uint16][anchor_code : BinaryCode256]`. Семантические anchors живут здесь, не в payload tables.
- `sahi_query_log` (optional, M4+) — telemetry для grading.

Compaction hook при hypothetical:

- Decay / ArchiveCold / Dedupe jobs (per [`compaction-roadmap.md`](compaction-roadmap.md)) задевают SAHI как часть index reindexing: `deci_manifest` обязан содержать `DerivedRecordKind = SAHI_POSTING_ENTRY | SAHI_GRAPH_EDGE`.
- Для tombstones — chunk_alive filter в query path; физическое удаление откладывается до compaction.
- Graph rebuild обычно требует полного kNN pass; это дороже, чем bucket postings rebuild, и должен быть planned compaction job, не inline.

Read path при hypothetical:

```text
query embedding
  → embedder (или бинаризация на месте)
  → binary query code b_q
  → SAHI-P entry: nearest anchors, posts reads
  → optional SAHI-G traversal (graph visits by Hamming)
  → final candidate union
  → exact Hamming rerank
  → optional float reranker
  → top-K
```

Это read path из заметки автора, воспроизведённый здесь как context. Подчеркну: ни SAHI contract, ни SAHI DBI не существуют в `src/agent_memory/` или в существующих guides. Любая future integration будет требовать отдельного ADR и planning wave.

### §3.2. Accumulative Semantic Membership Sketch (ASMS)

> **Source.** Полный research protocol на 2158 lines, статус "research prototype → production feasibility study". Базовый research implementation на Python+PyTorch; целевая — C++17. Автор явно называет эту работу более рискованной, чем mean embedding baseline, и перечисляет **10 kill criteria** до её принятия в production.

> [Source: internal note — no public source available. Path: tmp/Accumulative Semantic Membership Sketch.md]

#### 3.2.1. What it is

ASMS — это trainable additive binary semantic sketch для document membership query. Документ `D = {x_1, ..., x_n}` с chunk embeddings `x_i ∈ R^d` отображается в sparse non-negative contributions `a_i = f_θ(x_i)`, где `a_i ∈ R_{≥0}^B`. Document accumulator: `c_D = Σ a_i`. После thresholding получается binary sketch `b_D ∈ {0, 1}^B`, где каждый бит означает **присутствие латентного семантического признака** (не уникальный ID документа).

Query encoder отдельно: `r_q = h_φ(e_q) ∈ [0, 1]^B`. Asymmetric containment score:

```text
coverage_soft(q, D) = Σ_j w_j · r_{q,j} · p_{D,j} / Σ_j w_j · r_{q,j} + ε
```

где `p_{D,j} = 1 - exp(-c_{D,j} / s_j)` (additive hazard aggregator) — гладкое насыщение.

ASMS явно **не автоэнкодер**: reconstruction не используется; supervision — query-document relevance, evidence-aware loss, noise/dup/invariance losses. Ключевые properties:

- **Permutation invariance**: chunks могут идти в любом порядке.
- **Monotonicity**: добавление чанка не выключает уже активные признаки.
- **Commutativity/associativity**: `c_A + c_B` коммутативно, что позволяет индексировать части документа независимо.
- **Online update**: `c_D ← c_D + a_new` (add) и `c_D ← c_D - a_removed` (delete) без полного reindex.
- **Invertible update**: `(c_D + a_i) - a_i = c_D` для сохранённых per-chunk contributions.
- **Sparsity**: chunk активирует только `K` признаков (`K << B`), что делает add/delete O(K).
- **Controlled saturation**: документный бит не должен автоматически стать активным при росте числа чанков.
- **Asymmetry**: missing query-bit штрафуется сильнее, чем extra document-bit.

Архитектурные режимы (автор явно просит проверить несколько):

- **ASMS-AH (Additive Hazard)** — main кандидат: `c_j = Σ a_{i,j}`; `p_j = 1 - exp(-c_j / s_j)`; bin `b_j = 1[c_j ≥ t_j]`. Threshold в probability-space эквивалентен `t_j = -s_j · log(1 - τ_j)`.
- **Simple sum + sigmoid**: baseline, чувствителен к дубликатам, простой в реализации.
- **Noisy-OR**: `p_j = 1 - Π_i (1 - v_{i,j})` — additive в log-space, но MIL-литература показывает, что noisy-OR может распределять градиент по слабым активациям хуже, чем max pooling. Требует проверки.
- **Max + accumulation hybrid**: `y_j = α_j m_j + β_j log(1 + s_j) - τ_j`; различает single-strong-evidence от multiple-moderate-evidence. Сложнее в delete-path (нужен второй по силе max).

Sparsity modes (per-chunk):

- **Top-K** — оставить только `K` максимальных вкладов.
- **Threshold sparsity** — `a_{i,j} = u_{i,j} · 1[u_{i,j} > ε]`.
- **Differentiable sparse gating** — sparsemax, entmax, hard-concrete, STE-gating.

Рекомендованная стартовая конфигурация автора: `B = 4096`, `K = 16`, base embedding E5/BGE-like, query/chunk encoders asymmetric, aggregator additive hazard, sparse uint16 accumulator на prod, float32 на research, dedup exact hash + weak novelty weighting.

#### 3.2.2. Use cases

ASMS полезен, когда:

- **Требуется asymmetric containment semantics** ("query семантика содержится в документе?", не "documents similar?").
- **Online add/delete обязателен** (контракт retrieve-store-update без full reindex).
- **Dлинные документы с редкими evidence chunks** (LongMemEval-style workloads).
- **Rare-facet retrieval** критичен (single supporting chunk в 128 distractor chunks — Recall@10 должен быть высоким).
- **Gотовы обучить 4096-bit representation** (требует существенного embedding-scale compute).
- **Query bits должны быть разрежены** — `Q ∈ {4, 8, 16, 32, 64}`.

Не подходит, когда:

- **Cumulative-документы с повторами** — simple sum считает duplicates независимыми evidence; correction (D1-D3 modes автора) усложняет систему. Если workload — pure append-mostly без dedup concern, mean embedding (B0 baseline) может работать не хуже и быть значительно проще.
- **Контрадикторные документы** ("X верно" + "X неверно"). ASMS sketch presence не решает contradiction; требуется separate fact store.
- **Embeddings плохо различают chunks** (если base embeddings не находят evidence chunks на flat scan, ASMS не исправит фундаментальную проблему).
- **Compute ограничен** — обучение требует MS MARCO-scale contrastive data + custom distillation; это не out-of-the-box encoder.

#### 3.2.3. Comparison vs existing techniques in our project

| Техника | Где | Сравнение с ASMS |
|---|---|---|
| **Mean document embedding** | [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) §4 baseline | Mean baseline — ключевой comparator. ASMS обязано превзойти mean на rare-facet workload (kill criterion #1). |
| **Max chunk similarity / chunk-as-document** | [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) Pattern 2 context | Multi-vector per document; дороже хранить (chunk embedding на каждый chunk), но upper bound на quality. ASMS — multi-bit-per-doc compression этой идеи. |
| **RAG с LLM-generated facts** | [`usage-llm-wiki.md`](usage-llm-wiki.md) and [`usage-memory-models.md`](usage-memory-models.md) | RAG извлекает факты через LLM; ASMS извлекает **семантические признаки** через ML encoder. Похожая цель (semantic compression), разный mechanism. |
| **roadmap-label PR #29 (BinarySignature)** | [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) §1 | roadmap-label PR #29 (BinarySignature) = chunk-level signature для bucket prefilter. ASMS = document-level semantic presence sketch. ASMS оперирует documents, не chunks; иначе вычисляет, иначе индексирует. См. MSBSE §3.3. |
| **ColBERT late interaction** | [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) §5 | ColBERT — token-level multi-vector. ASMS — bit-level single-vector per document с monotone aggregation. Slot multi-vector variant для ASMS упомянут в §3.2.5; см. ниже. |
| **Autoencoder document code** | бейзлайн автора (B3) | Точка отказа: ASMS-aware supervision должна превзойти reconstruction-based bottleneck. Автор явно проверяет это. |
| **MASNet-style monotone encoder** | упомянут в §3.5 source notes | MASNet доказывает theoretical impossibility для идеального monotone-separable repr на бесконечных множествах; ASMS — relaxed version. |

#### 3.2.4. Public citations

> **Author's note.** ASMS-заметка опирается на четыре research streams и одну отдельную работу. Автор ссылается на эти работы по именам/концепции в §3 заметки, но **не приводит arXiv IDs или URLs** — все пять категорий цитируются как field-name references. Конкретные paper identifiers **не** добавлены в этом гайде, чтобы не нарушить правило "no fabricated URLs or arXiv IDs". Readers, ищущие эти работы, ищут по canonical названиям, приведённым ниже.

[Source: "Deep Sets" — Zaheer, Kottur, Ravanbakhsh, Poczos, Salakhutdinov, Smola — NeurIPS 2017 (canonical title "Deep Sets")] <br>
[Source: "Multiple-Instance Learning" field — canonical references include Dietterich et al. 1997 (MIL origin), and standard MIL pooling surveys] <br>
[Source: "Neural Bloom Filter" / trainable approximate membership query (canonical title and venue per author's reference; specific identifier not added in this guide)] <br>
[Source: "MASNet" / Monotone And Separable set encoders (canonical title per author's reference; specific identifier not added in this guide)] <br>
[Source: "Hash-RAG" / asymmetric binary retrieval for propositions (canonical title per author's reference; specific identifier not added in this guide)] <br>
[Source: internal note — no public source available. Path: tmp/Accumulative Semantic Membership Sketch.md]

> **PR-number disambiguation.** Никакие конкретные PR-номера нашего репозитория не относятся к ASMS. Это research-stage proposal, не planned delivery. Любая будущая implementation потребует отдельной planning wave.

#### 3.2.5. Open questions / research challenges

ASMS-автор явно перечисляет следующие kill criteria (исследование останавливается, если):

1. **Mean embedding работает не хуже** на rare-facet benchmark — фундаментальная inadequacy добавления аккумуляции.
2. **Bitwise OR показывает то же качество** при меньшей сложности — ASMS-specific contribution не нужен.
3. **Накопление неизбежно сатурирует** длинные документы — даже controlled saturation modes не справляются.
4. **Noise suppression уничтожает единичные слабые факты** — detection/sensitivity trade разрешается в сторону detection, ASMS бесполезно.
5. **Требуется ~столько же памяти как для chunk embeddings** — compression выгода не реализована.
6. **Deletion требует ~full rebuild** — online-update property не сохраняется.
7. **Query bits нельзя разрежить** достаточно — inverted postings становятся полными.
8. **Model retraining требует слишком частого full reindex** — operational cost слишком высокий.
9. **ASMS работает на синтетике, но не на реальных retrieval/memory datasets** — overfit на ASMS-Bench.

Plus specific open architectural questions:

- **Slot integration.** Автор упоминает B13 "Multi-slot binary encoder" (предыдущая работа, см. §3.3) и B14 "Hybrid ASMS + slots". Как ASMS и multi-slot представления компонуются — это ещё один design axis, не решённый в текущей заметке.
- **Contradiction handling** (§30 заметки) — temporal accumulators, positive/negative channels, ternary query (required/forbidden/don't care) — это **future extension proposals**, не часть прототипа v1.

#### 3.2.6. Integration path with `agent-memory-cpp` MDBX layer

> **High-level sketch only — not a proposed ADR.** ASMS как candidate technique не имеет PR/ADR, lane, или planned DBI. Описанное ниже — контекстное намёк на возможную future integration, не фиксация структуры.

DBI table placement при hypothetical:

- `asms_bit_postings` (DBI) — inverted: `[bit_id : uint16][document_id : uint32]`. Для больших postings — WAND/Block-Max WAND/MaxScore layers.
- `asms_document_accumulator` (DBI) — sparse `[document_id : uint32][bit_id : uint16][value : uint16]` entries.
- `asms_binary_snapshot` (DBI) — `[document_id : uint32][semantic_bitmap : BinaryBitmap]`. Для быстрого scan-rerank.
- `asms_chunk_contributions` (DBI) — `[chunk_id : uint32][bit_id : uint16][weight : uint8]` records. Per-chunk contributions для delete-корректности.

`ContributionHeader` (per автору): `[encoder_version : uint32][embedding_version : uint32][bit_count : uint16][top_k : uint16]`. Per-chunk contributions зависят от версии encoder, и нельзя смешивать разные version-ы в одном accumulator. Это требует `encoder_version` в `schema_info` и policy при смене версии (поэтапный dual-read + reindex + switch + drop old).

Compaction hook при hypothetical:

- **ASMS-specific rebuild job** при смене encoder/embedding/bit_count/threshold/domain — крупный compaction job, не inline.
- **Per-document rebuild** при локальной проблеме (corrupted accumulator) — single-document scope, не full-corpus.
- **Reconciliation при split/merge документов** — explicit semantic-aware reconciliation требуется (точный accumulator = sum, но dedup tracking ломается на split).

Read path при hypothetical:

```text
query embedding
  → query encoder → sparse query requirements
  → postings union (по query bits)
  → weighted containment → top documents
  → soft accumulator rerank
  → chunk retrieval
  → float reranker
  → LLM context
```

Это read path из заметки автора. Подчёркиваю: ASMS не имеет ADR, lane, или PR-номера в нашем репозитории. Все references здесь — research-preview context, не implementation guidance.

### §3.3. Multi-slot Binary Semantic Document Signature (MSBSE / MSHDI)

> **Source.** Полный research protocol на 2510 lines, статус "research-grade development with subsequent move to production". Базовый research implementation на Python+PyTorch; целевая production-интеграция — C++17, ONNX Runtime или LibTorch. Автор явно описывает MSBSE как research-only direction, не как shipping plan.

> [Source: internal note — no public source available. Path: tmp/Многослотовая бинарная семантическая сигнатура документа.md]

#### 3.3.1. What it is

Multi-Slot Binary Semantic Encoder (MSBSE) отображает document `D = {x_1, ..., x_n}` с chunk embeddings `x_i ∈ R^d` в **фиксированное множество binary slot codes** `B_D = {b_{D,1}, ..., b_{D,S}}`, где `b_{D,s} ∈ {-1, +1}^B`. Каждый slot представляет **отдельную семантическую facet** документа — тему, тип информации, или класс вопросов.

Query encoder отдельно: `q → b_q ∈ {-1, +1}^B`. Document relevance score — **minimum Hamming distance** среди всех slots:

```text
d(q, D) = min_s H(b_q, b_{D,s})
```

То есть retrieval — это поиск documents, где хотя бы один slot близок к query. Этот принцип решает problem "average embedding hides rare facets": один усреднённый document embedding будет доминироваться центральной темой (70% C++ content + 5% testing edge case); multi-slot representation имеет структуру где отдельные slots покрывают отдельные facet'ы.

Архитектура (recommended):

```text
chunk embeddings (set)
  ↓
shared MLP projection (LayerNorm → Linear → GELU → Linear)
  ↓
1-4 ISAB / SAB blocks (Set Transformer)
  ↓
PMA с S обучаемыми seed-vectors
  ↓
S semantic slots (continuous representation)
  ↓
slot projection (per-slot MLP → B logits)
  ↓
S × B binary code per document
```

Binarization и aggregation по слотам разделены на training и inference — Straight-Through Estimator (STE) применяется **только при обучении** для проведения градиента через дискретный шаг, тогда как inference использует чистый deterministic `sign`:

- **Training:**
  - Soft binarization слота: `b̃ = tanh(β · l)` с постепенным увеличением `β` (continuation schedule), что даёт градиент через непрерывную relaxation.
  - Hard binarization при backprop: `b = sign(l)` через **Straight-Through Estimator (STE)** — forward pass дискретный, backward pass пропускает градиент как identity; альтернативы — Gumbel-Softmax, soft-to-hard schedule.
  - Мягкая slot aggregation: `s(q, D) = τ_s · log Σ_s exp(r_{q,D,s} / τ_s)` (logsumexp по слотам с temperature `τ_s`), заменяющая жёсткий `min_s H(b_q, b_{D,s})` чтобы градиент тек по всем слотам сразу.
- **Inference:**
  - Deterministic hard binarization: `b = sign(l)` или эквивалентно `b = (l > 0)`; **никакой gradient estimator** — STE не используется, модель уже обучена.
  - Жёсткая slot aggregation: `d(q, D) = min_s H(b_q, b_{D,s})` — каждый slot рассматривается как отдельный кандидат, берётся ближайший.
  - Никаких scheduling parameters (`β`, `τ_s`) — это обучающие гиперпараметры, не runtime knobs.

```text
s(q, D) = τ_s · log Σ_s exp(r_{q,D,s} / τ_s)
```

Без reconstruction-loss (это ключевое design choice): set-based reconstruction не помогает retrieval. Основные supervision signals:

- **Retrieval loss**: InfoNCE contrastive с in-batch negatives, BM25/dense hard negatives, topical близкие negatives.
- **Distillation loss**: от float teacher `t(q,D) = max_i cos(e_q, x_i)` (expensive upper bound) через KL divergence.
- **Coverage loss**: каждый chunk должен быть `cos`-близок хотя бы к одному slot; иначе retrieval-graduate learning игнорирует редкие chunks.
- **Evidence-aware loss**: при наличии evidence chunk annotations — отдельный supervision на evidence → slot alignment.
- **Diversity loss**: `1/S(S-1) · Σ_{s≠t} max(0, cos(u_s, u_t) - m)`; m = 0.3-0.6. Не требует полной ортогональности (реальные темы связаны).
- **Slot-balance loss**: равномерная нагрузка slot'ов (для длинных документов).

Plus binary regularization: quantization loss, bit balance, decorrelation.

Число slots и bits автора рекомендует перебирать при **одинаковом общем bit budget** — это центральный experiment:

```text
1 × 512 бит
2 × 256 бит
4 × 128 бит
8 × 64 бит
```

Это позволяет отделить "полезнее ли несколько коротких кодов, чем один длинный" от "просто ли больше битов". MSBSE-stored document memory: `S × B / 8` bytes. При `S=8, B=128`: 128 bytes per document; при `S=4, B=256`: 128 bytes; при `S=16, B=256`: 512 bytes.

#### 3.3.2. Use cases

MSBSE полезен, когда:

- **Documents multi-topic** (длинные документы с разными facet'ами, не редкость).
- **Rare-facet retrieval критичен** (запрос на 5% part of 100-chunk document должен находиться).
- **Single-mean embedding hides structure** (типичная проблема для compiled articles, technical wiki).
- **Готовы обучить custom set encoder** (Set Transformer / Deep Sets архитектура, не out-of-the-box embedding).
- **Have long-context chunks** (64-128+ chunks per document); hierarchical mode автор рекомендует для 256+ chunks.
- **HNSW / MIH binary backend доступен** для индексации на million-doc scale (иначе flat scan становится bottleneck).

Не подходит, когда:

- **Documents short** (single chunk — multi-slot degraded до single code).
- **Documents single-topic** (multi-slot коллапсирует до single code; coverage loss не помогает, нет variety).
- **Base embeddings не различают facets** (если float chunk retrieval не находит evidence chunks, multi-slot ничего не исправит).
- **Compute-budget tight** (Set Transformer с ISAB дороже, чем autoencoder document code).
- **Workload требует contradiction-aware или temporal-aware** retrieval (MSBSE не моделирует это).

#### 3.3.3. Comparison vs existing techniques in our project

| Техника | Где | Сравнение с MSBSE |
|---|---|---|
| **roadmap-label PR #29 (BinarySignature)** | [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) §1 | roadmap-label PR #29 (BinarySignature) = chunk-level signatures для bucket prefilter. MSBSE = document-level **multi-slot** semantic signatures. MSBSE — это **orthogonal axis**: multi-slot на document-level, а не single-slot на chunk-level. |
| **Mean document embedding** | [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) §4 baseline | Mean — обязательный baseline (kill criterion: MSBSE должно превзойти mean на MFDR). |
| **Max chunk similarity** | [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) upper-bound comparison | Max chunk = expensive upper bound (chunk embeddings × chunks chunks). MSBSE — compact version той же идеи: slot collision вместо chunk collision. |
| **ColBERT** | [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) §5 | ColBERT = token-level multi-vector. MSBSE = slot-level multi-vector (S=4-16) на document-level. Похожая multivector-идея, разная гранулярность. Автор явно ссылается на ColBERT как на важный ориентир. |
| **ASMS / accumulative semantic membership** | §3.2 этого гайда | ASMS = single-binary-vector per document через accumulation. MSBSE = multi-slot-binary per document через set encoder. Разные пути к одному goal. Автор ASMS упоминает MSBSE как B13 baseline; MSBSE автор делает обратную ссылку как альтернативу. Они **complementary**: см. §3.3.4 ниже. |
| **Set Transformer для других задач** | standard PyTorch lib | MSBSE использует Set Transformer как building block, не предлагает нового механизма set-обработки. |
| **Multi-Index Hashing** | [`optimization-roadmap.md`](optimization-roadmap.md) ANN baselines | MIH = exact kNN для binary codes на длинных 64/128/256-bit кодах. MSBSE использует MIH как candidate retrieval backend на slot-level. |
| **Pre-trained E5/BGE float retrieval** | overall | Float retrieval = верхняя граница, не compactable. MSBSE — compression layer для тех же задач с trade quality/memory. |

#### 3.3.4. Public citations

> **Author's note.** MSBSE-заметка опирается на несколько field-name references, но **не приводит arXiv IDs или URLs** для большинства из них. Автор ссылается на семейства методов (Set Transformer, Deep Sets, PMA, ISAB, SAB, ColBERT, Matryoshka, HashNet, MIH, binary hashing continuation) по canonical именам. Конкретные paper identifiers намеренно **не добавлены** в этом гайде, чтобы не нарушить правило "no fabricated URLs or arXiv IDs". Readers, ищущие эти работы, ищут по canonical именам.

[Source: "Set Transformer" / "Deep Sets" — canonical papers by Lee et al. 2019 (Set Transformer) and Zaheer et al. 2017 (Deep Sets); identifiers not added in this guide] <br>
[Source: "ColBERT" — Khattab & Zaharia 2020; identifier per author's reference; specific arXiv ID not added in this guide] <br>
[Source: "Matryoshka Representation Learning" — Kusupati et al. 2022; identifier per author's reference; specific arXiv ID not added in this guide] <br>
[Source: "HashNet" / deep hashing continuation — Cao et al. 2017; identifier per author's reference; specific arXiv ID not added in this guide] <br>
[Source: "Multi-Index Hashing" — Norouzi, Punjani, Fleet — NIPS 2012 / extended version per author's reference] <br>
[Source: internal note — no public source available. Path: tmp/Многослотовая бинарная семантическая сигнатура документа.md]

#### 3.3.5. Open questions / research challenges

MSBSE-автор перечисляет следующие hypothesis-tier claims, требующие benchmark:

1. **Multi-slot превосходит mean embedding** на rare-facet queries. Без этого MSBSE не оправдано.
2. **`S` коротких кодов лучше одного кода** той же общей bit-capacity. Это центральный experiment E5 ("E5. Одинаковый общий битовый бюджет").
3. **Retrieval loss важнее reconstruction loss.** Автор явно не использует reconstruction; это design, не benchmark.
4. **Coverage loss нужен**, чтобы модель не игнорировала редкие chunks.
5. **Exact flat Hamming scan** конкурентоспособен до определённого размера корпуса.
6. **MIH/HNSW ускоряют** без изменения модели при больших корпусах.

Plus risks (раздел 43):

- **Slot collapse**: все slots кодируют одну доминирующую тему. Защита — coverage/diversity/evidence diagnostics.
- **Слишком короткие коды**: relevant vs hard-negative Hamming distances сливаются. Защита — увеличить B, hard negatives, distillation.
- **Слишком много слотов**: память растёт, случайные совпадения растут, document length bias появляется. Защита — фиксированный S + равный-bit-budget comparison.
- **Обучение только на популярных темах**: редкая семантика игнорируется. Защита — LoTTE/MFDR/QASPER evidence.
- **Учебная утечка**: synthetic queries попадают в test. Защита — split до генерации + locked final benchmark.
- **Хороший reconstruction, плохой retrieval**: поэтому reconstruction не используется.
- **Хороший binary model, плохой ANN**: ANN recall должен быть > 0.98 относительно flat scan.
- **Бинарный индекс проигрывает простому scan** на компактных кодах.

Контрадикторные документы ("X верно" + "X неверно") — **не** проблема прототипа, но ограничение, явно зафиксированное в MSBSE-заметке.

#### 3.3.6. Integration path with `agent-memory-cpp` MDBX layer

> **High-level sketch only — not a proposed ADR.** MSBSE/MSHDI как candidate techniques не имеют PR/ADR, lane, или planned DBI. Описанное ниже — контекстное намёк на возможную future integration, не фиксация структуры.

DBI table placement при hypothetical:

- `msbse_slot_records` (DBI) — `[document_id : uint32][slot_id : uint16][slot_code : BinaryCode256]`. Каждый slot — отдельная индексная запись; дедупликация по document_id при search.
- `msbse_index` (DBI) — slot-level index (MIH/HNSW/Flat); `[slot_code : BinaryCode256][document_id : uint32][slot_id : uint16]`.
- `msbse_metadata` (DBI) — `[document_id : uint32][encoder_version : uint32][slot_count : uint16][bit_count : uint16]`. Versioning обязательно.

Compaction hook при hypothetical:

- **Encoder version migration** — explicit migration path: новый индекс, dual-read, reindex corpus, switch, drop old (по аналогии с ASMS §31 автора).
- **Slot rebalancing** при domain drift — periodic rebuild, не inline.

Read path при hypothetical:

```text
query embedding
  → query encoder → binary query code b_q
  → slot-level index search (Hamming, MIH или Flat)
  → dedupe by document_id
  → sort by minimum Hamming per document
  → optional float reranker
  → top-K documents
  → chunk retrieval внутри document
```

Read path из заметки автора (раздел 1), воспроизведённый как context. MSBSE оперирует на document-level; chunk retrieval внутри document — отдельный pipeline (hybrid BM25+dense+rerank).

## §4. Comparison matrix

Сравнительная матрица трёх техник по единой сетке осей. Все утверждения quality/size/effort — **author's hypothesis**, не validated на наших workloads. Перед implementation необходимо запустить benchmarks ([`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) §6, [`optimization-roadmap.md`](optimization-roadmap.md) §"Quality Targets Per Mode").

| Axis | SAHI | ASMS | MSBSE / MSHDI |
|---|---|---|---|
| **Purpose** | Retrieval index поверх бинарных chunk codes через semantic-interpretable anchors | Document presence sketch (additive, monotone) для asymmetric containment retrieval | Document-level multi-slot binary semantic codes для rare-facet retrieval |
| **Data structure** | Per-anchor bucketed postings + optional global Hamming graph | Sparse accumulator `c_D ∈ R_{\geq 0}^B` + binary snapshot + per-chunk contributions | `S` slot codes per document, `b_{D,s} ∈ {-1, +1}^B` each |
| **Update pattern** | Main immutable + Delta LSM-style; physical deletion через tombstones в compaction | Add: `c_D ← c_D + a_new`, Delete: `c_D ← c_D - a_removed`, O(K); merge/split через accumulator sum | Encoder-driven reindex при rebuild; нет cheap online update (slot set фиксирован S) |
| **Query type** | Exact Hamming rerank over posting candidates ∪ graph candidates | Asymmetric weighted containment, soft accumulator rerank, optional chunk retrieval | `min_s H(b_q, b_{D,s})` + dedupe by document_id |
| **Comparison vs BM25 / vector / MinHash** | BM25-style кандидаты не использует; vector-replacing ANN candidate generator через Hamming; MinHash — alternative (text Jaccard) vs SAHI (vector Hamming) | BM25: orthogonal; mean vector: lower-bound comparator; bitwise OR: lower-bound; noisy-OR/max: comparable; ASMS должна превзойти все baselines (kill criteria) | Mean embedding: обязательно превзойти на rare-facet; max chunk: upper bound; bitwise OR: lower bound; k-means centroids: comparator |
| **Comparison vs RFF / binary embeddings (roadmap-label PR #29 (BinarySignature))** | RFF = pre-filter в float RFF space; SAHI = ретривер в Hamming space; complementary. roadmap-label PR #29 (BinarySignature) = chunk-level signatures; SAHI = retrieval index на chunk signatures | roadmap-label PR #29 (BinarySignature) = chunk-level fingerprints; ASMS = document-level sketch; разные granularity | roadmap-label PR #29 (BinarySignature) = chunk-level single code; MSBSE = document-level multi-slot; разные granularity |
| **Comparison vs LLM Wiki / memory architectures** | Replacement для hybrid BM25+dense step, не для memory architecture per se | Compatible с LongMemEval-style workloads; не competing с Mem0/Zep/A-MEM architectures | Compatible с retrieval pipelines внутри memory architectures; не competing |
| **Public source citations** | arXiv:1904.08375 (Document Expansion); arXiv:1307.2982 (MIH); IMECS 2008 lower bounds; Springer 2013 Extreme Pivots | Deep Sets (field reference); MIL (field); Neural Bloom Filter (field); MASNet (field); Hash-RAG (field) — field-name references без arXiv IDs | Set Transformer; Deep Sets; ColBERT; Matryoshka; HashNet; MIH — field-name references без arXiv IDs |
| **Open questions** | Semantic vs geometric pivots; top-L vs metric band; union vs voting; dense vs sparse; graph vs posting extension; float reranker need; leakage в benchmark | 9 kill criteria (mean embedding wins, OR wins, saturation, noise suppression, memory cost, deletion cost, query sparsity, retraining cost, synthetic-only); 9 ablations; 7 training stages | 6 hypothesis-tier claims (multi-slot > mean, multi-slot budget equiv, retrieval > reconstruction, coverage need, flat scan competitive, MIH/HNSW accelerators); 8 risks (slot collapse, code length, slot count, popular topics, leakage, model/ANN drift, etc.) |
| **Index backend options** | Sparse postings (LSM), dense postings (array), MIH, HNSW-Hamming, graph traversal | Inverted postings (bit_id → doc_id), WAND/Block-Max WAND, full scan, sparse accumulator | Flat Hamming scan, MIH, HNSW-Hamming; optional set transformer inference через ONNX Runtime |
| **Compute cost at query** | `A` XOR+POPCNT до anchors (~M×4 ops for B=256) + `L` XOR+POPCNT rerank + optional graph visits | Query encoder forward + postings traversal (~Q XOR+POPCNT) + soft accumulator rerank | Query encoder forward + slot-level index search (O(S) per document) + dedupe |
| **Storage at scale (illustrative)** | N chunks × ~32 bytes (256-bit code) + ~400 MB postings (N=10^6, M=100) + ~32-128 MB graph | ~16 KiB/document dense accumulator (B=8192, uint16) или sparse + chunks storage | ~32-512 bytes/document (S×B/8: S=8,B=128 → 128 B; S=16,B=256 → 512 B) |
| **Training requirement** | No training for SAHI-P/G with geometric anchors; LLM-generated optional | Custom training: MS MARCO contrastive, distillation, ablations | Custom training: Set Transformer/Dense Sets + multi-loss + retrieval distillation |
| **Online update cost** | LSM-style delta + tombstones | O(K) add/delete via stored contributions | Rebuild per document (encoder re-forward) |
| **C++ integration maturity** | None — research sketch | None — research protocol с C++17 target | None — research protocol с C++17/ONNX/LibTorch target |

## §5. Where this fits in our roadmap

Этот раздел явно позиционирует три техники относительно существующих roadmap-направлений. **Ни одна из трёх техник не планируется к ship-it** в текущих M0/M1/M2 циклах. Все три — candidates для возможной M3-research wave, pending benchmark и ADR.

### §5.1. Relative to roadmap-label PR #29 (BinarySignature) ([`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md))

`BinarySignature` primitives are the current implemented binary surface, while bucket/index integration remains planned; see the scope note in [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md). This is a **chunk-level** fingerprint surface for bucket prefilter.

Три техники этого гайда — **orthogonal extensions** за пределами roadmap-label PR #29 (BinarySignature):

- **SAHI**: использует roadmap-label PR #29 (BinarySignature)-style chunk codes как input; добавляет posting-layer поверх для retrieval. roadmap-label PR #29 (BinarySignature) — prefilter; SAHI — candidate generator + rerank.
- **ASMS**: работает на **document level**, не chunk level; roadmap-label PR #29 (BinarySignature) — chunk level. Разные granularity, не overlapping use cases.
- **MSBSE**: document-level multi-slot, не chunk-level single-code. Через hybrid B14 baseline (ASMS+slots) возможна future composition.

Все три могут co-exist с roadmap-label PR #29 (BinarySignature) в одном стеке: roadmap-label PR #29 (BinarySignature) остаётся первым filter, три техники — alternatives/additions для second-stage retrieval.

### §5.2. Relative to RFF-density ([`memory-routing-roadmap.md`](memory-routing-roadmap.md) §4)

RFF-density — pre-retrieval router в float embedding space (Sivertsen & Eidheim 2026). Это **routing layer** для выбора partitions.

SAHI и MSBSE работают в Hamming space, RFF в float space — different metrics, complementary:

- RFF: cheap partition routing (µs) → full retrieval inside selected partitions.
- SAHI: sub-linear Hamming-space retrieval без classes/partitions overhead.
- ASMS/MSBSE: document-level semantic representation → может заменить document-level float rerank step в pipeline.

RFF остаётся pre-filter; three techniques — кандидаты на replacement отдельных layer'ов retrieval pipeline.

Adaptive RAG ([`memory-routing-roadmap.md`](memory-routing-roadmap.md) §5) и DLP-style axes ([`memory-routing-roadmap.md`](memory-routing-roadmap.md) §6) сидят ещё выше — принимают решения «нужен ли retrieval» и «какие sections boost'ить» соответственно. Three techniques этого гайда — retrieval primitives, не routing decisions.

### §5.3. Relative to codebase-memory-mcp patterns ([`code-intelligence-roadmap.md`](code-intelligence-roadmap.md))

Pattern 1 — MinHash + LSH для candidate filter в Jaccard-on-shingles space. Pattern 2 — RotSQ/RaBitQ-style quantization. Pattern 3 — coverage graph.

MSBSE концептуально близок к Pattern 1 (тоже candidate filter, но Hamming-on-binary-embeddings вместо Jaccard-on-shingles). Автор MSBSE явно упоминает, что SAHI мог бы быть дополнительным coarse router для MSBSE; это **future cross-reference**, не текущая integration.

Code intelligence reference: PR #1 в roadmap-нумерации [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) = MinHash + LSH. Cross-link из Pattern 1 на MSBSE/SAHI как future Hamming-space filter.

### §5.4. Relative to memory architectures ([`memory-architectures-roadmap.md`](memory-architectures-roadmap.md))

Три техники этого гайда — **retrieval primitives**, не memory architectures. Они могут быть использованы **внутри** memory architectures (Mem0, Zep, A-MEM, RLM, etc.), но не конкурируют с этими architectures. Каждая architecture выбирает свои retrieval primitives; three techniques — candidates для этой selection.

Конкретно:

- **LongMemEval-style workloads** ([`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) §2) — ASMS author explicitly names LongMemEval/LongMemEval-V2 как agent memory benchmarks; это asymmetric-containment workload, идеальный fit для ASMS, потенциально fit для MSBSE.
- **Compiled-article workflows** ([`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) §4) — multi-topic documents с редкими facet'ами; MSBSE sweet spot.
- **Streaming event memory** ([`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) §5 cross-cutting axes) — ASMS monotone accumulator идеален для online add/delete.

### §5.5. Relative to retrieval techniques ([`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) §2)

Три техники этого гайда — **candidates внутри одной ячейки** retrieval typology: dense retrieval alternatives с compressed binary storage. Они не expanding typology, а filling-in существующей dense/Hamming cell с research-stage options.

Sparse retrieval (BM25, BM25F) и late interaction (ColBERT) уже documented в [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) §3-5; three techniques — orthogonal alternatives на dense side.

### §5.6. Relative to chunkers ([`chunkers-roadmap.md`](chunkers-roadmap.md))

Три техники оперируют chunk embeddings; chunking strategy определяет, что именно попадает в каждый encoder. Multi-slot MSBSE особенно чувствителен к chunking: редко встречающиеся chunks должны попадать в coverage loss (если их нет в батче, модель их не учится покрывать).

Chunkers roadmap уже включает "Two-Stage Indexing (Chunks + Synthetic Questions)" (§6) — chunk-as-query augmentation полезен для MSBSE training, но это shared concern, не unique dependency.

## §6. Implementation ladder

Все три техники находятся на **M3-research** уровне зрелости. Это означает:

- Нет planned delivery date.
- Нет planned PR lane.
- Нет planned DBI / contract / API.
- Нет benchmark на наших workloads (внутренние заметки используют свои hypothetic benchmarks).

Переход от M3-research к M4-candidate требует:

1. **Internal evaluation framework.** Reproduce experiment matrix автора (ASMS stages 1-7; MSBSE steps 1-12; SAHI baselines 0-5) на наших workloads (golden dataset из [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) Maturity Levels).
2. **Quality vs float baseline.** Метрики Recall@10, nDCG@10, MRR@10 vs float retrieval baseline; rare-facet split (≤ 1%, 1-5%, 5-10%, 10-25%, > 25%) как отдельный отчёт.
3. **Memory + latency Pareto.** Quality/memory/latency tradeoff на 100k / 1M / 10M document scales.
4. **Online update validation** (ASMS): add/delete state matches full rebuild.
5. **Production kill criteria check**: для ASMS — 10 kill criteria из §3.2.5; для MSBSE — 8 risks из §3.3.5; для SAHI — 7 ablations из §3.1.5.
6. **ADR proposal**: planning wave с конкретными DBI placement, contract boundary, integration tier.

Per-technique M-ladder (illustrative, NOT a commitment):

| Maturity | SAHI | ASMS | MSBSE / MSHDI |
|---|---|---|---|
| **M0** | Planned research | Planned research | Planned research |
| **M1** | Not planned | Not planned | Not planned |
| **M2** | Not planned | Not planned | Not planned |
| **M3-research** | Author experiments + repo ablation tables, internal bench script | Continuous prototype + sparse contributions + incremental tests | Float Set Transformer model + ISAB + PMA + slot head + soft binary continuation |
| **M4-candidate** (hypothetical future) | Possible Dense postings + bucketed layout + Hamming graph + flat-scan benchmark | Possible sparse uint16 accumulator + binary snapshot + inverted postings + MS MARCO ablation table | Possible hard binary fine-tune + MIH/HNSW backend + C++ prototype + locked benchmark |
| **M5-production** (hypothetical) | Possible stable contract, dual-read, integration with roadmap-label PR #29 (BinarySignature) | Possible encoder versioning + dedup correction + agent memory benchmarks | Possible Pareto frontier + production model selection + integration with RAG pipeline |

Эта таблица **не является commitment**; M4+ maturity требует explicit planning wave. Если ни одна из техник не проходит quality или memory target после M3-research, техника может быть отброшена (per авторским kill criteria).

## §7. References

### External public sources

> The following public citations are explicitly referenced in the three source notes. They are listed here for reader convenience; each is anchored to its specific use within the technique sections above. PR-numbers in this guide refer to **GitHub PR #N** in our repository, not roadmap-label "PR #N" in [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md).

- [arXiv:1904.08375](https://arxiv.org/abs/1904.08375) — Nogueira, Yang, Cho, Lin, "Document Expansion by Query Prediction" (2019). Cited in SAHI §3.1.4 as the "questions as index coordinates" foundation.
- [arXiv:1307.2982](https://arxiv.org/abs/1307.2982) — Norouzi, Punjani, Fleet, "Fast Exact Search in Hamming Space with Multi-Index Hashing" (2012/2014). Cited in SAHI §3.1.4 as exact kNN baseline for binary codes; also referenced in MSBSE §3.3.4 as flat-/indexed-search backend.
- [IMECS 2008 paper](https://www.iaeng.org/publication/IMECS2008/IMECS2008_pp437-445.pdf) — "New Distance Lower Bounds for Efficient Proximity Searching". Cited in SAHI §3.1.4 as triangle-inequality / pivot-search lower bound.
- [Springer 2013 chapter](https://dl.acm.org/doi/10.1007/978-3-642-41062-8_12) — Bustos, Sklar, "Extreme Pivots for Faster Metric Indexes". Cited in SAHI §3.1.4 as pivot-selection literature.
- [arXiv:1803.09065](https://arxiv.org/abs/1803.09065) — Tissier, Gravier, Habrard, "Document Binarization" (word-level GloVe context, 2018). Cited in [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) §6 (hypothesis for binary embedding quality).
- [Zenodo 20737147](https://zenodo.org/records/20737147) — Sivertsen & Eidheim 2026, RFF KDE Laplacian. Cited in [`memory-routing-roadmap.md`](memory-routing-roadmap.md) §4 as foundational RFF-density router.
- [arXiv:2403.14403](https://arxiv.org/abs/2403.14403) — Jeong et al. 2024, Adaptive-RAG. Cited in [`memory-routing-roadmap.md`](memory-routing-roadmap.md) §5.
- [ACL Anthology 2025.emnlp-main.439](https://aclanthology.org/2025.emnlp-main.439/) — Marina et al. 2025, LLM-Independent Adaptive RAG. Cited in [`memory-routing-roadmap.md`](memory-routing-roadmap.md) §5.

Field-name references in source notes (no specific identifiers intentionally added):

- Set Transformer, Deep Sets, PMA, ISAB, SAB — multi-set encoder literature; canonical references include Zaheer et al. 2017 (Deep Sets) and Lee et al. 2019 (Set Transformer).
- ColBERT (Khattab & Zaharia) — late interaction token-level multi-vector retrieval.
- Matryoshka Representation Learning — nested representations of varying bit-count.
- HashNet — deep hashing continuation training.
- Multiple-Instance Learning (MIL) field — bag-level supervision with instance-level aggregation.
- Neural Bloom Filter — trainable approximate membership query for compact set representations.
- MASNet-style monotone-and-separable set encoders — partial-order-preserving set representations.
- Hash-RAG — asymmetric binary retrieval for propositions.

### Internal notes

- [Source: internal note — no public source available. Path: tmp/Semantic Anchor Hamming Index.md] — SAHI research sketch by internal author. Source for §3.1 in this guide.
- [Source: internal note — no public source available. Path: tmp/Accumulative Semantic Membership Sketch.md] — ASMS research protocol by internal author. Source for §3.2 in this guide.
- [Source: internal note — no public source available. Path: tmp/Многослотовая бинарная семантическая сигнатура документа.md] — MSBSE/MSHDI research protocol by internal author. Source for §3.3 in this guide.

### Project guides

- [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) — roadmap-label PR #29 (BinarySignature); current binary surface.
- [`memory-routing-roadmap.md`](memory-routing-roadmap.md) — RFF-density (§4), Adaptive RAG (§5), DLP-style (§6).
- [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) — Pattern 1 MinHash+LSH, Pattern 2 RotSQ.
- [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) — typology of 13 memory architectures.
- [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) — Naive → Contextual → ... → RLM progression.
- [`chunkers-roadmap.md`](chunkers-roadmap.md) — chunking strategies feeding into three techniques.
- [`optimization-roadmap.md`](optimization-roadmap.md) — implementation ladder for binary surfaces, encoder registry, SIMD path.
- [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) — canonical memory profile specification.
- [`knowledge-base-roadmap.md`](knowledge-base-roadmap.md) — retrieval flow.
- [`compaction-roadmap.md`](compaction-roadmap.md) — job types, handoffs.
- [`research-reading-map.md`](research-reading-map.md) — research-note index.

## Research gate binding: rare-facet retrieval

ASMS, MSBSE and SAHI remain research candidates. Their comparison is owned by
the [Rare-Facet / Multi-Granularity Retrieval Gate](evaluation-roadmap.md#rare-facet--multi-granularity-retrieval-gate),
not by this technique catalogue alone.

The common experiment must use the same encoder split, leakage controls,
source revisions, total bit budget, candidate budget, decoded-byte budget,
latency procedure and exact source hydration. Rare-facet slices are required
because a document can match the broad topic while losing the small
decision-relevant detail that motivated the query.

The following semantic boundaries are mandatory:

- ASMS accumulation is a retrieval signal, not an evidence or authority
  accumulator. Repeated copies and derived summaries cannot create independent
  corroboration.
- ASMS deletion requires retained per-chunk contributions or a rebuild-safe
  source. Missing contributions require rebuild or an explicit dirty/fail-closed
  result.
- MSBSE slots are projections, not durable identities, independent evidence or
  lifecycle state. Slot matches do not change provenance.
- SAHI remains deferred until an equal-budget experiment shows a clear,
  reproducible advantage over existing MIH/R4 controls.

No result from this gate is a production readiness claim or a reason to add a
public C++ type, DBI or identity kind. A successful experiment would first
produce a versioned report and an explicit implementation decision.
