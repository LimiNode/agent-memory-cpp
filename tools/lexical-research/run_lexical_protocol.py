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
METRIC_KS = (1, 5, 10, 50)


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
    if not isinstance(dataset["qrels"], list):
        raise ValueError("qrels must be an array")
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
    allowed_answer_modes = {"judged", "no_answer", "ignore"}
    for query in dataset["queries"]:
        answer_mode = query.get("answer_mode", "judged")
        if answer_mode not in allowed_answer_modes:
            raise ValueError(f"unsupported query answer_mode: {answer_mode}")
    known = set(corpus_ids)
    known_queries = set(query_ids)
    for judgment in dataset["qrels"]:
        if judgment.get("query_id") not in known_queries:
            raise ValueError("qrel references an unknown query")
        if judgment.get("doc_id") not in known:
            raise ValueError("qrel references an unknown corpus item")
        if not isinstance(judgment.get("grade"), int) or judgment["grade"] < 0:
            raise ValueError("qrel grade must be a non-negative integer")


def build_index(dataset: dict[str, Any]) -> dict[str, Any]:
    """Build the portable control index outside the timed query path."""
    k1 = 1.5
    b = 0.75
    documents = dataset["corpus"]
    tokenized = {item["id"]: tokenize(item["text"]) for item in documents}
    lengths = {doc_id: len(tokens) for doc_id, tokens in tokenized.items()}
    average_length = sum(lengths.values()) / len(lengths)
    document_frequency: dict[str, int] = {}
    for tokens in tokenized.values():
        for term in set(tokens):
            document_frequency[term] = document_frequency.get(term, 0) + 1
    return {
        "documents": documents,
        "tokenized": tokenized,
        "lengths": lengths,
        "average_length": average_length,
        "document_frequency": document_frequency,
        "document_count": len(documents),
        "k1": k1,
        "b": b,
    }


