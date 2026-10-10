# Compression-is-Intelligence Roadmap

> C++17 compliance: кодовые сниппеты используют `const std::vector<T>&` вместо `std::span` и явные конструкторы вместо designated initializers. Этот гайд не содержит C++ API; это концептуальный слой, лежащий **под** всеми engineering-гайдами проекта, объясняющий, *что* сжатие должно сохранять, чтобы retrieval / memory / quantization / summarization были полезными на практике.

## §1. Purpose

Этот гайд существует как **концептуальный каркас** для всей compression-смежной работы в `agent-memory-cpp`. Он не описывает API, классы или MDBX-схемы — это философский слой, лежащий под engineering-гайдами и объясняющий, что «хорошо сжимать» означает для retrieval, памяти и summarization.

Cross-link на инженерные гайды, где это концептуальное framing применяется:

- [`optimization-roadmap.md`](optimization-roadmap.md) — vector / binary / ANN optimisation, encoder registry, SIMD dispatch, HammingTopK kernel.
- [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) — binarisation как compression layer; autoencoder vs LSH vs PQ; composite compression (MRL + INT8 / PQ / binary).
- [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) — typology of retrieval techniques; что «good retrieval preserves».
- [`usage-memory-models.md`](usage-memory-models.md) — выбор memory model (A-MEM, Karpathy, Mem0, Letta, Zep); что summarisation должна сохранять.
- [`usage-llm-wiki.md`](usage-llm-wiki.md) — LLM Wiki pattern (Karpathy / Cole Medin / OpenWiki); что synthesis layer сохраняет и теряет.
- [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) — спектр внешних memory-архитектур через призму compression framing.
- [`advanced-binary-techniques-roadmap.md`](advanced-binary-techniques-roadmap.md) — RotSQ, PQ residuals, multi-bit quantisation.

Позиция в иерархии: **«философский слой»** под engineering-гайдами. Цитаты из Shannon / 3Blue1Brown используются как engineering-метафора, а не как буквальное определение интеллекта.

Non-goals:

- Не вводить новые API или классы.
- Не каталогизировать compression codecs (это в [`optimization-roadmap.md`](optimization-roadmap.md) и [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md)).
- Не утверждать, что «compression is intelligence» в буквальном смысле — это overclaim.

## §2. Source attribution policy

Этот гайд синтезирует материал из нескольких источников. Цитаты следуют двухуровневому паттерну:

- `[Source: <public citation>]` — основная цитата на опубликованную работу, документацию проекта, ревизию репозитория или статью. При цитировании внешнего источника публичная ссылка авторитетна.
- `[Source: internal note — no public source available. Path: <path>]` — утверждение взято только из внутренних исследовательских заметок без эквивалентного публичного источника на момент написания. Требует дополнительной проверки.

Внутренние заметки — это discovery aids, а не авторитетные цитаты. Публичные roadmap-заявления цитируют публичный источник первым; внутренние заметки служат лишь маркером частного происхождения.

Этот гайд ссылается на публичное видео 3Blue1Brown (см. §8) и на внутренний concept-note в `ai-agent-playbook`. Где одно и то же утверждение присутствует в обоих, публичная ссылка идёт первой; внутренняя — как маркер provenance.

## §3. Prediction ↔ Compression equivalence

Шенноновская интуиция из 1948: распределение вероятностей по символам задаёт нижнюю границу средней длины кода. Prefix-free codes позволяют кодировать вероятные символы коротко, а редкие — длинно, сохраняя однозначную декодируемость. Математическая связь между предсказанием и сжатием:

```text
Если модель хорошо предсказывает следующий символ / токен,
она присваивает высокую вероятность правильному продолжению.

Если у нас есть вероятности,
мы можем кодировать вероятные продолжения коротко,
а редкие — длинно.

лучшее предсказание -> меньше surprise -> меньше средняя длина кода
хуже предсказание   -> больше surprise -> больше средняя длина кода
```

Cross-entropy loss, используемый в LLM pretraining, формально — это средняя стоимость кодирования данных истинного распределения `p` с кодом, построенным по модельному распределению `q`. Если модель уверенно предсказывает правильный токен, cross-entropy падает; если модель «удивляется» правильному токену — растёт.

В этом смысле **next-token prediction induces a compressor**: cross-entropy loss, используемый в pretraining, формально — это ожидаемая coding cost, и снижение cross-entropy соответствует более короткому expected code length. Модель учится не просто «угадывать слова», она учится строить компактную вероятностную модель языка, которая захватывает статистические регулярности корпуса. **Важная оговорка:** trained model сам по себе — не lossless compressed archive обучающего корпуса; без decoder (самой модели) невозможно восстановить исходные тексты. Модель хранит статистическую модель распределения, а не сами данные.

