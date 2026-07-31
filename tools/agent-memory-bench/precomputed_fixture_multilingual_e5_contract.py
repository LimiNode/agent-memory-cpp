"""Bilingual text and qrels contract for the frozen multilingual E5 fixture."""

from __future__ import annotations


CORPUS = [
    {
        "id": "doc:binary-filter-ru",
        "title": "Бинарный кандидатный фильтр",
        "text": (
            "Бинарные сигнатуры сначала выбирают кандидатов по расстоянию Хэмминга, "
            "а затем точный rerank по float-векторам восстанавливает итоговый порядок."
        ),
    },
    {
        "id": "doc:binary-filter-en",
        "title": "Binary candidate filter",
        "text": (
            "Binary signatures select a Hamming candidate set first, then exact "
            "float-vector reranking restores the final retrieval order."
        ),
    },
    {
        "id": "doc:artifact-provenance-ru",
        "title": "Происхождение embedding artifact",
        "text": (
            "Замороженный artifact эмбеддингов хранит revision модели и токенизатора, "
            "идентичность генератора, qrels, dataset и SHA-256 хэши содержимого."
        ),
    },
    {
        "id": "doc:artifact-provenance-en",
        "title": "Embedding artifact provenance",
        "text": (
            "A frozen embedding artifact records model and tokenizer revisions, "
            "generator identity, qrels, dataset identity, and SHA-256 content hashes."
        ),
    },
    {
        "id": "doc:encrypted-memory-ru",
        "title": "Шифрование памяти",
        "text": (
            "Локальное хранилище памяти защищают AEAD-шифрованием, KDF для ключа и "
            "атомарной заменой файла, чтобы не оставить частично записанные данные."
        ),
    },
    {
        "id": "doc:encrypted-memory-en",
        "title": "Encrypted memory storage",
        "text": (
            "Local memory storage uses AEAD encryption, a key derivation function, "
            "and atomic file replacement to protect data at rest."
        ),
    },
    {
        "id": "doc:memory-routing-ru",
        "title": "Маршрутизация слоёв памяти",
        "text": (
            "Urgency-aware routing выбирает short, medium, long и base memory tiers, "
            "чтобы срочный чат не ждал дорогого глубокого retrieval."
        ),
    },
    {
        "id": "doc:memory-routing-en",
        "title": "Memory tier routing",
        "text": (
            "Urgency-aware routing selects short, medium, long, and base memory tiers "
            "so an urgent chat does not wait for expensive deep retrieval."
        ),
    },
    {
        "id": "doc:graded-qrels-ru",
        "title": "Градуированные qrels",
        "text": (
            "Градуированные qrels различают сильную и частичную релевантность; nDCG "
            "измеряет не только факт нахождения, но и порядок документов."
        ),
    },
    {
        "id": "doc:graded-qrels-en",
        "title": "Graded qrels",
        "text": (
            "Graded qrels distinguish highly and partially relevant records; nDCG "
            "measures both the retrieved items and their ranking order."
        ),
    },
    {
        "id": "doc:e5-prefixes-ru",
        "title": "Префиксы E5",
        "text": (
            "Для retrieval-моделей E5 запросы кодируются с префиксом query:, а "
            "документы — с префиксом passage:, иначе сравнение нарушает контракт модели."
        ),
    },
    {
        "id": "doc:e5-prefixes-en",
        "title": "E5 retrieval prefixes",
        "text": (
            "E5 retrieval models encode user requests with the query: prefix and "
            "documents with the passage: prefix so the two embedding roles match."
        ),
    },
]


QUERIES = [
    {
        "id": "q:binary-rerank-ru",
        "text": "как бинарный фильтр кандидатов использует rerank по float векторам",
        "query_type": "semantic",
        "limit": 10,
    },
    {
        "id": "q:artifact-hashes-en",
        "text": "which hashes and revisions make a frozen embedding artifact reproducible",
        "query_type": "semantic",
        "limit": 10,
    },
    {
        "id": "q:memory-encryption-ru",
        "text": "как защитить локальную память AEAD шифрованием и KDF",
        "query_type": "semantic",
        "limit": 10,
    },
    {
        "id": "q:urgent-memory-en",
        "text": "which memory tiers should urgent chat retrieval use",
        "query_type": "semantic",
        "limit": 10,
    },
    {
        "id": "q:graded-evaluation-ru",
        "text": "зачем нужны graded qrels и что измеряет nDCG",
        "query_type": "semantic",
        "limit": 10,
    },
    {
        "id": "q:e5-asymmetric-en",
        "text": "how should E5 encode query and document text for retrieval",
        "query_type": "semantic",
        "limit": 10,
    },
]


JUDGMENTS = [
    {"query_id": "q:binary-rerank-ru", "item_id": "doc:binary-filter-ru", "relevance_grade": 3},
    {"query_id": "q:binary-rerank-ru", "item_id": "doc:binary-filter-en", "relevance_grade": 2},
    {"query_id": "q:artifact-hashes-en", "item_id": "doc:artifact-provenance-en", "relevance_grade": 3},
    {"query_id": "q:artifact-hashes-en", "item_id": "doc:artifact-provenance-ru", "relevance_grade": 2},
    {"query_id": "q:memory-encryption-ru", "item_id": "doc:encrypted-memory-ru", "relevance_grade": 3},
    {"query_id": "q:memory-encryption-ru", "item_id": "doc:encrypted-memory-en", "relevance_grade": 2},
    {"query_id": "q:urgent-memory-en", "item_id": "doc:memory-routing-en", "relevance_grade": 3},
    {"query_id": "q:urgent-memory-en", "item_id": "doc:memory-routing-ru", "relevance_grade": 2},
    {"query_id": "q:graded-evaluation-ru", "item_id": "doc:graded-qrels-ru", "relevance_grade": 3},
    {"query_id": "q:graded-evaluation-ru", "item_id": "doc:graded-qrels-en", "relevance_grade": 2},
    {"query_id": "q:e5-asymmetric-en", "item_id": "doc:e5-prefixes-en", "relevance_grade": 3},
    {"query_id": "q:e5-asymmetric-en", "item_id": "doc:e5-prefixes-ru", "relevance_grade": 2},
]
