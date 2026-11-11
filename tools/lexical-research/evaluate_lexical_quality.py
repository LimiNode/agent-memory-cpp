#!/usr/bin/env python3
"""Evaluate an executable lexical receipt against independent qrels."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures/independent_retrieval_quality_v1.json"
K = 5


def dcg(grades: list[int]) -> float:
    return sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))


def validate_receipt(
    fixture: dict,
    receipt: dict,
    expected_fixture_sha256: str | None = None,
    expected_source_commit: str | None = None,
) -> dict:
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
    source_commit = receipt.get("source_commit")
    if not isinstance(source_commit, str) or (
        source_commit != "UNPINNED" and
        (len(source_commit) != 40 or any(char not in "0123456789abcdef" for char in source_commit.lower()))
    ):
        raise AssertionError("receipt source commit is missing or malformed")
    if expected_source_commit is not None and source_commit != expected_source_commit:
        raise AssertionError("receipt source commit does not match the executed source HEAD")
    queries = {query["id"]: query for query in fixture["queries"]}
    if len(queries) != len(fixture["queries"]):
        raise AssertionError("fixture contains duplicate query IDs")
    judgments_by_query: dict[str, dict[str, int]] = {query_id: {} for query_id in queries}
    for judgment in fixture.get("judgments", []):
        query_id = judgment.get("query_id")
        item_id = judgment.get("item_id")
        if query_id not in queries:
            raise AssertionError("judgment references an unknown query")
        if item_id not in queries[query_id].get("eligible_document_ids", []):
            raise AssertionError("judgment references an ineligible document")
        if item_id in judgments_by_query[query_id]:
            raise AssertionError("fixture contains duplicate or conflicting judgments")
        grade = judgment.get("relevance_grade")
        if not isinstance(grade, int) or grade < 0:
            raise AssertionError("judgment relevance grade is malformed")
        judgments_by_query[query_id][item_id] = grade
    run_list = receipt.get("runs", [])
    run_ids = [run.get("query_id") for run in run_list]
    if len(run_ids) != len(set(run_ids)):
        raise AssertionError("receipt contains duplicate query runs")
    runs = {run_id: run for run_id, run in zip(run_ids, run_list)}
    if set(runs) != set(queries):
        raise AssertionError("receipt must contain exactly one run per fixture query")
    documents = {document["id"]: document for document in fixture["documents"]}
    summary: dict[str, object] = {"k": K, "queries": {}, "aggregate": {}}
    complete_values: list[tuple[float, float]] = []
    no_answer_violations = 0
    scope_leak_violations = 0
    permitted_irrelevant_hits = 0
    for query_id, query in queries.items():
        run = runs[query_id]
        if run.get("scope_id") != query["scope_id"]:
            raise AssertionError(f"{query_id} receipt scope does not match fixture scope")
        returned = run.get("returned", [])
        ids = [item.get("document_id") for item in returned]
        if len(ids) != len(set(ids)) or any(item_id not in documents for item_id in ids):
            raise AssertionError(f"{query_id} returned duplicate or unknown document IDs")
        forbidden = set(query.get("access_control", {}).get("forbidden_document_ids", []))
        forbidden_hits = sorted(forbidden & set(ids))
        scope_mismatch_hits = sorted(
            item_id for item_id in ids if documents[item_id]["scope_id"] != query["scope_id"]
        )
        scope_leak_violations += len(set(forbidden_hits) | set(scope_mismatch_hits))
        if any(not isinstance(item.get("score"), (int, float)) or not math.isfinite(item["score"])
               for item in returned):
            raise AssertionError(f"{query_id} has invalid producer score")
        state = fixture["query_states"][query_id]
        grades = judgments_by_query[query_id]
        eligible = set(query.get("eligible_document_ids", []))
        judged = set(grades)
        if state == "complete_qrels" and judged != eligible:
            raise AssertionError(f"{query_id} complete qrels do not cover the eligible pool")
        if state == "incomplete_qrels" and judged >= eligible:
            raise AssertionError(f"{query_id} is marked incomplete but covers the eligible pool")
        row: dict[str, object] = {"returned_count": len(ids)}
        if state == "incomplete_qrels":
            row["status"] = "EXCLUDED_INCOMPLETE_QRELS"
        elif query["class"] == "business_niche":
            row["status"] = "NOT_AVAILABLE_EXHAUSTIVE_ENUMERATION"
        elif state == "judged_zero_relevant" and query["class"] == "wrong_scope":
            row["forbidden_hits"] = forbidden_hits
            row["scope_mismatch_hits"] = scope_mismatch_hits
            if forbidden_hits or scope_mismatch_hits:
                row["status"] = "FAIL_SCOPE_LEAK"
            elif ids:
                row["status"] = "PERMITTED_IRRELEVANT_HIT"
                permitted_irrelevant_hits += len(ids)
            else:
                row["status"] = "ACCESS_FILTER_PASS"
        elif state == "judged_zero_relevant":
            expected_empty = query.get("outcome_contract", {}).get("expected_empty", False)
            row["status"] = "PASS" if expected_empty and not ids else "FAIL_NO_ANSWER"
            if row["status"] != "PASS": no_answer_violations += 1
        elif query["class"] == "stale_derived_knowledge":
            # The fixture's derived_revision label is not an admitted
            # publication generation.  A real freshness conformance test is
            # tracked separately; this query is therefore not evidence.
            row["status"] = "NOT_IMPLEMENTED_FRESHNESS"
            row["freshness_contract"] = "requires admitted revision update and snapshot final admission"
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
        "scope_leak_violations": scope_leak_violations,
        "permitted_irrelevant_hits": permitted_irrelevant_hits,
        "stale_hit_status": "NOT_EVALUATED",
        "quality_status": (
            "ACCESS_CONTRACT_VIOLATION" if scope_leak_violations else
            "NO_ANSWER_CONTRACT_VIOLATION" if no_answer_violations else
            "MEASURED_WITH_QUALITY_WARNINGS" if permitted_irrelevant_hits else
            "MEASURED_WITHOUT_ACCESS_VIOLATION"
        ),
        "freshness_status": "NOT_IMPLEMENTED_FRESHNESS_CONFORMANCE",
        "tail_claim": "not measured; no p99 or stability claim",
    }
    return summary


def _expect_cli_failure(fixture_path: Path, receipt: dict, message: str) -> None:
    with tempfile.TemporaryDirectory(prefix="lexical-quality-negative-") as directory:
        receipt_path = Path(directory) / "mutated-receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()),
             "--fixture", str(fixture_path), "--receipt", str(receipt_path)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    if args.self_test:
        sample = {
            "receipt_id": "lexical_quality_baseline_v1",
            "execution_status": "EXECUTED",
            "fixture_id": fixture["fixture_id"],
            "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
            "source_commit": "UNPINNED",
            "runs": [
                {"query_id": query["id"], "scope_id": query["scope_id"], "returned": []}
                for query in fixture["queries"]
            ],
        }
        validate_receipt(fixture, sample)
        duplicate = json.loads(json.dumps(sample))
        duplicate["runs"].append(dict(duplicate["runs"][0]))
        try:
            validate_receipt(fixture, duplicate)
        except AssertionError:
            pass
        else:
            raise AssertionError("duplicate query run was accepted")
        missing = json.loads(json.dumps(sample))
        missing["runs"] = missing["runs"][1:]
        try:
            validate_receipt(fixture, missing)
        except AssertionError:
            pass
        else:
            raise AssertionError("missing query run was accepted")
        forged = json.loads(json.dumps(sample))
        try:
            validate_receipt(fixture, forged, expected_source_commit="a" * 40)
        except AssertionError:
            pass
        else:
            raise AssertionError("forged source commit was accepted")
        stale_digest = json.loads(json.dumps(sample))
        try:
            validate_receipt(fixture, stale_digest, expected_fixture_sha256="f" * 64)
        except AssertionError:
            pass
        else:
            raise AssertionError("stale fixture digest was accepted")
        irrelevant = json.loads(json.dumps(sample))
        irrelevant_run = next(run for run in irrelevant["runs"] if run["query_id"] == "q:wrong-scope")
        irrelevant_run["returned"] = [{"document_id": "doc:article-mdbx", "score": 1.0}]
        irrelevant_summary = validate_receipt(fixture, irrelevant)
        if irrelevant_summary["aggregate"]["permitted_irrelevant_hits"] != 1:
            raise AssertionError("permitted irrelevant hit was not classified separately")
        no_answer = json.loads(json.dumps(sample))
        no_answer_run = next(run for run in no_answer["runs"] if run["query_id"] == "q:no-answer")
        no_answer_run["returned"] = [{"document_id": "doc:article-mdbx", "score": 1.0}]
        no_answer_summary = validate_receipt(fixture, no_answer)
        if no_answer_summary["aggregate"]["no_answer_violations"] != 1:
            raise AssertionError("no-answer mutation was not classified")
        _expect_cli_failure(args.fixture, no_answer,
                            "no-answer contract violation returned CLI success")
        bad = json.loads(json.dumps(sample))
        bad_run = bad["runs"][0]
        bad_run["returned"] = [{"document_id": "doc:private-tool", "score": 1.0}]
        bad_summary = validate_receipt(fixture, bad)
        if bad_summary["aggregate"]["scope_leak_violations"] != 1:
            raise AssertionError("cross-scope mutation was not classified")
        _expect_cli_failure(args.fixture, bad,
                            "scope-leak mutation returned CLI success")
        scope_tampered = json.loads(json.dumps(sample))
        scope_tampered["runs"][0]["scope_id"] = "private"
        try:
            validate_receipt(fixture, scope_tampered)
        except AssertionError:
            pass
        else:
            raise AssertionError("receipt scope tampering was accepted")
        duplicate_judgment = json.loads(json.dumps(fixture))
        duplicate_judgment["judgments"].append({
            "query_id": "q:exact-mdbx",
            "item_id": "doc:article-mdbx",
            "relevance_grade": 3,
        })
        try:
            validate_receipt(duplicate_judgment, sample)
        except AssertionError:
            pass
        else:
            raise AssertionError("duplicate judgment was accepted")
        incomplete_judgment = json.loads(json.dumps(fixture))
        incomplete_judgment["judgments"] = [
            judgment for judgment in incomplete_judgment["judgments"]
            if not (judgment["query_id"] == "q:exact-mdbx" and
                    judgment["item_id"] == "doc:article-mdbx")
        ]
        try:
            validate_receipt(incomplete_judgment, sample)
        except AssertionError:
            pass
        else:
            raise AssertionError("incomplete complete_qrels was accepted")
        print("lexical_quality_evaluation=PASS")
        return 0
    if args.receipt is None:
        parser.error("--receipt is required unless --self-test is used")
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    expected_digest = hashlib.sha256(args.fixture.read_bytes()).hexdigest()
    expected_source_commit = args.expected_source_commit
    if expected_source_commit is None:
        import os
        expected_source_commit = os.environ.get("AGENT_MEMORY_SOURCE_COMMIT")
    summary = validate_receipt(fixture, receipt, expected_digest, expected_source_commit)
    if args.output:
        args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary["aggregate"], sort_keys=True))
    return 2 if (
        summary["aggregate"]["scope_leak_violations"] or
        summary["aggregate"]["no_answer_violations"]
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
