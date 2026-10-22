#!/usr/bin/env python3
"""Offline, dependency-free comparison of classical lexical scorers.

The harness is deliberately separate from the production C++ index.  It uses
the fixture tokenizer and deterministic ``(-score, doc_id)`` ordering so that
algorithmic differences are inspectable and reproducible.
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
import time
from pathlib import Path
from typing import Any

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
CAMEL_RE_1 = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
CAMEL_RE_2 = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
ALGORITHMS = ("bm25", "bm25_plus", "bm25l")


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
    ids = [row.get("id") for row in data["corpus"]]
    qids = [row.get("id") for row in data["queries"]]
    if not ids or len(set(ids)) != len(ids) or any(not isinstance(x, str) or not x for x in ids):
        raise ValueError("corpus ids must be unique non-empty strings")
    if not qids or len(set(qids)) != len(qids):
        raise ValueError("query ids must be unique")
    modes = {"judged", "judged_zero_relevant", "no_answer", "unjudged", "incomplete_qrels", "ignore"}
    known = set(ids)
    known_queries = set(qids)
    pairs: set[tuple[str, str, str]] = set()
    grades: dict[tuple[str, str], list[int]] = {}
    for query in data["queries"]:
        if query.get("answer_mode") not in modes:
            raise ValueError("unsupported answer_mode")
    for row in data["qrels"]:
        key = (row.get("query_id"), row.get("doc_id"), row.get("relevance_level", "chunk"))
        if key[0] not in known_queries or key[1] not in known:
            raise ValueError("qrel references unknown id")
        if isinstance(row.get("grade"), bool) or not isinstance(row.get("grade"), int) or row["grade"] < 0:
            raise ValueError("qrel grade must be a non-negative integer")
        if key in pairs:
            raise ValueError("duplicate qrel")
        pairs.add(key)
        grades.setdefault((key[0], key[2]), []).append(row["grade"])
    for query in data["queries"]:
        mode = query["answer_mode"]
        found = [grade for (qid, _), values in grades.items() if qid == query["id"] for grade in values]
        if mode == "judged" and not any(grade > 0 for grade in found):
            raise ValueError("judged query requires a positive qrel")
        if mode == "judged_zero_relevant" and (not found or any(grade > 0 for grade in found)):
            raise ValueError("judged_zero_relevant requires explicit zero grades")
        if mode in {"no_answer", "unjudged", "ignore"} and found:
            raise ValueError(f"{mode} query must not have qrels")


def build_index(data: dict[str, Any]) -> dict[str, Any]:
    docs = data["corpus"]
    tokens = {doc["id"]: tokenize(doc.get("text", "")) for doc in docs}
    lengths = {doc_id: len(value) for doc_id, value in tokens.items()}
    df: dict[str, int] = {}
    for values in tokens.values():
        for term in set(values):
            df[term] = df.get(term, 0) + 1
    return {"docs": docs, "tokens": tokens, "lengths": lengths, "df": df,
            "n": len(docs), "avgdl": sum(lengths.values()) / len(docs), "k1": 1.5, "b": 0.75}


def score(index: dict[str, Any], query: str, algorithm: str, limit: int) -> list[dict[str, Any]]:
    terms = tokenize(query)
    counts_by_doc: dict[str, dict[str, int]] = {}
    for doc_id, values in index["tokens"].items():
        counts: dict[str, int] = {}
        for term in values:
            counts[term] = counts.get(term, 0) + 1
        counts_by_doc[doc_id] = counts
    hits: list[dict[str, Any]] = []
    for doc in index["docs"]:
        doc_id = doc["id"]
        dl = index["lengths"][doc_id]
        norm = 1.0 - index["b"] + index["b"] * dl / index["avgdl"]
        total = 0.0
        for term in terms:
            tf = counts_by_doc[doc_id].get(term, 0)
            df = index["df"].get(term, 0)
            if not tf or not df:
                continue
            idf = math.log1p((index["n"] - df + 0.5) / (df + 0.5))
            if algorithm == "bm25":
                contribution = tf * (index["k1"] + 1.0) / (tf + index["k1"] * norm)
            elif algorithm == "bm25_plus":
                contribution = tf * (index["k1"] + 1.0) / (tf + index["k1"] * norm) + 1.0
            elif algorithm == "bm25l":
                adjusted_tf = tf / norm
                contribution = (index["k1"] + 1.0) * (adjusted_tf + 1.0) / (index["k1"] + adjusted_tf + 1.0)
            else:
                raise ValueError(f"unknown algorithm: {algorithm}")
            total += idf * contribution
        if total > 0.0:
            hits.append({"doc_id": doc_id, "score": total})
    hits.sort(key=lambda hit: (-hit["score"], hit["doc_id"]))
    return hits[:limit]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


def qrel_map(data: dict[str, Any], level: str) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for row in data["qrels"]:
        if row.get("relevance_level", "chunk") == level:
            result.setdefault(row["query_id"], {})[row["doc_id"]] = row["grade"]
    return result


def metrics(hits: list[dict[str, Any]], relevant: dict[str, int], k: int) -> tuple[float | None, float | None, float | None]:
    positives = {doc for doc, grade in relevant.items() if grade > 0}
    if not positives:
        return None, None, None
    top = hits[:k]
    recall = len({hit["doc_id"] for hit in top} & positives) / len(positives)
    def gain(grade: int) -> float: return float(2 ** grade - 1)
    def discount(rank: int) -> float: return 1.0 / math.log2(rank + 1.0)
    actual = sum(gain(relevant.get(hit["doc_id"], 0)) * discount(rank) for rank, hit in enumerate(top, 1))
    ideal_grades = sorted((grade for grade in relevant.values() if grade > 0), reverse=True)[:k]
    ideal = sum(gain(grade) * discount(rank) for rank, grade in enumerate(ideal_grades, 1))
    rr = next((1.0 / rank for rank, hit in enumerate(hits, 1) if relevant.get(hit["doc_id"], 0) > 0), 0.0)
    return recall, actual / ideal if ideal else 0.0, rr


def run(data: dict[str, Any], source: Path, limit: int, repeats: int) -> dict[str, Any]:
    validate(data)
    index = build_index(data)
    reports: dict[str, Any] = {}
    for algorithm in ALGORITHMS:
        rows = []
        timings: list[float] = []
        for query in data["queries"]:
            samples = []
            hits: list[dict[str, Any]] = []
            for _ in range(repeats):
                started = time.perf_counter()
                hits = score(index, query["text"], algorithm, limit)
                samples.append((time.perf_counter() - started) * 1000.0)
            timings.extend(samples)
            rows.append({"query_id": query["id"], "query_class": query.get("query_class", "unspecified"),
                         "answer_mode": query["answer_mode"], "hits": hits,
                         "latency_ms": statistics.median(samples)})
        quality: dict[str, Any] = {}
        for level in ("resource", "chunk"):
            qrels = qrel_map(data, level)
            eligible = [row for row in rows if row["answer_mode"] == "judged" and row["query_id"] in qrels
                        and any(value > 0 for value in qrels[row["query_id"]].values())]
            values = [metrics(row["hits"], qrels[row["query_id"]], 10) for row in eligible]
            quality[level] = {"query_count": len(eligible),
                              "recall_at_10": sum(v[0] for v in values) / len(values) if values else None,
                              "ndcg_at_10": sum(v[1] for v in values) / len(values) if values else None,
                              "mrr": sum(v[2] for v in values) / len(values) if values else None,
                              "by_query_class": {}}
            classes = sorted({row["query_class"] for row in eligible})
            for query_class in classes:
                subset = [v for row, v in zip(eligible, values) if row["query_class"] == query_class]
                quality[level]["by_query_class"][query_class] = {"query_count": len(subset),
                    "recall_at_10": sum(v[0] for v in subset) / len(subset), "ndcg_at_10": sum(v[1] for v in subset) / len(subset),
                    "mrr": sum(v[2] for v in subset) / len(subset)}
        reports[algorithm] = {"quality": quality, "queries": rows,
                              "timing_ms": {"p50": percentile(timings, .50), "p95": percentile(timings, .95), "p99": percentile(timings, .99),
                                             "sample_count": len(timings), "query_count": len(data["queries"]), "repeat_count": repeats,
                                             "sample_count_semantics": "query_repeat_observations_not_independent_queries",
                                             "statistical_status": "descriptive", "tail_stability_claim": "none"}}
    return {"schema_version": 1, "runner": "classical_lexical_bakeoff_python", "status": "SMOKE_ONLY",
            "dataset_name": data["dataset_name"], "dataset_sha256": sha256_file(source), "runner_revision": git_revision(),
            "python": platform.python_version(), "platform": platform.platform(), "tokenizer": "code_aware_fixture_v1",
            "scoring": {"bm25": "idf*tf*(k1+1)/(tf+k1*(1-b+b*dl/avgdl))", "bm25_plus_delta": 1.0,
                        "bm25l_delta": 1.0, "k1": 1.5, "b": 0.75, "idf": "log1p_okapi", "tie_break": "doc_id ascending"},
            "result_limit": limit, "algorithms": reports,
            "storage_compatibility": {"implemented": ["offline corpus text", "in-memory scoring", "resource/chunk qrels"],
                "contract_only": ["projection-aware field scoring", "BM25F"],
                "missing": ["MDBX postings", "persistent lexical DBI", "production SearchProjection" ]}}


def self_test(path: Path) -> None:
    data = read_json(path)
    report = run(data, path, 10, 1)
    for algorithm in ALGORITHMS:
        expected = data["expected_top_ids"][algorithm]
        actual = {row["query_id"]: [hit["doc_id"] for hit in row["hits"]] for row in report["algorithms"][algorithm]["queries"]}
        for query_id, ids in expected.items():
            if actual.get(query_id, [])[:len(ids)] != ids:
                raise AssertionError(f"{algorithm}/{query_id}: expected {ids}, got {actual.get(query_id)}")
    for vector in data["golden_vectors"]:
        index = build_index({"corpus": vector["corpus"]})
        hits = score(index, vector["query"], vector["algorithm"], 10)
        if not hits or abs(hits[0]["score"] - vector["top_score"]) > 1e-9:
            raise AssertionError(f"golden vector failed: {vector['name']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(args.fixture)
        print("classical bake-off self-test: PASS")
        return 0
    report = run(read_json(args.fixture), args.fixture, args.limit, args.repeats)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