[Source: https://www.youtube.com/watch?v=l6DKRf-fAAM — 3Blue1Brown, *Reinventing Entropy | Compression is Intelligence Part 1*]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Compression is Intelligence — энтропия, сжатие и обучение моделей.md]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/resources/videos/Reinventing Entropy Compression is Intelligence Part 1 - l6DKRf-fAAM - расшифровка.md]

### §3.1. Operational intelligence vs general sense

Важное разграничение для нашего проекта. «Compression is intelligence» как метафора полезна, но для retrieval / memory summarization мы заботимся не о «general patterns», а об **operational details** — числах, командах, API constraints, версиях, исключениях, counter-arguments, provenance, каузальных связях. Эти детали обычно **редкие** (низкая вероятность в обучающем распределении), но **высокоинформативные** для принятия следующего решения.

Связь с entropy-интуицией:

```text
если chunk не уменьшает неопределённость ответа,
он является шумом для текущей задачи
```

Это условие по **relevance** (конкретному запросу), а не по raw entropy. Уточнения:

- **Useful chunk ≈ chunk that reduces uncertainty of the answer, conditional on the current query** (снижает uncertainty ответа при данном запросе).
- **Relevance ≈ mutual information with the target**, not raw rarity or standalone entropy.
- **High surprisal ≠ relevance; low surprisal ≠ noise**. Rare text может быть irrelevant noise (статистически редкое, но бесполезное); типовой mandatory constraint (версия протокола, обязательное ограничение) — low surprisal, но critical для ответа.

То есть high-entropy chunk (редкое содержание) — это *потенциально* деталь; low-entropy chunk (общий смысл) — *потенциально* шум. Но обе крайности требуют проверки на query-conditional relevance: rare content может быть irrelevant noise, а mandatory constraint — critical даже при низком surprisal.

## §4. Compression as engineering metaphor (not literal)

«Compression is intelligence» — полезная метафора, но **опасна** при буквальном прочтении. Шенноновский «perfect compression» (когда compressed bitstream неотличим от random noise) подразумевает, что система:

- замечает статистические регулярности;
- строит компактные модели;
- использует эти модели для предсказания;
- переносит предсказания на новые случаи.

Это **не** означает:

- наличие целей;
- проверку причинности;
- различение корреляции и объяснения;
- устойчивую память;
- понимание последствий действий.

Для AI-агентов compression — один слой, а не вся архитектура. Хороший компрессор ≠ AGI.

[Source: https://www.youtube.com/watch?v=l6DKRf-fAAM — 3Blue1Brown, *Reinventing Entropy | Compression is Intelligence Part 1*]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Compression is Intelligence — энтропия, сжатие и обучение моделей.md]

### §4.1. Три импликации для нашего стека

**Для RAG.** Хороший retrieval не просто сокращает текст — он сохраняет те части, которые меняют ответ:

- точные числа;
- команды;
- API constraints;
- версии;
- исключения;
- контраргументы;
- provenance;
- причинные связи.

Тематически похожий, но практически бесполезный chunk — это failure mode retrieval-слоя.

**Для memory summarization.** Сжатие вредно, если оно оставляет общий смысл, но теряет операционные детали. Сравните:

```text
"пользователь настраивал TTS"
```

Это слабое summary — оно не меняет никакого будущего решения.

```text
"Qwen3-TTS с compile занял около 9.5 GB VRAM;
без compile около 5.2 GB; вместе с kira-q3 и Whisper
на 10 GB GPU не помещается"
```

Это полезная память: следующее решение о том, какие модели ставить на ту же GPU, принимается исходя из этих чисел, а не из общего «TTS».

**Для memory vs LLM context.** Внешняя память должна сохранять то, что модель иначе была бы вынуждена re-derive из контекста. Если заметка не содержит ничего такого, что модель сама бы не вывела из prompt, — это не память, а шум.

## §5. Seven check-questions for compression quality

Из источника `ai-agent-playbook/concepts/llm-research/Compression is Intelligence — энтропия, сжатие и обучение моделей.md`. Семь вопросов для оценки качества сжатия заметки / summary / chunk:

1. **Изменит ли удаление этой детали будущее решение?** Если да — деталь важна, не сжимать.
2. **Является ли это числом, командой, ограничением, версией или исключением?** Если да — это operational detail, сохранить дословно.
3. **Это факт источника или интерпретация агента?** Разделение критично для provenance и auditability.
4. **Можно ли восстановить sequence действий по заметке?** Если нет — потеряли важный workflow-сигнал.
5. **Сохранён ли provenance?** Ссылка на источник / video ID / arXiv ID / commit hash — обязательна.
6. **Может ли другой агент по этой заметке выполнить задачу без повторного просмотра источника?** Reproducibility test.
7. **Где риск потери смысла выше: в summary, в embedding retrieval или в ручной интерпретации?** Calibrate compression method accordingly.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Compression is Intelligence — энтропия, сжатие и обучение моделей.md]

Эти вопросы применяются не только к memory summarization, но и к retrieval chunks, binary embedding bit allocation, и LLM Wiki article synthesis.

## §6. Application matrix — how conceptual framing maps to engineering choices

Как «7 check-questions» и «operational > general» соотносятся с engineering-выборами в нашем проекте:

| Engineering choice | Какие check-questions отвечает | Какие check-questions пропускает |
|---|---|---|
| **Binary embeddings** (`binary_bucket_index`, `BinarySignature`) | Q1 (does detail change decision — bit allocation keeps high-information bits), Q2 (numbers/commands encoded). Q4 (sequence) and Q5 (provenance) preserved at unit level, not at bit level. | Q3 (source vs interpretation), Q6 (another agent), Q7 (loss source). Bit-level encoding has no semantic guarantee beyond statistical preservation. |
| **RFF routing** (`RandomFourierFeatures` density classifier in [`memory-routing-roadmap.md`](memory-routing-roadmap.md)) | Q1 (cheap pre-filter sorts units by decision-relevant categories). | Q2-Q7. RFF is a coarse pre-filter; the actual memory units still need to be semantically evaluated downstream. |
| **Vector store quantisation** (PQ, INT8, scalar — выбор Vector DB см. в [`vector-db-engineering-roadmap.md`](vector-db-engineering-roadmap.md)) | Q1 (compressed candidates still preserve similarity ranking well enough to choose top-K). Q2 (numbers survive quantisation as centroids). | Q3-Q6. Quantisation is lossy on the vector side; the source text remains authoritative via chunk payload. |
| **Memory summarisation** (LLM Wiki `SummaryPromotionJob`, A-MEM `Memory Evolution`) | Q1-Q7. Это explicit compression step, и все семь вопросов должны быть пройдены. | Самый рискованный слой: агрессивное summary может потерять Q2/Q4/Q6/Q7 одновременно. См. §4.1 про operational vs general. |
| **Retrieval RAGAS scoring** (retrieval quality evaluation) | Q1 (does retrieved context change answer), Q6 (would another agent produce same answer). Q5 (provenance) if answer attribution is logged. | Q3 (source vs interpretation). Q7 (which stage loses most) requires decomposition beyond RAGAS. |
| **BM25 / BM-score lexical retrieval** | Q1, Q2, Q5. Exact token match preserves commands, version strings, error codes. Q4 (sequence) reconstructible from chunk order. | Q3, Q6, Q7. Lexical retrieval is statistical over tokens, not over semantics. |

Из этой матрицы видно, что **memory summarisation — единственный слой, где все семь вопросов обязательны**. Остальные слои — narrow pre-filters или candidate selectors; они делегируют семантическую ответственность вышестоящему слою или external storage.

## §7. Limitations

Анти-hype секция. Что «compression is intelligence» **не** даёт нашему проекту:

- **Не объясняет causality.** Шенноновский compression не различает причину и следствие; LLM pretraining — тоже. Retrieval может вернуть статистически похожий chunk, который **коррелирует**, но не **причинно связан** с запросом.
- **Не гарантирует истинность.** Низкая cross-entropy на training corpus ≠ истинное знание; модель может компактно кодировать ложные, но статистически регулярные паттерны.
- **Не покрывает exceptions.** Хороший статистический компрессор склонен терять редкие исключения. В retrieval это проявляется как «common case worked, edge case failed».
- **Не заменяет verification / tool use / durable memory.** Compression — слой, не архитектура.

Из этого следует, что:

- Memory summarisation должна сохранять operational details (см. §4.1) даже если они редкие — иначе мы потеряем именно те детали, ради которых summarisation существует.
- Retrieval должен явно хранить provenance, чтобы можно было проверить, откуда пришёл chunk, и откатиться к источнику при неуверенности.
- Binary quantisation и PQ — lossy codecs; они не должны быть **последним** слоем перед answer. Quality-sensitive rerank на float rerank или оригинальном chunk остаётся обязательным (см. [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) §3.4, §5, §8).

[Source: https://www.youtube.com/watch?v=l6DKRf-fAAM — 3Blue1Brown, *Reinventing Entropy | Compression is Intelligence Part 1*]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Compression is Intelligence — энтропия, сжатие и обучение моделей.md]

## §8. References

### 8.1. Public sources

- **3Blue1Brown (Grant Sanderson, 2026). "Reinventing Entropy | Compression is Intelligence Part 1."** YouTube video `l6DKRf-fAAM`. URL: <https://www.youtube.com/watch?v=l6DKRf-fAAM>. Series aims to lay down mathematical fundamentals to assess what the phrase "compression is intelligence" really means. Discusses prefix-free codes, entropy as average information per symbol, Shannon's noiseless coding theorem, and the equivalence of prediction and compression.

### 8.2. Internal notes (ai-agent-playbook)

- `ai-agent-playbook/concepts/llm-research/Compression is Intelligence — энтропия, сжатие и обучение моделей.md` (internal note) — concept-summary на основе видео 3Blue1Brown: prefix-free codes, ASCII/robot examples, entropy / cross-entropy в LLM pretraining, "compression as engineering metaphor" оговорки, семь check-questions для compression quality заметок.
- `ai-agent-playbook/resources/videos/Reinventing Entropy Compression is Intelligence Part 1 - l6DKRf-fAAM - расшифровка.md` (internal note) — полная очищенная англоязычная расшифровка видео `l6DKRf-fAAM` (32:19), сохранённая как постоянная resource-заметка vault.

### 8.3. In-house guides

- [`optimization-roadmap.md`](optimization-roadmap.md) — vector math baseline, optional Eigen adapter, SIMD dispatch (SSE4.2 / AVX2 / AVX-512), HammingTopK kernel design, encoder registry, dense index modes.
- [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) — binarisation landscape (sign / autoencoder / LSH / PQ), per-bit-size quality vs storage tradeoff, hybrid binary + dense, composite compression (MRL + INT8 / PQ / binary).
- [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) — typology of retrieval techniques (Naive / Advanced / Hybrid / Contextual / Graph / Fusion / Adaptive / Agentic / RLM); hybrid not normative by default.
- [`memory-routing-roadmap.md`](memory-routing-roadmap.md) — pre-retrieval routing layer (RFF density classifier, intent router), sits "до" binary embedding filter.
- [`usage-memory-models.md`](usage-memory-models.md) — operator guide по выбору memory architecture (Karpathy / A-MEM / lifemodel / NOUZ / Mem0 / Letta / Zep / Memoria / Блок фактов / СВИНОПАС / J-Space / RLM).
- [`usage-llm-wiki.md`](usage-llm-wiki.md) — operator guide по LLM Wiki pattern на `agent-memory-cpp` (Karpathy / Cole Medin / OpenWiki / Second Brain variants).
- [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) — 13+ внешних memory-архитектур через единый comparison framework.
- [`advanced-binary-techniques-roadmap.md`](advanced-binary-techniques-roadmap.md) — RotSQ codec, PQ residuals, multi-bit quantisation, multi-stage binary pipelines.

## Decision-relevant retention

Compression is useful only when it preserves the information needed for the
next declared decision. This is stricter than semantic similarity and different
from retrieval recall. A compressor can preserve the topic while losing a
threshold, exception, sequence dependency, conflict or evidence root.

The engineering contract is owned by the
[Decision-Relevant Retention Gate](evaluation-roadmap.md#decision-relevant-retention-gate).
Every compression or summary comparison should use the same source revision,
fixed decision probes and declared total byte/token budget. The minimum probe
families are:

- numbers, units, identifiers, versions and thresholds;
- commands, API constraints, paths and configuration keys;
- goals, constraints, exclusions and exceptions;
- ordered steps, dependencies and pending work;
- conflicts, alternatives and unknowns;
- provenance, evidence roots and source drill-down;
- source statements versus derived inference.

The gate requires an exact or key-based probe subset. LLM-based judging can add
qualitative information, but it cannot be the only evidence of retention.

Compression reports classify failures as detail, chronology, exception,
provenance, source/inference conflation or replay loss. Missing replay inputs
are reported as unavailable or PENDING_SOURCE_REPLAY; they are never silently
replaced with the current source or treated as a successful reconstruction.

A summary or compressed representation remains a derived projection. It does
not become an independent observation, a new authority source or a replacement
for the retained source merely because it is shorter or frequently retrieved.
Decision-relevant retention is therefore a condition for a future lifecycle
decision, not a promise that every compression method is lossless.