def score_bm25(
    index: dict[str, Any], query_terms: list[str], limit: int
) -> list[dict[str, Any]]:
    scores: list[dict[str, Any]] = []
    documents = index["documents"]
    tokenized = index["tokenized"]
    lengths = index["lengths"]
    average_length = index["average_length"]
    document_frequency = index["document_frequency"]
    document_count = index["document_count"]
    k1 = index["k1"]
    b = index["b"]
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
    build_started = time.perf_counter()
    index = build_index(dataset)
    build_ms = (time.perf_counter() - build_started) * 1000.0
    query_runs: list[dict[str, Any]] = []
    preprocessing_timings: list[float] = []
    scoring_timings: list[float] = []
    end_to_end_timings: list[float] = []
    for query in dataset["queries"]:
        preprocessing_samples: list[float] = []
        scoring_samples: list[float] = []
        end_to_end_samples: list[float] = []
        hits: list[dict[str, Any]] = []
        for _ in range(repeats):
            end_to_end_start = time.perf_counter()
            preprocess_start = time.perf_counter()
            query_terms = tokenize(query["text"])
            preprocessing_samples.append((time.perf_counter() - preprocess_start) * 1000.0)
            scoring_start = time.perf_counter()
            hits = score_bm25(index, query_terms, limit)
            scoring_samples.append((time.perf_counter() - scoring_start) * 1000.0)
            end_to_end_samples.append((time.perf_counter() - end_to_end_start) * 1000.0)
        preprocessing_ms = statistics.median(preprocessing_samples)
        scoring_ms = statistics.median(scoring_samples)
        end_to_end_ms = statistics.median(end_to_end_samples)
        preprocessing_timings.extend(preprocessing_samples)
        scoring_timings.extend(scoring_samples)
        end_to_end_timings.extend(end_to_end_samples)
        query_runs.append({
            "query_id": query["id"],
            "query_class": query.get("query_class", "unspecified"),
            "answer_mode": query.get("answer_mode", "judged"),
            "hits": hits,
            "latency_ms": end_to_end_ms,
            "query_preprocessing_ms": preprocessing_ms,
            "scoring_ms": scoring_ms,
        })

    judged_runs = [row for row in query_runs if row["answer_mode"] == "judged"]
    recall = {
        str(k): (
            sum(metric_at_k(row["hits"], qrels.get(row["query_id"], {}), k) for row in judged_runs)
            / len(judged_runs)
            if k <= limit
            else None
        )
        for k in METRIC_KS
    }
    ndcg = (
        sum(ndcg_at_k(row["hits"], qrels.get(row["query_id"], {}), 10) for row in judged_runs)
        / len(judged_runs)
        if limit >= 10
        else None
    )
    mrr = (
        sum(reciprocal_rank(row["hits"], qrels.get(row["query_id"], {})) for row in judged_runs)
        / len(judged_runs)
        if limit >= 1
        else None
    )
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
        "candidate_depth": limit,
        "repeat_count": repeats,
        "query_count": len(query_runs),
        "quality": {
            "recall_at": recall,
            "ndcg_at_10": ndcg,
            "mrr": mrr,
            "no_answer_accuracy": no_answer_accuracy,
            "evaluation_status": "complete" if limit >= 50 else "truncated_candidate_depth",
        },
        "timing": {
            "build_ms": build_ms,
            "query_preprocessing_ms": {
                "p50": percentile(preprocessing_timings, 0.50),
                "p95": percentile(preprocessing_timings, 0.95),
                "p99": percentile(preprocessing_timings, 0.99),
            },
            "scoring_ms": {
                "p50": percentile(scoring_timings, 0.50),
                "p95": percentile(scoring_timings, 0.95),
                "p99": percentile(scoring_timings, 0.99),
            },
            "end_to_end_ms": {
                "p50": percentile(end_to_end_timings, 0.50),
                "p95": percentile(end_to_end_timings, 0.95),
                "p99": percentile(end_to_end_timings, 0.99),
            },
            "sample_count": len(end_to_end_timings),
            "query_count": len(query_runs),
            "repeat_count": repeats,
            "observation_unit": "one_query_repeat",
            "aggregation": "raw_query_repeat_samples; query receipt latency is per-query median",
            "sample_count_semantics": "query_repeat_observations_not_independent_queries",
            "statistical_status": (
                "directional"
                if len(end_to_end_timings) < 100
                else "descriptive"
                if len(end_to_end_timings) < 1000
                else "measured"
            ),
            "measured_status_semantics": "measured_sample_distribution_only",
            "tail_stability_claim": "none",
            "scope": "local_python_query_only_after_index_build",
        },
        "latency_ms": {
            "scope": "local_python_directional_only",
            "p50": percentile(end_to_end_timings, 0.50),
            "p95": percentile(end_to_end_timings, 0.95),
            "p99": percentile(end_to_end_timings, 0.99),
        },
        "queries": query_runs,
    }
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def self_test(dataset_path: Path) -> None:
    report = run(dataset_path, None, limit=50, repeats=1)
    expected = read_json(dataset_path).get("expected_top_ids", {})
    actual = {row["query_id"]: [hit["doc_id"] for hit in row["hits"]] for row in report["queries"]}
    for query_id, expected_ids in expected.items():
        if actual.get(query_id, [])[: len(expected_ids)] != expected_ids:
            raise AssertionError(f"{query_id}: expected {expected_ids}, got {actual.get(query_id, [])}")
    if report["quality"]["no_answer_accuracy"] != 1.0:
        raise AssertionError("no-answer smoke query must remain empty")
    if report["quality"]["evaluation_status"] != "complete":
        raise AssertionError("default smoke depth must support Recall@50")
    truncated = run(dataset_path, None, limit=10, repeats=1)
    if truncated["quality"]["evaluation_status"] != "truncated_candidate_depth":
        raise AssertionError("sub-50 candidate depth must be marked truncated")
    if truncated["quality"]["recall_at"]["50"] is not None:
        raise AssertionError("truncated Recall@50 must not be reported as a number")
    timing = report["timing"]
    if timing["sample_count"] != timing["query_count"] * timing["repeat_count"]:
        raise AssertionError("sample_count must count query-repeat observations")
    if timing["sample_count_semantics"] != "query_repeat_observations_not_independent_queries":
        raise AssertionError("timing receipt must reject an independent-query interpretation")
    if timing["measured_status_semantics"] != "measured_sample_distribution_only":
        raise AssertionError("measured status must remain scoped to the observed sample distribution")
    if timing["tail_stability_claim"] != "none":
        raise AssertionError("repeat-count thresholds must not claim stable tail latency")
    dataset = read_json(dataset_path)
    invalid_cases = (
        ("unknown qrel query", {"qrels": [{"query_id": "missing", "doc_id": "d1", "grade": 1}]}),
        ("unknown qrel document", {"qrels": [{"query_id": "q_exact_identifier", "doc_id": "missing", "grade": 1}]}),
        ("negative qrel grade", {"qrels": [{"query_id": "q_exact_identifier", "doc_id": "d1", "grade": -1}]}),
    )
    for label, override in invalid_cases:
        candidate = dict(dataset)
        candidate.update(override)
        try:
            validate_dataset(candidate)
        except ValueError:
            continue
        raise AssertionError(f"{label} must be rejected")
    print(json.dumps({"status": "PASS", "dataset": str(dataset_path), "quality": report["quality"]}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int, default=50)
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
