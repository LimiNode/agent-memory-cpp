#!/usr/bin/env python3
"""Offline, dependency-free comparison of classical lexical scorers."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
CAMEL_RE_1 = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
CAMEL_RE_2 = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
ALGORITHMS = ("bm25", "bm25_plus", "bm25l")
LEVELS = ("resource", "chunk")
ANSWER_MODES = {"judged", "judged_zero_relevant", "no_answer", "unjudged", "incomplete_qrels", "ignore"}


def tokenize(text: str) -> list[str]:
    expanded = CAMEL_RE_2.sub(" ", CAMEL_RE_1.sub(" ", text))
    return [part.casefold() for part in TOKEN_RE.findall(expanded)]


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("fixture must be a JSON object")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_revision() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def validate(data: dict[str, Any]) -> None:
    for key in ("dataset_name", "corpus", "queries", "qrels", "golden_vectors"):
        if key not in data:
            raise ValueError(f"fixture missing {key}")
    if not isinstance(data["corpus"], list) or not data["corpus"]:
        raise ValueError("corpus must be a non-empty array")
    ids = [row.get("id") for row in data["corpus"]]
    if len(set(ids)) != len(ids) or any(not isinstance(x, str) or not x for x in ids):
        raise ValueError("corpus ids must be unique non-empty strings")
    if any(not tokenize(row.get("text", "")) for row in data["corpus"]):
        raise ValueError("every corpus document must contain at least one token")
    for row in data["corpus"]:
        if row.get("document_unit") not in LEVELS:
            raise ValueError("document_unit must be resource or chunk")
        if not isinstance(row.get("resource_id"), str) or not row["resource_id"]:
            raise ValueError("every corpus document needs resource_id")
    qids = [row.get("id") for row in data["queries"]]
    if not qids or len(set(qids)) != len(qids):
        raise ValueError("query ids must be unique")
    known, known_queries = set(ids), set(qids)
    pairs: set[tuple[str, str, str]] = set()
    grades: dict[str, list[tuple[str, int]]] = {}
    units = {row["id"]: row["document_unit"] for row in data["corpus"]}
    for query in data["queries"]:
        if query.get("answer_mode") not in ANSWER_MODES:
            raise ValueError("unsupported answer_mode")
    for row in data["qrels"]:
        level = row.get("relevance_level")
        if level not in LEVELS:
            raise ValueError("qrel relevance_level must be resource or chunk")
        key = (row.get("query_id"), row.get("doc_id"), level)
        if key[0] not in known_queries or key[1] not in known:
            raise ValueError("qrel references unknown id")
        if units[key[1]] != level:
            raise ValueError("qrel relevance_level does not match document_unit")
        if isinstance(row.get("grade"), bool) or not isinstance(row.get("grade"), int) or row["grade"] < 0:
            raise ValueError("qrel grade must be a non-negative integer")
        if key in pairs:
            raise ValueError("duplicate or conflicting qrel")
        pairs.add(key)
        grades.setdefault(key[0], []).append((level, row["grade"]))
    for query in data["queries"]:
        mode, found = query["answer_mode"], grades.get(query["id"], [])
        if mode == "judged" and not any(grade > 0 for _, grade in found):
            raise ValueError("judged query requires a positive qrel")
        if mode == "judged_zero_relevant" and (not found or any(grade > 0 for _, grade in found)):
            raise ValueError("judged_zero_relevant requires explicit zero-grade qrels")
        if mode == "incomplete_qrels" and not found:
            raise ValueError("incomplete_qrels requires at least one explicit qrel")
        if mode in {"no_answer", "unjudged", "ignore"} and found:
            raise ValueError(f"{mode} query must not have qrels")


def build_index(documents: list[dict[str, Any]]) -> dict[str, Any]:
    tokens = {doc["id"]: tokenize(doc["text"]) for doc in documents}
    lengths = {doc_id: len(values) for doc_id, values in tokens.items()}
    term_frequencies: dict[str, dict[str, int]] = {}
    df: dict[str, int] = {}
    for doc_id, values in tokens.items():
        counts: dict[str, int] = {}
        for term in values:
            counts[term] = counts.get(term, 0) + 1
        term_frequencies[doc_id] = counts
        for term in counts:
            df[term] = df.get(term, 0) + 1
    if not documents:
        raise ValueError("evaluation arm has no documents")
    return {"docs": documents, "tokens": tokens, "tf": term_frequencies, "lengths": lengths,
            "df": df, "n": len(documents), "avgdl": sum(lengths.values()) / len(documents), "k1": 1.5, "b": 0.75}


def score_candidates(index: dict[str, Any], query_terms: list[str], algorithm: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for doc in index["docs"]:
        doc_id, norm = doc["id"], 1.0 - index["b"] + index["b"] * index["lengths"][doc["id"]] / index["avgdl"]
        total = 0.0
        for term in query_terms:
            tf, df = index["tf"][doc_id].get(term, 0), index["df"].get(term, 0)
            if not tf or not df:
                continue
            idf = math.log1p((index["n"] - df + 0.5) / (df + 0.5))
            if algorithm == "bm25":
                factor = tf * (index["k1"] + 1.0) / (tf + index["k1"] * norm)
            elif algorithm == "bm25_plus":
                factor = tf * (index["k1"] + 1.0) / (tf + index["k1"] * norm) + 1.0
            elif algorithm == "bm25l":
                adjusted_tf = tf / norm
                factor = (index["k1"] + 1.0) * (adjusted_tf + 1.0) / (index["k1"] + adjusted_tf + 1.0)
            else:
                raise ValueError(f"unknown algorithm: {algorithm}")
            total += idf * factor
        if total > 0.0:
            hits.append({"doc_id": doc_id, "score": total})
    return hits


def rank_candidates(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    candidates.sort(key=lambda hit: (-hit["score"], hit["doc_id"]))
    return candidates[:limit]


def score(index: dict[str, Any], query_terms: list[str], algorithm: str, limit: int) -> list[dict[str, Any]]:
    return rank_candidates(score_candidates(index, query_terms, algorithm), limit)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))] if ordered else 0.0


def qrel_map(data: dict[str, Any], level: str) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for row in data["qrels"]:
        if row["relevance_level"] == level:
            result.setdefault(row["query_id"], {})[row["doc_id"]] = row["grade"]
    return result


def metrics(hits: list[dict[str, Any]], relevant: dict[str, int], k: int) -> tuple[float | None, float | None, float | None]:
    positives = {doc for doc, grade in relevant.items() if grade > 0}
    if not positives:
        return None, None, None
    top = hits[:k]
    recall = len({hit["doc_id"] for hit in top} & positives) / len(positives)
    gain = lambda grade: float(2 ** grade - 1)
    discount = lambda rank: 1.0 / math.log2(rank + 1.0)
    actual = sum(gain(relevant.get(hit["doc_id"], 0)) * discount(rank) for rank, hit in enumerate(top, 1))
    ideal_grades = sorted((grade for grade in relevant.values() if grade > 0), reverse=True)[:k]
    ideal = sum(gain(grade) * discount(rank) for rank, grade in enumerate(ideal_grades, 1))
    rr = next((1.0 / rank for rank, hit in enumerate(hits, 1) if relevant.get(hit["doc_id"], 0) > 0), 0.0)
    return recall, actual / ideal if ideal else 0.0, rr


def evaluate_arm(data: dict[str, Any], documents: list[dict[str, Any]], algorithm: str, limit: int, repeats: int) -> dict[str, Any]:
    build_started = time.perf_counter()
    index = build_index(documents)
    build_ms = (time.perf_counter() - build_started) * 1000.0
    rows, prep_samples, scoring_samples, ranking_samples, end_to_end = [], [], [], [], []
    for query in data["queries"]:
        query_prep, scoring, ranking, e2e = [], [], [], []
        hits: list[dict[str, Any]] = []
        for _ in range(repeats):
            started = time.perf_counter()
            prep_started = time.perf_counter()
            terms = tokenize(query["text"])
            query_prep.append((time.perf_counter() - prep_started) * 1000.0)
            scoring_started = time.perf_counter()
            candidates = score_candidates(index, terms, algorithm)
            scoring.append((time.perf_counter() - scoring_started) * 1000.0)
            ranking_started = time.perf_counter()
            hits = rank_candidates(candidates, limit)
            ranking.append((time.perf_counter() - ranking_started) * 1000.0)
            e2e.append((time.perf_counter() - started) * 1000.0)
        prep_samples.extend(query_prep); scoring_samples.extend(scoring); ranking_samples.extend(ranking); end_to_end.extend(e2e)
        rows.append({"query_id": query["id"], "query_class": query.get("query_class", "unspecified"),
                     "answer_mode": query["answer_mode"], "hits": hits, "latency_ms": statistics.median(e2e),
                     "query_preprocessing_ms": statistics.median(query_prep), "scoring_ms": statistics.median(scoring),
                     "ranking_ms": statistics.median(ranking)})
    quality: dict[str, Any] = {}
    for level in LEVELS:
        qrels = qrel_map(data, level)
        eligible = [row for row in rows if row["answer_mode"] == "judged" and row["query_id"] in qrels
                    and any(value > 0 for value in qrels[row["query_id"]].values())]
        values = [metrics(row["hits"], qrels[row["query_id"]], 10) for row in eligible] if limit >= 10 else []
        classes = sorted({row["query_class"] for row in eligible})
        by_class = {}
        for query_class in classes:
            subset = [value for row, value in zip(eligible, values) if row["query_class"] == query_class]
            if subset:
                by_class[query_class] = {"query_count": len(subset), "recall_at_10": sum(v[0] for v in subset) / len(subset),
                                         "ndcg_at_10": sum(v[1] for v in subset) / len(subset), "mrr": sum(v[2] for v in subset) / len(subset)}
        quality[level] = {"query_count": len(eligible), "recall_at_10": sum(v[0] for v in values) / len(values) if values else None,
                          "ndcg_at_10": sum(v[1] for v in values) / len(values) if values else None,
                          "mrr": sum(v[2] for v in values) / len(values) if values else None, "by_query_class": by_class}
    no_answer = [row for row in rows if row["answer_mode"] == "no_answer"]
    zero = [row for row in rows if row["answer_mode"] == "judged_zero_relevant"]
    quality["no_answer_accuracy"] = sum(not row["hits"] for row in no_answer) / len(no_answer) if no_answer else None
    quality["zero_relevant_accuracy"] = sum(not row["hits"] for row in zero) / len(zero) if zero else None
    incomplete = sum(row["answer_mode"] == "incomplete_qrels" for row in rows)
    unjudged = sum(row["answer_mode"] == "unjudged" for row in rows)
    quality["qrels_coverage"] = {"aggregation": "judged_queries_with_positive_qrels_only",
                                  "aggregated_query_count": len([r for r in rows if r["answer_mode"] == "judged"]),
                                  "eligible_positive_judged_query_count": len([r for r in rows if r["answer_mode"] == "judged"]),
                                  "judged_zero_relevant_query_count": len(zero), "no_answer_query_count": len(no_answer),
                                  "incomplete_qrels_query_count": incomplete, "unjudged_query_count": unjudged,
                                  "ignored_query_count": sum(row["answer_mode"] == "ignore" for row in rows),
                                  "status": "partial" if incomplete or unjudged else "complete"}
    quality["evaluation_status"] = "partial_qrels" if incomplete or unjudged else ("complete" if limit >= 10 else "truncated_candidate_depth")
    timing = {"query_preprocessing_ms": {"p50": percentile(prep_samples, .5), "p95": percentile(prep_samples, .95), "p99": percentile(prep_samples, .99)},
              "scoring_ms": {"p50": percentile(scoring_samples, .5), "p95": percentile(scoring_samples, .95), "p99": percentile(scoring_samples, .99)},
              "ranking_ms": {"p50": percentile(ranking_samples, .5), "p95": percentile(ranking_samples, .95), "p99": percentile(ranking_samples, .99)},
              "end_to_end_ms": {"p50": percentile(end_to_end, .5), "p95": percentile(end_to_end, .95), "p99": percentile(end_to_end, .99)},
              "sample_count": len(end_to_end), "query_count": len(data["queries"]), "repeat_count": repeats,
              "sample_count_semantics": "query_repeat_observations_not_independent_queries", "statistical_status": "descriptive", "tail_stability_claim": "none"}
    return {"document_count": len(documents), "document_units": sorted({d["document_unit"] for d in documents}), "index_build_ms": build_ms,
            "quality": quality, "queries": rows, "timing_ms": timing}


def run(data: dict[str, Any], source: Path, limit: int, repeats: int) -> dict[str, Any]:
    if limit <= 0 or repeats <= 0:
        raise ValueError("limit and repeats must be positive")
    validate(data)
    verify_golden_vectors(data)
    arms = {level: [doc for doc in data["corpus"] if doc["document_unit"] == level] for level in LEVELS}
    arms["mixed_unit_pool"] = list(data["corpus"])
    reports = {algorithm: {arm: evaluate_arm(data, documents, algorithm, limit, repeats) for arm, documents in arms.items()} for algorithm in ALGORITHMS}
    return {"schema_version": 2, "runner": "classical_lexical_bakeoff_python", "status": "SMOKE_ONLY",
            "dataset_name": data["dataset_name"], "dataset_sha256": sha256_file(source), "runner_revision": git_revision(),
            "python": platform.python_version(), "platform": platform.platform(), "tokenizer": "code_aware_fixture_v1",
            "scoring": {"bm25": "idf*tf*(k1+1)/(tf+k1*(1-b+b*dl/avgdl))", "bm25_plus_delta": 1.0, "bm25l_delta": 1.0,
                        "k1": 1.5, "b": 0.75, "idf": "log1p_okapi", "tie_break": "doc_id ascending"},
            "result_limit": limit, "evaluation_arms": ["resource", "chunk"], "diagnostic_arm": "mixed_unit_pool",
            "algorithms": reports, "golden_vector_outcomes": golden_vector_outcomes(data),
            "storage_compatibility": {"implemented": ["offline corpus text", "in-memory scoring", "resource/chunk qrels"],
                "contract_only": ["projection-aware field scoring", "BM25F field materialization"],
                "missing": ["MDBX postings", "persistent lexical DBI", "retrieval lifecycle wiring"]}}


def verify_golden_vectors(data: dict[str, Any]) -> None:
    for vector in data["golden_vectors"]:
        index = build_index(vector["corpus"])
        hits = score(index, tokenize(vector["query"]), vector["algorithm"], 10)
        expected_ids = vector.get("expected_ids", [])
        if [hit["doc_id"] for hit in hits[:len(expected_ids)]] != expected_ids:
            raise AssertionError(f"golden ranking failed: {vector['name']}")
        if not hits or abs(hits[0]["score"] - vector["top_score"]) > 1e-9:
            raise AssertionError(f"golden score failed: {vector['name']}")
        for algorithm, ids in vector.get("algorithm_rankings", {}).items():
            actual = [hit["doc_id"] for hit in score(index, tokenize(vector["query"]), algorithm, 10)]
            if actual[:len(ids)] != ids:
                raise AssertionError(f"golden cross-scorer ranking failed: {vector['name']}/{algorithm}")


def golden_vector_outcomes(data: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = []
    for vector in data["golden_vectors"]:
        index = build_index(vector["corpus"])
        outcomes.append({"name": vector["name"], "expected_top_score": vector["top_score"],
                         "rankings": {algorithm: [hit["doc_id"] for hit in score(index, tokenize(vector["query"]), algorithm, 10)]
                                      for algorithm in ALGORITHMS}})
    return outcomes


def self_test(path: Path) -> None:
    data = read_json(path)
    report = run(data, path, 10, 1)
    expected = data["expected_top_ids"]
    for algorithm in ALGORITHMS:
        for arm in ("resource", "chunk"):
            actual = {row["query_id"]: [hit["doc_id"] for hit in row["hits"]]
                      for row in report["algorithms"][algorithm][arm]["queries"]}
            for query_id, ids in expected[arm][algorithm].items():
                if actual.get(query_id, [])[:len(ids)] != ids:
                    raise AssertionError(f"{algorithm}/{arm}/{query_id}: expected {ids}, got {actual.get(query_id)}")
    verify_golden_vectors(data)
    bad = json.loads(json.dumps(data)); bad["qrels"] = [{"query_id": "q_ru", "doc_id": "resource_ru", "grade": 2, "relevance_level": "bogus"}]
    try:
        validate(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid relevance_level must fail closed")
    bad = json.loads(json.dumps(data)); bad["corpus"][0]["document_unit"] = "segment_set"
    try:
        validate(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid document_unit must fail closed")
    bad = json.loads(json.dumps(data)); del bad["corpus"]
    try:
        validate(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("malformed schema must fail closed")
    bad = json.loads(json.dumps(data)); bad["qrels"] = [row for row in bad["qrels"] if row["query_id"] != "q_partial"]
    try:
        validate(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("empty incomplete_qrels must fail closed")
    bad = json.loads(json.dumps(data)); bad["qrels"].append(dict(bad["qrels"][0]))
    try:
        validate(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate qrels must fail closed")
    shallow = run(data, path, 5, 1)
    if shallow["algorithms"]["bm25"]["chunk"]["quality"]["chunk"]["recall_at_10"] is not None:
        raise AssertionError("Recall@10 must be null below candidate depth 10")
    try:
        run(data, path, 0, 1)
    except ValueError:
        pass
    else:
        raise AssertionError("non-positive limit must fail closed")
    bad = json.loads(json.dumps(data)); bad["golden_vectors"][0]["top_score"] += 1.0
    try:
        verify_golden_vectors(bad)
    except AssertionError:
        pass
    else:
        raise AssertionError("corrupted golden score must fail closed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(args.fixture); print("classical bake-off self-test: PASS"); return 0
    report = run(read_json(args.fixture), args.fixture, args.limit, args.repeats)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
