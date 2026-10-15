#!/usr/bin/env python3
"""Small dependency-free runner for the lexical research protocol.

This is a research harness, not a replacement for the C++ lexical index.  It
uses the same BM25 defaults recorded by the project roadmap and emits a
source-bound, compact JSON receipt that can be compared with later scorers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
CAMEL_RE_1 = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
CAMEL_RE_2 = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")


def tokenize(text: str) -> list[str]:
    """Approximate the code-aware StandardTokenizer boundary for the fixture."""
    expanded = CAMEL_RE_2.sub(" ", CAMEL_RE_1.sub(" ", text))
    return [part.casefold() for part in TOKEN_RE.findall(expanded)]


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_revision() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def validate_dataset(dataset: dict[str, Any]) -> None:
    for field in ("dataset_name", "corpus", "queries", "qrels"):
        if field not in dataset:
            raise ValueError(f"dataset is missing '{field}'")
    if not isinstance(dataset["corpus"], list) or not dataset["corpus"]:
        raise ValueError("corpus must be a non-empty array")
    if not isinstance(dataset["queries"], list) or not dataset["queries"]:
        raise ValueError("queries must be a non-empty array")
    corpus_ids = [item.get("id") for item in dataset["corpus"]]
    query_ids = [item.get("id") for item in dataset["queries"]]
    if any(not isinstance(value, str) or not value for value in corpus_ids):
        raise ValueError("every corpus item needs a non-empty string id")
    if len(set(corpus_ids)) != len(corpus_ids):
        raise ValueError("corpus ids must be unique")
    if any(not isinstance(value, str) or not value for value in query_ids):
        raise ValueError("every query needs a non-empty string id")
    if len(set(query_ids)) != len(query_ids):
        raise ValueError("query ids must be unique")
    known = set(corpus_ids)
    known_queries = set(query_ids)
    for judgment in dataset["qrels"]:
        if judgment.get("query_id") not in known_queries:
            raise ValueError("qrel references an unknown query")
        if judgment.get("doc_id") not in known:
            raise ValueError("qrel references an unknown corpus item")
        if not isinstance(judgment.get("grade"), int) or judgment["grade"] < 0:
            raise ValueError("qrel grade must be a non-negative integer")


def score_bm25(dataset: dict[str, Any], query: str, limit: int) -> list[dict[str, Any]]:
    k1 = 1.5
    b = 0.75
    documents = dataset["corpus"]
    tokenized = {item["id"]: tokenize(item["text"]) for item in documents}
    lengths = {doc_id: len(tokens) for doc_id, tokens in tokenized.items()}
    average_length = sum(lengths.values()) / len(lengths)
    query_terms = tokenize(query)
    document_frequency: dict[str, int] = {}
    for tokens in tokenized.values():
        for term in set(tokens):
            document_frequency[term] = document_frequency.get(term, 0) + 1
    scores: list[dict[str, Any]] = []
    document_count = len(documents)
    for item in documents:
        terms = tokenized[item["id"]]
        frequencies: dict[str, int] = {}
        for term in terms:
            frequencies[term] = frequencies.get(term, 0) + 1
        score = 0.0
        for term in query_terms:
            df = document_frequency.get(term, 0)
            if not df:
                continue
            idf = math.log(1.0 + (document_count - df + 0.5) / (df + 0.5))
            tf = frequencies.get(term, 0)
            if not tf:
                continue
            normalization = 1.0 - b + b * lengths[item["id"]] / average_length
            score += idf * (tf * (k1 + 1.0)) / (tf + k1 * normalization)
        if score > 0.0:
            scores.append({"doc_id": item["id"], "score": score})
    scores.sort(key=lambda hit: (-hit["score"], hit["doc_id"]))
    return scores[:limit]


def judgments(dataset: dict[str, Any]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for row in dataset["qrels"]:
        result.setdefault(row["query_id"], {})[row["doc_id"]] = row["grade"]
    return result


def metric_at_k(hits: list[dict[str, Any]], relevant: dict[str, int], k: int) -> float:
    positive = {doc_id for doc_id, grade in relevant.items() if grade > 0}
    if not positive:
        return 0.0
    found = {hit["doc_id"] for hit in hits[:k]} & positive
    return len(found) / len(positive)


def ndcg_at_k(hits: list[dict[str, Any]], relevant: dict[str, int], k: int) -> float:
    def gain(grade: int) -> float:
        return float((2**grade) - 1)

    def discount(rank: int) -> float:
        return 1.0 / math.log2(rank + 1.0)

    actual = sum(
        gain(relevant.get(hit["doc_id"], 0)) * discount(rank)
        for rank, hit in enumerate(hits[:k], start=1)
    )
    ideal_grades = sorted((grade for grade in relevant.values() if grade > 0), reverse=True)
    ideal = sum(gain(grade) * discount(rank) for rank, grade in enumerate(ideal_grades[:k], 1))
    return actual / ideal if ideal else 0.0


def reciprocal_rank(hits: list[dict[str, Any]], relevant: dict[str, int]) -> float:
    for rank, hit in enumerate(hits, start=1):
        if relevant.get(hit["doc_id"], 0) > 0:
            return 1.0 / rank
    return 0.0


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def run(dataset_path: Path, output_path: Path | None, limit: int, repeats: int) -> dict[str, Any]:
    dataset = read_json(dataset_path)
    validate_dataset(dataset)
    qrels = judgments(dataset)
    query_runs: list[dict[str, Any]] = []
    timings: list[float] = []
    for query in dataset["queries"]:
        samples: list[float] = []
        hits: list[dict[str, Any]] = []
        for _ in range(repeats):
            start = time.perf_counter()
            hits = score_bm25(dataset, query["text"], limit)
            samples.append((time.perf_counter() - start) * 1000.0)
        timings.append(statistics.median(samples))
        query_runs.append({
            "query_id": query["id"],
            "query_class": query.get("query_class", "unspecified"),
            "answer_mode": query.get("answer_mode", "judged"),
            "hits": hits,
            "latency_ms": statistics.median(samples),
        })

    judged_runs = [row for row in query_runs if row["answer_mode"] == "judged"]
    recall = {
        str(k): sum(metric_at_k(row["hits"], qrels.get(row["query_id"], {}), k) for row in judged_runs)
        / len(judged_runs)
        for k in (1, 5, 10, 50)
    }
    ndcg = sum(ndcg_at_k(row["hits"], qrels.get(row["query_id"], {}), 10) for row in judged_runs) / len(judged_runs)
    mrr = sum(reciprocal_rank(row["hits"], qrels.get(row["query_id"], {})) for row in judged_runs) / len(judged_runs)
    no_answer = [row for row in query_runs if row["answer_mode"] == "no_answer"]
    no_answer_accuracy = (
        sum(not row["hits"] for row in no_answer) / len(no_answer) if no_answer else None
    )
    report = {
        "schema_version": 1,
        "runner": "lexical_protocol_bm25_python",
        "status": "SMOKE_ONLY",
        "dataset_name": dataset["dataset_name"],
        "dataset_sha256": sha256_file(dataset_path),
        "runner_revision": git_revision(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "tokenizer": "code_aware_fixture_v1",
        "scoring": {"name": "okapi_bm25", "k1": 1.5, "b": 0.75, "idf": "log1p_okapi"},
        "result_limit": limit,
        "repeat_count": repeats,
        "query_count": len(query_runs),
        "quality": {
            "recall_at": recall,
            "ndcg_at_10": ndcg,
            "mrr": mrr,
            "no_answer_accuracy": no_answer_accuracy,
        },
        "latency_ms": {
            "scope": "local_python_directional_only",
            "p50": percentile(timings, 0.50),
            "p95": percentile(timings, 0.95),
            "p99": percentile(timings, 0.99),
        },
        "queries": query_runs,
    }
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def self_test(dataset_path: Path) -> None:
    report = run(dataset_path, None, limit=10, repeats=1)
    expected = read_json(dataset_path).get("expected_top_ids", {})
    actual = {row["query_id"]: [hit["doc_id"] for hit in row["hits"]] for row in report["queries"]}
    for query_id, expected_ids in expected.items():
        if actual.get(query_id, [])[: len(expected_ids)] != expected_ids:
            raise AssertionError(f"{query_id}: expected {expected_ids}, got {actual.get(query_id, [])}")
    if report["quality"]["no_answer_accuracy"] != 1.0:
        raise AssertionError("no-answer smoke query must remain empty")
    print(json.dumps({"status": "PASS", "dataset": str(dataset_path), "quality": report["quality"]}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.limit <= 0 or args.repeats <= 0:
        parser.error("--limit and --repeats must be positive")
    try:
        if args.self_test:
            self_test(args.dataset)
        else:
            print(json.dumps(run(args.dataset, args.output, args.limit, args.repeats), indent=2, sort_keys=True))
        return 0
    except (AssertionError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"lexical protocol runner: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
