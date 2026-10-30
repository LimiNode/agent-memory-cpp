#!/usr/bin/env python3
"""Run a deterministic document-only learned-sparse smoke experiment.

The evaluator intentionally uses a frozen weighted-postings artifact rather
than a neural runtime. It proves artifact and lifecycle contracts first; it
does not claim that the toy weights represent a trained model.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import statistics
import time
from pathlib import Path
from typing import Any


TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
METRIC_KS = (1, 5, 10, 50)
EXPECTED_SMOKE_MANIFEST_SHA256 = (
    "53d2aa1a9535044db9d9c9e3c914c8053c3a96b039e4825af4ceec95b8a79ddc"
)


class ValidationError(ValueError):
    """Raised when a frozen corpus or derived artifact is invalid."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in TOKEN_RE.finditer(text)]


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError("manifest must be an object")
    return value


def validate_manifest(manifest: dict[str, Any]) -> None:
    if isinstance(manifest.get("schema_version"), bool) or manifest.get("schema_version") != 1:
        raise ValidationError("unsupported manifest schema")
    required = ("dataset_name", "source_revision", "tokenizer", "model", "vocabulary", "corpus", "queries", "qrels", "weighted_postings")
    for key in required:
        if key not in manifest:
            raise ValidationError(f"missing manifest field: {key}")
    corpus = manifest["corpus"]
    queries = manifest["queries"]
    qrels = manifest["qrels"]
    postings = manifest["weighted_postings"]
    if not isinstance(corpus, list) or not corpus:
        raise ValidationError("corpus must be a non-empty list")
    if not isinstance(queries, list) or not queries:
        raise ValidationError("queries must be a non-empty list")
    if not isinstance(qrels, list) or not isinstance(postings, list):
        raise ValidationError("qrels and weighted_postings must be lists")

    if any(not isinstance(item, dict) for item in corpus + queries + qrels + postings):
        raise ValidationError("manifest records must be objects")
    doc_ids = [item.get("id") for item in corpus]
    if any(not isinstance(doc_id, str) or not doc_id for doc_id in doc_ids) or len(set(doc_ids)) != len(doc_ids):
        raise ValidationError("corpus IDs must be unique non-empty strings")
    if any(not isinstance(item.get("text"), str) for item in corpus):
        raise ValidationError("corpus text must be strings")
    if any(item.get("active") is not True for item in corpus):
        raise ValidationError("all corpus records in an active artifact must be explicitly active")
    query_ids = [item.get("id") for item in queries]
    if any(not isinstance(query_id, str) or not query_id for query_id in query_ids) or len(set(query_ids)) != len(query_ids):
        raise ValidationError("query IDs must be unique non-empty strings")
    doc_set = set(doc_ids)
    query_set = set(query_ids)
    active_docs = set(doc_ids)

    vocabulary = manifest["vocabulary"]
    if not isinstance(vocabulary, dict) or not vocabulary:
        raise ValidationError("vocabulary must map normalized terms to token IDs")
    if any(not isinstance(term, str) or term.casefold() != term for term in vocabulary):
        raise ValidationError("vocabulary terms must be normalized strings")
    token_ids = list(vocabulary.values())
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in token_ids) or len(set(token_ids)) != len(token_ids):
        raise ValidationError("vocabulary token IDs must be unique non-negative integers")

    for query in queries:
        if (not isinstance(query.get("text"), str)
            or not query.get("text")
            or query.get("answer_mode") not in {"judged", "no_answer"}):
            raise ValidationError("query text and answer_mode are invalid")

    seen_qrels: set[tuple[str, str]] = set()
    for row in qrels:
        key = (row.get("query_id"), row.get("doc_id"))
        if key in seen_qrels:
            raise ValidationError("duplicate qrel")
        seen_qrels.add(key)
        if key[0] not in query_set or key[1] not in doc_set:
            raise ValidationError("qrel references unknown query or document")
        grade = row.get("grade")
        if isinstance(grade, bool) or not isinstance(grade, int) or grade < 0:
            raise ValidationError("qrel grade must be a non-negative integer")

    tokenizer = manifest["tokenizer"]
    model = manifest["model"]
    if not isinstance(tokenizer, dict) or not isinstance(model, dict):
        raise ValidationError("tokenizer and model must be objects")
    if (tokenizer.get("name") != "unicode_word_v1"
        or not isinstance(tokenizer.get("revision"), str)
        or not tokenizer.get("revision")):
        raise ValidationError("tokenizer identity is incomplete")
    if (model.get("mode") != "document_only_impact"
        or not isinstance(model.get("id"), str)
        or not model.get("id")
        or not isinstance(model.get("revision"), str)
        or not model.get("revision")):
        raise ValidationError("this evaluator accepts only document-only model artifacts")
    if not isinstance(manifest.get("source_revision"), str) or not manifest.get("source_revision"):
        raise ValidationError("source_revision is required")

    terms_by_document = {
        item["id"]: set(tokens(item["text"])) for item in corpus
    }
    seen_postings: set[tuple[str, str]] = set()
    for posting in postings:
        doc_id = posting.get("doc_id")
        term = posting.get("term")
        key = (doc_id, term)
        if key in seen_postings:
            raise ValidationError("duplicate weighted posting")
        seen_postings.add(key)
        if doc_id not in active_docs or not isinstance(term, str) or term.casefold() != term:
            raise ValidationError("weighted posting identity is invalid")
        if term not in terms_by_document[doc_id]:
            raise ValidationError("document-only posting is absent from its document")
        if vocabulary.get(term) != posting.get("token_id"):
            raise ValidationError("weighted posting token ID is incompatible with the vocabulary")
        weight = posting.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight < 0.0:
            raise ValidationError("weighted posting must contain a finite non-negative weight")
        if posting.get("source_revision") != manifest["source_revision"]:
            raise ValidationError("weighted posting source revision is stale")
        if posting.get("tokenizer_revision") != tokenizer["revision"]:
            raise ValidationError("weighted posting tokenizer revision is incompatible")

    qrels_by_query = {query_id: [] for query_id in query_set}
    for row in qrels:
        qrels_by_query[row["query_id"]].append(row)
    for query in queries:
        rows = qrels_by_query[query["id"]]
        if query["answer_mode"] == "no_answer" and rows:
            raise ValidationError("no-answer query must not have qrels")
        if query["answer_mode"] == "judged" and not rows:
            raise ValidationError("judged query must have qrels")


