# Research Batch R5 — результат второго агента

Дата: 20 ноября 2026, Москва. База интеграционной проверки:
`bfa01a22d4091bb5c5f7c17606199fda6342b790`.

Финальная сверка GitHub: 19 ноября 2026, 22:35 UTC (20 ноября, 01:35 Москва).
Все 18 запусков CI и упаковки завершились успешно на указанных HEAD.

Все девять PR открыты и остаются Draft. GitHub merge, Ready, tag, release и
production activation не выполнялись. Изменений в `src/` и `external/` нет.
Рабочее дерево первого агента и пять пользовательских RTHQ-файлов не изменены.

## 1. PR, exact HEAD и проверки

Все PR направлены в `main`, основаны на указанной базе и не имеют конфликтов
слияния. Незакрытых review threads нет. Это не означает независимый APPROVED review.

| PR | HEAD | Exact-head CI | Packaging / installed consumers |
| --- | --- | --- | --- |
| [#633](https://github.com/LimiNode/agent-memory-cpp/pull/633) | `557e1e3e4c6841cf4340361e38d4dfc11e672059` | [44606993854](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44606993854), SUCCESS | [44606993109](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44606993109), SUCCESS |
| [#635](https://github.com/LimiNode/agent-memory-cpp/pull/635) | `1830db88acfe4bad5615ca82c0727e8b4d9b451b` | [44611271377](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44611271377), SUCCESS | [44611271225](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44611271225), SUCCESS |
| [#638](https://github.com/LimiNode/agent-memory-cpp/pull/638) | `21f08f9c8555f2c549b6721bd6cd93463460bcad` | [44615234645](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44615234645), SUCCESS | [44615235459](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44615235459), SUCCESS |
| [#640](https://github.com/LimiNode/agent-memory-cpp/pull/640) | `1d7254eb008136e5e4ae15f2ed4ed7b04a4d2445` | [44624646273](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44624646273), SUCCESS | [44624646600](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44624646600), SUCCESS |
| [#641](https://github.com/LimiNode/agent-memory-cpp/pull/641) | `6597ed38a40acd63acb85c0116a3a387018dd91e` | [44615249171](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44615249171), SUCCESS | [44615249184](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44615249184), SUCCESS |
| [#642](https://github.com/LimiNode/agent-memory-cpp/pull/642) | `f829fcaef7d9e1070bd419b2c892b76d50765941` | [44607150621](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44607150621), SUCCESS | [44607150424](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44607150424), SUCCESS |
| [#664](https://github.com/LimiNode/agent-memory-cpp/pull/664), vector / E-means | `ad62c6dbf5fd8f14ef8d2f1eb745d00d652d17fa` | [44620290339](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44620290339), SUCCESS | [44620290212](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44620290212), SUCCESS |
| [#665](https://github.com/LimiNode/agent-memory-cpp/pull/665), lexical top-K | `59ce09c3e18feff33fc7d2a2292eb11d53bb3b43` | [44617773367](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44617773367), SUCCESS | [44617773277](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44617773277), SUCCESS |
| [#666](https://github.com/LimiNode/agent-memory-cpp/pull/666), matched admission | `b688cd237d5ac1034cc5905fe393d3bd6cab8c42` | [44623541752](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44623541752), SUCCESS | [44623541772](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44623541772), SUCCESS |

## 2. Что исправлено

- Исходные исследовательские PR синхронизированы с `main@bfa01a22` в отдельных
  worktrees. Сохранены независимые изменения и исторические receipts.
- #635: numerical-frontier job включён в обязательный общий CI gate.
- #638/#641: исправлена доступность CPU E-means; исторический Faiss 1.15.0 не
  выдаётся за состояние Faiss 1.16.0. Старый E-means v1 помечен
  `INVALID_FOR_COMPARISON`; его неверные сравнительные выводы отозваны.
- #664: реальный `ExactVectorIndex`, независимый Decimal oracle и настоящий
  `NumpyEmeans`; исправлены требования чистой среды CI и выгрузка скрытых артефактов.
- #665: три source-bound варианта селектора внутри фактического поиска,
  правильные generated-header guards и условный `noexcept` по свойствам actual Hit.
- #666: одинаковый `retrieve()` для linear/sorted/hash; lookup строится внутри
  запроса. Основной таймер исключает подготовку fixture/engine, но включает
  поиск, reranker, допуск и формирование ответа. Более широкий таймер отделён.
  Обязательны C++17/Release/toolchain/platform/processor/pointer width;
  CI требует GNU/Linux или MSVC/Windows. Добавлены 10 отрицательных проверок
  окружения и отрицательный контроль противоречащих интервалов времени.
- #666: исправлена YAML-команда CTest. Отступ ранее отделял фильтр от команды
  и запускал все 334 теста при сборке только исследовательской цели.
  На конечном HEAD выполняются ровно три целевых теста, оба platform jobs PASS.

## 3. Lexical top-K: полный поиск, не только селектор

Источник: конечный Ubuntu artifact #665, expected HEAD `59ce09c3...`, compiled
checkout `a3aa86205dc60f5ec91fd8225bb61e6258da8ef6`. Receipt SHA-256:
`53ff4452c57bd4c7e0be151d7ba82ad5d36e0f1b24527648a664b2f111929cbf`.
Windows receipt проверен отдельно, включая CRLF source bytes и sidecar:
`65b5efad1556f0eed6e446d862cab2451bb049a7fa0c8a859c466a20b97370db`.

Медиана восьми измерений, мс; N=65 536, фиксированный синтетический корпус:

| Случай | Full sort | Partial sort | Nth + prefix sort | Bounded heap |
| --- | ---: | ---: | ---: | ---: |
| Mixed, K=16 | 34.2986 | 23.5963 | 24.1733 | 23.6692 |
| Ties, K=52 427 из 52 428 кандидатов | 20.2411 | 30.3102 | 21.1344 | 34.7218 |

Это полный `ExactLexicalIndex::search()`: оценка, фильтрация, метаданные,
выбор и формирование результата. Построение индекса исключено. Старое
примерно 27-кратное ускорение kernel-only не перенесено на полный поиск:
здесь выигрыш при малом K около 1.45 раза, при K около N возможен проигрыш.
Проверены 16 случаев, 1024 измерения, три CTest, 18 validator controls и три
generator controls. Решение: `CONDITIONAL`, production defaults — без изменений.

## 4. Reranker admission: область окупаемости

Исторический clean-source MSVC receipt привязан к
`df17946cb6cbad36f0da25caa6823dc67d3f41d7`; его данные не переписаны после
validator/CI corrective. Основной таймер — только `retrieve()`, включая
построение lookup, проверки и материализацию; подготовка fixture исключена.
Запись `last_query = query` внутри fixture search включена одинаково во все arms.

| Кандидаты / дополнительные metadata keys | Linear p50, мс | Sorted p50, мс | Hash p50, мс |
| --- | ---: | ---: | ---: |
| 10 / 2 | 0.0340 | 0.0393 | 0.0357 |
| 500 / 2 | 4.0972 | 3.0347 | 2.6965 |
| 2000 / 2 | 44.2335 | 13.7050 | 11.4774 |
| 2000 / 32 | 102.9341 | 75.3033 | 71.9109 |

Первое наблюдаемое пересечение средних, не устойчивый production threshold:

| Metadata keys | Sorted | Hash |
| ---: | ---: | ---: |
| 2 | 500 кандидатов | 50 кандидатов |
| 8 | 500 | 100 |
| 32 | 100 | 100 |

18 случаев, 648 retained query samples, 11 fail-closed controls на arm.
Положительные результаты совпадают с неизменённым production linear engine.
Отклоняются foreign chunk/resource, section/metadata/tier/enrichment mutations,
duplicates, NaN/±infinity и oversized output. Решение: `CONDITIONAL` для
дальнейшей проверки больших наборов; production — `KEEP CURRENT`.

В конечном CI expected HEAD — `b688cd23...`, фактический compiled merge checkout —
`48163807a5526c5a75b66392442a2cc3f563d30b`. Оба receipts повторно проверены
после скачивания, включая source/generated SHA, sidecar и compiler/platform.

## 5. Exact-vector arithmetic

Текущий CI #664 явно выполняет checkout PR HEAD: tested source, expected HEAD
и compiled checkout равны `ad62c6dbf5fd8f14ef8d2f1eb745d00d652d17fa`.

Production cosine использует Neumaier compensated double dot и устойчивые
double inverse norms, а не простой последовательный FP64 accumulator.
Oracle: Python Decimal, precision 100, exact FP32 inputs, округление в FP32
перед сравнением ранжирования. `long double` и Boost в production не добавлены.

Шесть correctness fixtures, десять timing cases, семь exception controls и
девять мутаций evidence прошли на настоящих Ubuntu/GNU и Windows/MSVC.
Максимальная наблюдённая абсолютная ошибка FP32-rounded oracle:
cosine 0; dot `4.76837158203125e-7`; negative squared L2 `7.62939453125e-6`.

Пример Ubuntu, N=65 536, d=32, K=10, batch из четырёх фиксированных queries:
полный поиск p50 37.5316 мс scalar / 37.0892 мс auto. Отдельное arithmetic-only
ядро: Neumaier 27.0758 мс, sequential FP64 5.7445 мс, FP32 4.4260 мс.
Последние два не являются равноценными быстрыми индексами: FP64 теряет член
в `2^120 + 1 - 2^120`, FP32 дополнительно проваливает tiny cosine.
Метка AVX2 не означает SIMD-накопление cosine numerator. Решение: `KEEP CURRENT`.

## 6. Реальный CPU Lloyd / E-means

Числа локальной таблицы ниже сохранены в receipt для чистого
`6272e7b230f7dbbc5d2b235614b9d5badb95ea7e`; tested/expected/actual checkout
в нём совпадают. Конечный CI на `ad62c6db...` исполнен и проверен отдельно;
старые timings не переименованы в результаты нового HEAD.

Faiss 1.16.0, NumPy 2.4.6, Python 3.11.9, отдельная venv, один поток.
Настоящий `faiss.contrib.e_means.NumpyEmeans`, не переименованный Lloyd.
Синтетические disjoint splits: 4096 train / 4096 base / 128 query / 1024 heldout,
d=16, k=8; query и heldout не попадают в обучение.

| Метод | Final train MSE | Heldout MSE | Local training p50, мс | Native 1-probe recall@10 / @100 | Serialized bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Lloyd | 8.111261 | 8.872920 | 4.49 | 0.953125 / 0.935234 | 295627 |
| E-means | 8.110503 | 8.870321 | 111.84 | 0.950000 / 0.935078 | 295627 |

Оба метода получают полные training rows и одинаковую инициализацию.
Измеренный assignment-work budget — 40960 row assignments, 327680 centroid
distance evaluations. Это не равенство update work или wall-clock.
Native IVF `ndis` и latency измеряются отдельно от capped-scan Python proxy.
При одинаковом proxy cap 512 recall@10/@100 у обоих 0.847656/0.858828,
при 1024 — 0.985156/0.982266; при полном просмотре — 1/1.
Тринадцать отрицательных контролей прошли; центроиды устанавливаются без retrain.
Решение: `DEFER` — smoke не показывает существенного качественного преимущества.
Это не отрицательный вердикт всему семейству E-means и не реальный R4 benchmark.
O-means — `NOT_EXECUTED`; production IVF/native serving не добавлены.

## 7. Registry и итоговые решения

#640 сохраняет `source_commit=e34a59a19abc4346cd541bd0ce139df098eb011e`
как audit snapshot. Новый integration baseline обозначен отдельным полем.
Добавлен ledger четырёх экспериментальных линий и семи исполненных runs:
точные source/checkout, owner PR, receipt и sidecar, byte hashes, timing scope,
ограничения и решения. Старые inventory statuses / `EVIDENCE_PENDING` сохранены.
Ничего не повышено до `PRIMARY` из-за наличия README/CI/design note.
Validator и self-test проходят; 14 прежних и шесть ledger negative controls
сохранены. Этот validator проверяет registry, не подменяет владельцев raw evidence.

| Кандидат | Решение |
| --- | --- |
| Универсальная замена полного lexical sort | Не принята; `CONDITIONAL` только для последующей проверки режимов N/K |
| Sorted/hash admission | `CONDITIONAL`; production defaults `KEEP CURRENT` |
| Текущий compensated cosine | `KEEP CURRENT` |
| Упрощение до sequential FP64 / FP32 | Не принимать: нарушается численный контракт fixtures |
| E-means / native IVF serving | `DEFER` до представительных данных и доказанного выигрыша |
| Production ADOPT | Нет |

## 8. Совмещённая проверка

Локальная, не опубликованная интеграционная ветка:
`preflight/r5-research-combined-v2`, SHA
`9c55e8aa5e930d40978d66af06a0cecb86b6abf1`.
Содержит текущие изменения всех девяти PR с сохранением независимых scopes.

- Все шесть исследовательских C++ targets собраны в Release/C++17.
- 11/11 целевых CTest PASS, включая реальное выполнение Decimal oracle.
- Полные lexical и admission matrices, source/sidecar validation PASS.
- E-means выполнен заново на exact combined source; 13 controls PASS.
- YAML audit: 23 уникальных jobs, 41 benchmark target, 342 CTests;
  нет дубликатов, циклов needs или потерь пяти обязательных новых/исправленных gates.
- `JSON=OFF / MDBX=OFF` static library configure/build PASS.
- Упаковка и installed consumers проверяются exact-head workflows выше;
  локальный combined build не выдаётся за дополнительный installed-consumer run.
- `git diff --check` PASS; production/external diff пуст.

Локальные combined receipts находятся рядом с этим отчётом. CTest-generated
vector receipt — в `build-r5-combined-v2` интеграционного worktree.
Исторические опубликованные receipts сохранены в исходных владельцах PR.

## 9. Что ещё не закрыто

R5 нельзя объявлять полностью закрытым по всем первоначальным требованиям:

- R5.1: подробный пофазный профиль tokenization/candidates/scoring/filtering/
  selection/materialization — `NOT_MEASURED`; kernel нельзя вычесть из search
  и выдать разницу за остальные фазы. Токенизация находится вне данного index call.
- Peak RSS не измерен. Allocation counts/bytes — не peak/retained memory.
- Authorization, tenant isolation и revision freshness на уровне продукта —
  `NOT_EXERCISED`; metadata equality в индексе этих гарантий не доказывает.
- Синтетический корпус с короткими tokens и одним filter не представляет
  производственное распределение запросов. Перед opt-in implementation нужны
  отдельный stage-profile, peak-memory и представительная contract qualification.
- Представительного frozen R4 vector corpus для quality conclusions в этом
  батче нет. E-means вывод ограничен synthetic smoke; portable native replay
  и serving требуют отдельного положительного основания и отдельного Draft PR.
- Повторные samples не являются независимыми запросами и не подтверждают p99.

Новые production PR, merge или activation по этим данным не выполняются.
Следующий безопасный шаг — независимое ревью подготовленных Draft PR и закрытие
перечисленных измерительных пробелов, а не автоматическая замена defaults.
