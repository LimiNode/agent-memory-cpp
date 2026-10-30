#!/usr/bin/env python3
"""Fail-closed validator and mutation self-test for the lexical R2 receipt."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any


EXPECTED_BACKENDS = {"bm25", "flat-v2", "segmented-v2"}
EXPECTED_TOLERANCE = 1e-5
EXPECTED_CORPUS = "lexical_retrieval_qualification_r2_corpus.tsv"
EXPECTED_QUERIES = "lexical_retrieval_qualification_r2_queries.tsv"
EXPECTED_REVISIONS = {
    "bm25": "ExactLexicalIndex@production-bm25-v1",
    "flat-v2": "MdbxPersistentLexicalIndex@flat-v2",
    "segmented-v2": "MdbxSegmentedLexicalIndex@segmented-v2",
}


class ReceiptError(ValueError):
    """Raised when a receipt cannot prove the R2 contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReceiptError(message)


def finite_number(value: Any, message: str) -> float:
    require(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value),
        message,
    )
    return float(value)


def fnv1a64(path: Path) -> str:
    value = 14695981039346656037
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ReceiptError(f"cannot read fixture for digest: {path}: {exc}") from exc
    for byte in payload:
        value ^= byte
        value = (value * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return f"{value:016x}"


def validate_receipt(receipt: dict[str, Any], fixture_root: Path | None = None) -> None:
    require(receipt.get("schema_version") == 2, "unsupported R2 receipt schema")
    require(
        receipt.get("protocol") == "lexical_retrieval_qualification_r2",
        "unsupported R2 receipt protocol",
    )
    query_count = receipt.get("query_count")
    require(isinstance(query_count, int) and not isinstance(query_count, bool) and query_count > 0,
            "invalid R2 query count")
    require(receipt.get("candidate_depth") == 10, "R2 candidate depth must be 10")
    tolerance = finite_number(receipt.get("score_tolerance"), "invalid R2 score tolerance")
    require(tolerance == EXPECTED_TOLERANCE, "R2 score tolerance revision changed")

    query_ids = receipt.get("query_ids")
    require(
        isinstance(query_ids, list)
        and len(query_ids) == query_count
        and all(isinstance(value, str) and value for value in query_ids)
        and len(set(query_ids)) == query_count,
        "R2 query identity list is invalid",
    )
    measurement_paths = receipt.get("measurement_paths")
    require(
        measurement_paths == {
            "bm25": "production_exact_search",
            "durable": "diagnostic_snapshot_bm25",
        },
        "R2 measurement paths are ambiguous",
    )
    require(receipt.get("tokenizer_revision") == "standard-tokenizer-v1",
            "R2 tokenizer revision is missing or changed")
    require(receipt.get("scorer_revision") == "bm25-exact-v1",
            "R2 scorer revision is missing or changed")

    fixtures = receipt.get("fixtures")
    require(isinstance(fixtures, dict), "R2 fixture identity is missing")
    for field in ("corpus", "queries"):
        require(isinstance(fixtures.get(field), str) and fixtures[field],
                f"R2 fixture {field} name is invalid")
    require(fixtures["corpus"] == EXPECTED_CORPUS and fixtures["queries"] == EXPECTED_QUERIES,
            "R2 fixture identity changed")
    require(fixtures.get("digest_algorithm") == "fnv1a64", "R2 digest algorithm is invalid")
    for field in ("corpus_digest", "query_digest"):
        digest = fixtures.get(field)
        require(isinstance(digest, str) and len(digest) == 16,
                f"R2 fixture {field} is invalid")
        require(all(character in "0123456789abcdef" for character in digest),
                f"R2 fixture {field} is not canonical hex")
    if fixture_root is not None:
        require(fnv1a64(fixture_root / fixtures["corpus"]) == fixtures["corpus_digest"],
                "R2 corpus digest mismatch")
        require(fnv1a64(fixture_root / fixtures["queries"]) == fixtures["query_digest"],
                "R2 query digest mismatch")

    rows = receipt.get("backends")
    require(
        isinstance(rows, list)
        and len(rows) == len(EXPECTED_BACKENDS)
        and {row.get("id") for row in rows if isinstance(row, dict)} == EXPECTED_BACKENDS,
        "R2 receipt must contain BM25, flat-v2, and segmented-v2",
    )
    by_backend = {row["id"]: row for row in rows}
    references: dict[str, dict[str, Any]] = {}
    for backend_id, row in by_backend.items():
        require(row.get("revision") == EXPECTED_REVISIONS[backend_id],
                f"backend revision is invalid for {backend_id}")
        expected_path = "production_exact_search" if backend_id == "bm25" else "diagnostic_snapshot_bm25"
        require(row.get("query_path") == expected_path, f"query path is invalid for {backend_id}")
        for field in ("index_build_ms", "update_ms", "reopen_ms", "index_bytes"):
            value = finite_number(row.get(field), f"invalid lifecycle field {field} for {backend_id}")
            require(value >= 0, f"negative lifecycle field {field} for {backend_id}")
            if field == "index_bytes" and backend_id != "bm25":
                require(value > 0, f"durable index has no bytes for {backend_id}")
        quality = row.get("quality")
        require(isinstance(quality, dict), f"quality metrics missing for {backend_id}")
        for field in ("recall_at_1", "recall_at_5", "ndcg_at_10", "mrr", "no_answer_accuracy"):
            value = finite_number(quality.get(field), f"invalid quality metric {field} for {backend_id}")
            require(0.0 <= value <= 1.0, f"quality metric out of range for {backend_id}")
        timing = row.get("latency_ms")
        require(
            isinstance(timing, dict)
            and timing.get("sample_count") == query_count
            and timing.get("observation_unit") == "one_query"
            and timing.get("statistical_status") == "directional",
            f"R2 timing semantics are incomplete for {backend_id}",
        )
        for field in ("p50", "p95", "p99"):
            require(finite_number(timing.get(field), f"invalid latency percentile {field}") >= 0,
                    f"negative latency percentile for {backend_id}")

        evidence = row.get("query_evidence")
        require(isinstance(evidence, list) and len(evidence) == query_count,
                f"per-query evidence is incomplete for {backend_id}")
        observed: dict[str, dict[str, Any]] = {}
        for item in evidence:
            require(isinstance(item, dict), f"invalid query evidence for {backend_id}")
            query_id = item.get("id")
            require(query_id in query_ids and query_id not in observed,
                    f"query evidence identity is invalid for {backend_id}")
            ranked_ids = item.get("ranked_ids")
            scores = item.get("scores")
            candidate_count = item.get("candidate_count")
            require(
                isinstance(ranked_ids, list)
                and isinstance(scores, list)
                and isinstance(candidate_count, int)
                and not isinstance(candidate_count, bool)
                and candidate_count == len(ranked_ids) == len(scores)
                and candidate_count <= receipt["candidate_depth"],
                f"candidate evidence is invalid for {backend_id}/{query_id}",
            )
            require(all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in ranked_ids),
                    f"ranked IDs are invalid for {backend_id}/{query_id}")
            require(len(set(ranked_ids)) == len(ranked_ids),
                    f"ranked IDs are duplicated for {backend_id}/{query_id}")
            for score in scores:
                finite_number(score, f"scores are invalid for {backend_id}/{query_id}")
            observed[query_id] = {"ranked_ids": ranked_ids, "scores": scores}
        require(set(observed) == set(query_ids), f"query evidence set is incomplete for {backend_id}")
        references[backend_id] = observed

    baseline = references["bm25"]
    for backend_id, observed in references.items():
        ranking_parity = True
        score_parity = True
        for query_id in query_ids:
            expected = baseline[query_id]
            actual = observed[query_id]
            ranking_parity = ranking_parity and actual["ranked_ids"] == expected["ranked_ids"]
            score_parity = score_parity and len(actual["scores"]) == len(expected["scores"])
            if len(actual["scores"]) == len(expected["scores"]):
                score_parity = score_parity and all(
                    abs(float(lhs) - float(rhs)) <= tolerance
                    for lhs, rhs in zip(actual["scores"], expected["scores"])
                )
        require(by_backend[backend_id].get("ranking_parity") is ranking_parity,
                f"ranking parity claim is not supported for {backend_id}")
        require(by_backend[backend_id].get("score_parity") is score_parity,
                f"score parity claim is not supported for {backend_id}")

    require(receipt.get("quality_status") == "QUALITY_INCONCLUSIVE",
            "R2 must not claim a quality conclusion without independent qrels")
    require(receipt.get("hydration_status") == "NOT_MEASURED_NO_CANONICAL_READ_PATH",
            "R2 must not claim canonical hydration evidence")