def qrel_map(manifest: dict[str, Any]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for row in manifest["qrels"]:
        result.setdefault(row["query_id"], {})[row["doc_id"]] = row["grade"]
    return result


def bm25_hits(manifest: dict[str, Any], query: str, limit: int) -> list[dict[str, Any]]:
    corpus = manifest["corpus"]
    tokenized = {item["id"]: tokens(item["text"]) for item in corpus}
    lengths = {doc_id: len(values) for doc_id, values in tokenized.items()}
    average_length = sum(lengths.values()) / len(lengths)
    document_frequency = {term: sum(term in values for values in tokenized.values()) for values in tokenized.values() for term in set(values)}
    scores: list[dict[str, Any]] = []
    for item in corpus:
        values = tokenized[item["id"]]
        frequencies = {term: values.count(term) for term in set(values)}
        score = 0.0
        for term in tokens(query):
            df = document_frequency.get(term, 0)
            tf = frequencies.get(term, 0)
            if not df or not tf:
                continue
            idf = math.log(1.0 + (len(corpus) - df + 0.5) / (df + 0.5))
            norm = 1.0 - 0.75 + 0.75 * lengths[item["id"]] / average_length
            score += idf * (tf * 2.5) / (tf + 1.5 * norm)
        if score > 0.0:
            scores.append({"doc_id": item["id"], "score": score})
    return sorted(scores, key=lambda hit: (-hit["score"], hit["doc_id"]))[:limit]


def build_weighted_index(manifest: dict[str, Any]) -> dict[str, tuple[tuple[str, float], ...]]:
    by_term: dict[str, list[tuple[str, float]]] = {}
    for posting in manifest["weighted_postings"]:
        by_term.setdefault(posting["term"], []).append((posting["doc_id"], float(posting["weight"])))
    return {
        term: tuple(sorted(postings, key=lambda posting: posting[0]))
        for term, postings in by_term.items()
    }


def weighted_scores(
    index: dict[str, tuple[tuple[str, float], ...]], query: str
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for term in tokens(query):
        for doc_id, weight in index.get(term, ()):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight
    return scores


def rank_weighted_scores(scores: dict[str, float], limit: int) -> list[dict[str, Any]]:
    return [
        {"doc_id": doc_id, "score": score}
        for doc_id, score in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]
        if score > 0.0
    ]


def weighted_hits(
    index: dict[str, tuple[tuple[str, float], ...]], query: str, limit: int
) -> list[dict[str, Any]]:
    return rank_weighted_scores(weighted_scores(index, query), limit)


def recall_at_k(hits: list[dict[str, Any]], relevant: dict[str, int], k: int) -> float:
    positives = {doc_id for doc_id, grade in relevant.items() if grade > 0}
    return len({hit["doc_id"] for hit in hits[:k]} & positives) / len(positives) if positives else 0.0


def ndcg_at_10(hits: list[dict[str, Any]], relevant: dict[str, int]) -> float:
    def gain(grade: int) -> float:
        return float(2**grade - 1)

    def discount(rank: int) -> float:
        return 1.0 / math.log2(rank + 1.0)

    actual = sum(gain(relevant.get(hit["doc_id"], 0)) * discount(rank) for rank, hit in enumerate(hits[:10], 1))
    ideal_grades = sorted((grade for grade in relevant.values() if grade > 0), reverse=True)
    ideal = sum(gain(grade) * discount(rank) for rank, grade in enumerate(ideal_grades[:10], 1))
    return actual / ideal if ideal else 0.0


def quality_metrics(
    query_runs: list[dict[str, Any]],
    qrels: dict[str, dict[str, int]],
    arm: str,
    limit: int,
) -> dict[str, Any]:
    judged = [
        row for row in query_runs
        if row["answer_mode"] == "judged" and row["relevant_count"] > 0
    ]
    no_answer = [row for row in query_runs if row["answer_mode"] == "no_answer"]
    return {
        "recall_at": {
            str(k): (
                sum(recall_at_k(row[f"{arm}_hits"], qrels[row["query_id"]], k)
                    for row in judged) / len(judged)
                if judged and k <= limit else None
            )
            for k in METRIC_KS
        },
        "ndcg_at_10": (
            sum(ndcg_at_10(row[f"{arm}_hits"], qrels[row["query_id"]])
                for row in judged) / len(judged)
            if judged and limit >= 10 else None
        ),
        "no_answer_accuracy": (
            sum(not row[f"{arm}_hits"] for row in no_answer) / len(no_answer)
            if no_answer else None
        ),
        "judged_query_count": len(judged),
    }


def run(manifest_path: Path, limit: int, repeats: int) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    validate_manifest(manifest)
    qrels = qrel_map(manifest)
    build_started = time.perf_counter()
    weighted_index = build_weighted_index(manifest)
    index_build_ms = (time.perf_counter() - build_started) * 1000.0
    traversal_timings: list[float] = []
    ranking_timings: list[float] = []
    total_timings: list[float] = []
    query_runs: list[dict[str, Any]] = []
    for query in manifest["queries"]:
        traversal_samples: list[float] = []
        ranking_samples: list[float] = []
        total_samples: list[float] = []
        weighted: list[dict[str, Any]] = []
        for _ in range(repeats):
            total_started = time.perf_counter()
            traversal_started = time.perf_counter()
            scores = weighted_scores(weighted_index, query["text"])
            traversal_samples.append((time.perf_counter() - traversal_started) * 1000.0)
            ranking_started = time.perf_counter()
            weighted = rank_weighted_scores(scores, limit)
            ranking_samples.append((time.perf_counter() - ranking_started) * 1000.0)
            total_samples.append((time.perf_counter() - total_started) * 1000.0)
        traversal_timings.extend(traversal_samples)
        ranking_timings.extend(ranking_samples)
        total_timings.extend(total_samples)
        relevant = qrels.get(query["id"], {})
        query_runs.append({
            "query_id": query["id"],
            "query_class": query.get("query_class", "unspecified"),
            "answer_mode": query["answer_mode"],
            "weighted_hits": weighted,
            "bm25_hits": bm25_hits(manifest, query["text"], limit),
            "weighted_traversal_ms": statistics.median(traversal_samples),
            "weighted_ranking_ms": statistics.median(ranking_samples),
            "weighted_total_ms": statistics.median(total_samples),
            "relevant_count": sum(grade > 0 for grade in relevant.values()),
        })
    return {
        "schema_version": 1,
        "status": "SMOKE_ONLY",
        "manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
        "source_revision": manifest["source_revision"],
        "model": manifest["model"],
        "tokenizer": manifest["tokenizer"],
        "posting_count": len(manifest["weighted_postings"]),
        "query_count": len(query_runs),
        "quality": {
            "weighted": quality_metrics(query_runs, qrels, "weighted", limit),
            "bm25": quality_metrics(query_runs, qrels, "bm25", limit),
        },
        "timing": {
            "scope": "weighted_posting_search_with_explicit_phases",
            "index_build_ms": index_build_ms,
            "sample_count": len(total_timings),
            "repeat_count": repeats,
            "observation_unit": "one_query_repeat",
            "sample_count_semantics": "query_repeat_observations_not_independent_queries",
            "measured_status": "directional" if len(total_timings) < 100 else "descriptive" if len(total_timings) < 1000 else "measured_sample_distribution_only",
            "traversal_sample_count": len(traversal_timings),
            "ranking_sample_count": len(ranking_timings),
            "total_sample_count": len(total_timings),
            "traversal_p50_ms": statistics.median(traversal_timings) if traversal_timings else None,
            "ranking_p50_ms": statistics.median(ranking_timings) if ranking_timings else None,
            "total_p50_ms": statistics.median(total_timings) if total_timings else None,
        },
        "queries": query_runs,
    }


def assert_expected_results(report: dict[str, Any], expected: dict[str, list[str]]) -> None:
    actual = {
        row["query_id"]: [hit["doc_id"] for hit in row["weighted_hits"]]
        for row in report["queries"]
    }
    if set(actual) != set(expected) or any(actual[query_id] != doc_ids for query_id, doc_ids in expected.items()):
        raise AssertionError(f"unexpected weighted ranking: {actual}")
    quality = report["quality"]
    weighted_quality = quality["weighted"]
    bm25_quality = quality["bm25"]
    for metrics in (weighted_quality, bm25_quality):
        if any(metrics["recall_at"][str(k)] != 1.0 for k in (1, 5, 10)):
            raise AssertionError(f"positive recall oracle failed: {metrics}")
        if metrics["ndcg_at_10"] != 1.0 or metrics["no_answer_accuracy"] != 1.0:
            raise AssertionError(f"quality oracle failed: {metrics}")
    timing = report["timing"]
    if (timing["traversal_sample_count"] != timing["sample_count"]
        or timing["ranking_sample_count"] != timing["sample_count"]
        or timing["total_sample_count"] != timing["sample_count"]
        or timing["index_build_ms"] < 0.0):
        raise AssertionError(f"timing phase instrumentation failed: {timing}")


def self_test(manifest_path: Path) -> None:
    baseline = load_manifest(manifest_path)
    validate_manifest(baseline)
    if sha256_bytes(manifest_path.read_bytes()) != EXPECTED_SMOKE_MANIFEST_SHA256:
        raise AssertionError("smoke fixture digest is not the independently pinned digest")
    expected = baseline["expected_weighted_top_ids"]
    report = run(manifest_path, limit=10, repeats=2)
    assert_expected_results(report, expected)
    unexpected_result = copy.deepcopy(report)
    next(row for row in unexpected_result["queries"] if row["query_id"] == "q_none")["weighted_hits"] = [
        {"doc_id": "d1", "score": 1.0}
    ]
    try:
        assert_expected_results(unexpected_result, expected)
    except AssertionError:
        pass
    else:
        raise AssertionError("unexpected no-answer result was accepted by the oracle")
    mutations = []
    invalid_weight = copy.deepcopy(baseline)
    invalid_weight["weighted_postings"][0]["weight"] = -1.0
    mutations.append(("negative weight", invalid_weight))
    duplicate = copy.deepcopy(baseline)
    duplicate["weighted_postings"].append(copy.deepcopy(duplicate["weighted_postings"][0]))
    mutations.append(("duplicate posting", duplicate))
    stale = copy.deepcopy(baseline)
    stale["weighted_postings"][0]["source_revision"] = "stale-source"
    mutations.append(("stale source", stale))
    wrong_tokenizer = copy.deepcopy(baseline)
    wrong_tokenizer["weighted_postings"][0]["tokenizer_revision"] = "other-tokenizer"
    mutations.append(("wrong tokenizer", wrong_tokenizer))
    wrong_token_id = copy.deepcopy(baseline)
    wrong_token_id["weighted_postings"][0]["token_id"] = 999999
    mutations.append(("wrong token ID", wrong_token_id))
    deleted_document = copy.deepcopy(baseline)
    deleted_id = deleted_document["weighted_postings"][0]["doc_id"]
    next(item for item in deleted_document["corpus"] if item["id"] == deleted_id)["active"] = False
    mutations.append(("deleted document posting", deleted_document))
    duplicate_qrel = copy.deepcopy(baseline)
    duplicate_qrel["qrels"].append(copy.deepcopy(duplicate_qrel["qrels"][0]))
    mutations.append(("duplicate qrel", duplicate_qrel))
    cross_document = copy.deepcopy(baseline)
    cross_document["weighted_postings"][0]["doc_id"] = "d2"
    mutations.append(("cross-document posting", cross_document))
    no_answer_qrel = copy.deepcopy(baseline)
    no_answer_qrel["qrels"].append({"query_id": "q_none", "doc_id": "d1", "grade": 1})
    mutations.append(("no-answer qrel", no_answer_qrel))
    non_finite = copy.deepcopy(baseline)
    non_finite["weighted_postings"][0]["weight"] = float("nan")
    mutations.append(("non-finite weight", non_finite))
    for label, candidate in mutations:
        try:
            validate_manifest(candidate)
        except ValidationError:
            continue
        raise AssertionError(f"mutation was accepted: {label}")
    print(json.dumps({"status": "PASS", "negative_controls": len(mutations) + 1, "quality": report["quality"]}, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.limit <= 0 or args.repeats <= 0:
        parser.error("--limit and --repeats must be positive")
    try:
        if args.self_test:
            self_test(args.manifest)
        else:
            result = run(args.manifest, args.limit, args.repeats)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (AssertionError, OSError, ValidationError, json.JSONDecodeError) as exc:
        print(f"learned sparse experiment: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
