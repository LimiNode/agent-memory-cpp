#!/usr/bin/env python3
"""Evaluate an executable lexical receipt against independent qrels."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures/independent_retrieval_quality_v1.json"
K = 5


def dcg(grades: list[int]) -> float:
    return sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))


def validate_receipt(fixture: dict, receipt: dict, expected_fixture_sha256: str | None = None) -> dict:
    if receipt.get("receipt_id") != "lexical_quality_baseline_v1":
        raise AssertionError("unexpected receipt identity")
    if receipt.get("execution_status") != "EXECUTED":
        raise AssertionError("receipt is not an executed run")
    if receipt.get("fixture_id") != fixture.get("fixture_id"):
        raise AssertionError("receipt fixture identity mismatch")
    if expected_fixture_sha256 is not None:
        if receipt.get("fixture_sha256") != expected_fixture_sha256:
            raise AssertionError("receipt fixture digest does not match frozen fixture bytes")
    elif not isinstance(receipt.get("fixture_sha256"), str) or len(receipt["fixture_sha256"]) != 64:
        raise AssertionError("receipt fixture digest is missing")
    queries = {query["id"]: query for query in fixture["queries"]}
    runs = {run.get("query_id"): run for run in receipt.get("runs", [])}
    if set(runs) != set(queries):
        raise AssertionError("receipt must contain exactly one run per fixture query")
    documents = {document["id"]: document for document in fixture["documents"]}
    summary: dict[str, object] = {"k": K, "queries": {}, "aggregate": {}}
    complete_values: list[tuple[float, float]] = []
    no_answer_violations = 0
    wrong_scope_violations = 0
    stale_hit_violations = 0
    for query_id, query in queries.items():
        run = runs[query_id]
        returned = run.get("returned", [])
        ids = [item.get("document_id") for item in returned]
        if len(ids) != len(set(ids)) or any(item_id not in documents for item_id in ids):
            raise AssertionError(f"{query_id} returned duplicate or unknown document IDs")
        if any(documents[item_id]["scope_id"] != query["scope_id"] for item_id in ids):
            raise AssertionError(f"{query_id} returned a cross-scope document")
        if any(not isinstance(item.get("score"), (int, float)) or not math.isfinite(item["score"])
               for item in returned):
            raise AssertionError(f"{query_id} has invalid producer score")
        state = fixture["query_states"][query_id]
        grades = {
            judgment["item_id"]: judgment["relevance_grade"]
            for judgment in fixture["judgments"]
            if judgment["query_id"] == query_id
        }
        row: dict[str, object] = {"returned_count": len(ids)}
        if state == "incomplete_qrels":
            row["status"] = "EXCLUDED_INCOMPLETE_QRELS"
        elif query["class"] == "business_niche":
            row["status"] = "NOT_AVAILABLE_EXHAUSTIVE_ENUMERATION"
        elif state == "judged_zero_relevant" and query["class"] == "wrong_scope":
            forbidden = set(query.get("access_control", {}).get("forbidden_document_ids", []))
            hits = sorted(forbidden & set(ids))
            row["forbidden_hits"] = hits
            row["status"] = "PASS" if not ids else "FAIL_WRONG_SCOPE"
            if row["status"] != "PASS": wrong_scope_violations += len(ids)
        elif state == "judged_zero_relevant":
            expected_empty = query.get("outcome_contract", {}).get("expected_empty", False)
            row["status"] = "PASS" if expected_empty and not ids else "FAIL_NO_ANSWER"
            if row["status"] != "PASS": no_answer_violations += 1
        elif query["class"] == "stale_derived_knowledge":
            forbidden = set(query.get("outcome_contract", {}).get("must_reject_ids", []))
            hits = sorted(forbidden & set(ids))
            row["status"] = "PASS" if not hits else "STALE_HIT_VIOLATION"
            row["forbidden_hits"] = hits
            stale_hit_violations += len(hits)
        else:
            ranked_grades = [grades.get(item_id, 0) for item_id in ids[:K]]
            relevant = {item_id for item_id, grade in grades.items() if grade >= 2}
            retrieved_relevant = len(set(ids[:K]) & relevant)
            recall = retrieved_relevant / len(relevant) if relevant else 0.0
            ideal = sorted(grades.values(), reverse=True)[:K]
            ideal_dcg = dcg(ideal)
            ndcg = dcg(ranked_grades) / ideal_dcg if ideal_dcg else 0.0
            row.update({"status": "MEASURED", "recall_at_5": recall, "ndcg_at_5": ndcg})
            complete_values.append((recall, ndcg))
        summary["queries"][query_id] = row
    summary["aggregate"] = {
        "measured_query_count": len(complete_values),
        "mean_recall_at_5": sum(value[0] for value in complete_values) / len(complete_values)
        if complete_values else 0.0,
        "mean_ndcg_at_5": sum(value[1] for value in complete_values) / len(complete_values)
        if complete_values else 0.0,
        "no_answer_violations": no_answer_violations,
        "wrong_scope_violations": wrong_scope_violations,
        "stale_hit_violations": stale_hit_violations,
        "quality_status": "MEASURED_WITHOUT_ACCESS_VIOLATION"
        if not no_answer_violations and not wrong_scope_violations
        else "CONTRACT_VIOLATION",
        "tail_claim": "not measured; no p99 or stability claim",
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    if args.self_test:
        sample = {
            "receipt_id": "lexical_quality_baseline_v1",
            "execution_status": "EXECUTED",
            "fixture_id": fixture["fixture_id"],
            "fixture_sha256": "0" * 64,
            "runs": [
                {"query_id": query["id"], "returned": []}
                for query in fixture["queries"]
            ],
        }
        validate_receipt(fixture, sample)
        bad = json.loads(json.dumps(sample))
        bad["runs"][0]["returned"] = [{"document_id": "doc:private-tool", "score": 1.0}]
        try:
            validate_receipt(fixture, bad)
        except AssertionError:
            pass
        else:
            raise AssertionError("cross-scope mutation was accepted")
        print("lexical_quality_evaluation=PASS")
        return 0
    if args.receipt is None:
        parser.error("--receipt is required unless --self-test is used")
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    expected_digest = hashlib.sha256(args.fixture.read_bytes()).hexdigest()
    summary = validate_receipt(fixture, receipt, expected_digest)
    if args.output:
        args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary["aggregate"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
