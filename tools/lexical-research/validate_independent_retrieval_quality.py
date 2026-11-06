#!/usr/bin/env python3
"""Validate manually authored, backend-independent retrieval qrels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


FIXTURE = Path(__file__).resolve().parent / "fixtures/independent_retrieval_quality_v1.json"
REQUIRED_CLASSES = {
    "exact_terminology", "synonym_paraphrase", "document_level_topic",
    "section_level_topic", "business_niche", "tool_service", "procedure_lifehack",
    "cross_document_comparison", "stale_derived_knowledge", "no_answer", "wrong_scope",
}
VALID_STATES = {"complete_qrels", "judged_zero_relevant", "incomplete_qrels", "unjudged"}


def validate(data: dict) -> None:
    if data.get("fixture_status") != "MANUALLY_JUDGED_SYNTHETIC":
        raise AssertionError("fixture must remain explicitly synthetic/manual")
    if "retriever" in data.get("judgment_protocol", {}) or "scores" in data.get("judgment_protocol", {}):
        raise AssertionError("judgment protocol must not depend on evaluated backend output")
    documents = {item["id"]: item for item in data["documents"]}
    queries = {query["id"]: query for query in data["queries"]}
    if len(documents) != len(data["documents"]) or len(queries) != len(data["queries"]):
        raise AssertionError("document or query IDs are duplicated")
    if {query["class"] for query in data["queries"]} != REQUIRED_CLASSES | {"incomplete_pool_control"}:
        raise AssertionError("query class coverage is incomplete")
    states = data["query_states"]
    if set(states) != set(queries):
        raise AssertionError("every query needs an explicit state")
    pairs = set()
    judgments_by_query = {query_id: {} for query_id in queries}
    for judgment in data["judgments"]:
        pair = (judgment["query_id"], judgment["item_id"])
        if pair in pairs:
            raise AssertionError("duplicate judgment")
        pairs.add(pair)
        if judgment["query_id"] not in queries or judgment["item_id"] not in documents:
            raise AssertionError("judgment references unknown ID")
        if judgment["relevance_grade"] not in {0, 1, 2, 3}:
            raise AssertionError("relevance grade must be 0..3")
        judgments_by_query[judgment["query_id"]][judgment["item_id"]] = judgment["relevance_grade"]
    for query in data["queries"]:
        query_id = query["id"]
        eligible = query.get("eligible_document_ids")
        if not isinstance(eligible, list) or not eligible:
            raise AssertionError(f"{query_id} lacks an explicit eligible pool")
        if len(set(eligible)) != len(eligible) or any(item not in documents for item in eligible):
            raise AssertionError(f"{query_id} has an invalid eligible pool")
        if any(documents[item]["scope_id"] != query["scope_id"] for item in eligible):
            raise AssertionError(f"{query_id} eligible pool crosses scope boundary")
        state = states[query_id]
        if state not in VALID_STATES:
            raise AssertionError(f"{query_id} has an unknown qrels state")
        judged = judgments_by_query[query_id]
        judged_ids = set(judged)
        eligible_ids = set(eligible)
        if not judged_ids.issubset(eligible_ids):
            raise AssertionError(f"{query_id} judges an ineligible document")
        coverage = data.get("judgment_coverage", {}).get(query_id, {})
        if coverage.get("eligible_count") != len(eligible) or coverage.get("judged_count") != len(judged):
            raise AssertionError(f"{query_id} coverage receipt does not match qrels")
        if coverage.get("unjudged_count") != len(eligible_ids - judged_ids):
            raise AssertionError(f"{query_id} unjudged count is inconsistent")
        if state in {"complete_qrels", "judged_zero_relevant"}:
            if judged_ids != eligible_ids or coverage.get("unjudged_count") != 0:
                raise AssertionError(f"{query_id} is not closed-world complete")
        elif state == "incomplete_qrels":
            if not judged_ids or coverage.get("unjudged_count") == 0:
                raise AssertionError(f"{query_id} must expose unjudged documents")
        elif judged_ids:
            raise AssertionError(f"unjudged query {query_id} must not carry qrels")
    if states["q:incomplete"] != "incomplete_qrels":
        raise AssertionError("incomplete qrels state missing")
    for query in data["queries"]:
        if query["class"] == "business_niche":
            contract = query.get("enumeration_contract", {})
            if not contract.get("required") or not contract.get("complete_source"):
                raise AssertionError("business-niche query lacks exhaustive contract")
            expected = contract.get("expected_items", [])
            covered = contract.get("covered_items", [])
            if not contract.get("declared_complete") or sorted(expected) != sorted(covered):
                raise AssertionError("business-niche enumeration is not complete")
    wrong_scope = next(query for query in data["queries"] if query["class"] == "wrong_scope")
    forbidden = wrong_scope.get("access_control", {}).get("forbidden_document_ids", [])
    if not forbidden or set(forbidden) & set(wrong_scope["eligible_document_ids"]):
        raise AssertionError("wrong-scope query lacks a separate hard-filter contract")
    stale = next(query for query in data["queries"] if query["class"] == "stale_derived_knowledge")
    if "doc:stale-summary" not in stale.get("outcome_contract", {}).get("must_reject_ids", []):
        raise AssertionError("stale-hit rejection contract is missing")
    if not any(state == "judged_zero_relevant" for state in states.values()):
        raise AssertionError("zero-relevant judged controls are missing")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.fixture.read_text(encoding="utf-8"))
    validate(data)
    if args.self_test:
        mutations = []
        mutated = json.loads(args.fixture.read_text(encoding="utf-8"))
        mutated["judgments"].append(mutated["judgments"][0])
        mutations.append(("duplicate judgment", mutated))
        incomplete = json.loads(args.fixture.read_text(encoding="utf-8"))
        incomplete["judgments"] = [
            judgment for judgment in incomplete["judgments"]
            if judgment["query_id"] != "q:exact-mdbx"
            or judgment["item_id"] != "doc:unrelated"
        ]
        incomplete["judgment_coverage"]["q:exact-mdbx"]["judged_count"] -= 1
        incomplete["judgment_coverage"]["q:exact-mdbx"]["unjudged_count"] += 1
        mutations.append(("implicit unjudged complete qrels", incomplete))
        hard_filter = json.loads(args.fixture.read_text(encoding="utf-8"))
        hard_filter["judgments"].append({
            "query_id": "q:wrong-scope", "item_id": "doc:private-tool", "relevance_grade": 3
        })
        hard_filter["judgment_coverage"]["q:wrong-scope"]["judged_count"] += 1
        mutations.append(("cross-scope qrel", hard_filter))
        for label, case in mutations:
            try:
                validate(case)
            except AssertionError:
                pass
            else:
                raise AssertionError(f"{label} mutation was accepted")
    print("independent_retrieval_quality_fixture=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
