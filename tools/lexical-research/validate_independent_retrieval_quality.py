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
    for judgment in data["judgments"]:
        pair = (judgment["query_id"], judgment["item_id"])
        if pair in pairs:
            raise AssertionError("duplicate judgment")
        pairs.add(pair)
        if judgment["query_id"] not in queries or judgment["item_id"] not in documents:
            raise AssertionError("judgment references unknown ID")
        if judgment["relevance_grade"] not in {0, 1, 2, 3}:
            raise AssertionError("relevance grade must be 0..3")
    if states["q:incomplete"] != "incomplete_qrels":
        raise AssertionError("incomplete qrels state missing")
    for query in data["queries"]:
        if query["class"] == "business_niche":
            contract = query.get("enumeration_contract", {})
            if not contract.get("required") or not contract.get("complete_source"):
                raise AssertionError("business-niche query lacks exhaustive contract")
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
        mutated = json.loads(args.fixture.read_text(encoding="utf-8"))
        mutated["judgments"].append(mutated["judgments"][0])
        try:
            validate(mutated)
        except AssertionError:
            pass
        else:
            raise AssertionError("duplicate judgment mutation was accepted")
    print("independent_retrieval_quality_fixture=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