def validate(path: Path) -> None:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        validate_receipt(receipt, Path.cwd() / "tools" / "lexical-research" / "fixtures")
    except (OSError, json.JSONDecodeError, ReceiptError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"status": "PASS", "receipt": path.name}, sort_keys=True))


def self_test() -> None:
    evidence = [{"id": "q", "candidate_count": 1, "ranked_ids": [1], "scores": [1.0]}]
    receipt: dict[str, Any] = {
        "schema_version": 2,
        "protocol": "lexical_retrieval_qualification_r2",
        "fixtures": {
            "corpus": EXPECTED_CORPUS,
            "queries": EXPECTED_QUERIES,
            "digest_algorithm": "fnv1a64",
            "corpus_digest": "0123456789abcdef",
            "query_digest": "fedcba9876543210",
        },
        "tokenizer_revision": "standard-tokenizer-v1",
        "scorer_revision": "bm25-exact-v1",
        "candidate_depth": 10,
        "score_tolerance": EXPECTED_TOLERANCE,
        "query_count": 1,
        "query_ids": ["q"],
        "measurement_paths": {"bm25": "production_exact_search", "durable": "diagnostic_snapshot_bm25"},
        "backends": [],
        "quality_status": "QUALITY_INCONCLUSIVE",
        "hydration_status": "NOT_MEASURED_NO_CANONICAL_READ_PATH",
    }
    for backend_id in ("bm25", "flat-v2", "segmented-v2"):
        receipt["backends"].append({
            "id": backend_id,
            "revision": EXPECTED_REVISIONS[backend_id],
            "query_path": "production_exact_search" if backend_id == "bm25" else "diagnostic_snapshot_bm25",
            "ranking_parity": True,
            "score_parity": True,
            "index_build_ms": 0.0,
            "update_ms": 0.0,
            "reopen_ms": 0.0,
            "index_bytes": 0 if backend_id == "bm25" else 1,
            "quality": {field: 0.0 for field in ("recall_at_1", "recall_at_5", "ndcg_at_10", "mrr", "no_answer_accuracy")},
            "latency_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "sample_count": 1, "observation_unit": "one_query", "statistical_status": "directional"},
            "query_evidence": copy.deepcopy(evidence),
        })
    validate_receipt(receipt)
    mutations = []
    ranked_id = copy.deepcopy(receipt)
    ranked_id["backends"][1]["query_evidence"][0]["ranked_ids"] = [2]
    mutations.append(ranked_id)
    score = copy.deepcopy(receipt)
    score["backends"][1]["query_evidence"][0]["scores"] = [1.1]
    mutations.append(score)
    tolerance = copy.deepcopy(receipt)
    tolerance["score_tolerance"] = 2e-5
    mutations.append(tolerance)
    nonfinite = copy.deepcopy(receipt)
    nonfinite["backends"][1]["query_evidence"][0]["scores"] = [float("nan")]
    mutations.append(nonfinite)
    digest = copy.deepcopy(receipt)
    digest["fixtures"]["corpus_digest"] = "0000000000000000"
    digest["fixtures"]["corpus"] = "not-a-fixture-digest"
    mutations.append(digest)
    for mutation in mutations:
        try:
            validate_receipt(mutation)
        except ReceiptError:
            continue
        raise SystemExit("R2 validator mutation self-test accepted a corrupt receipt")
    print(json.dumps({"status": "PASS", "mutations": len(mutations)}, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", type=Path, nargs="?")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.receipt is None:
        parser.error("receipt is required unless --self-test is used")
    validate(args.receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
