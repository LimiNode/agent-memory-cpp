#!/usr/bin/env python3
"""Validate the frozen lexical product-path feasibility contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


FIXTURE = Path(__file__).resolve().parent / "fixtures/lexical_product_path_feasibility.json"
OPERATIONS = {"import", "query", "update", "delete", "rebuild", "reopen"}
FIELDS = {"title", "heading", "body", "code", "tags", "summary", "question"}


def validate(document: dict) -> None:
    if document.get("schema_version") != 1:
        raise AssertionError("unsupported fixture schema")
    corpus = document["corpus"]
    if corpus["document_count"] != 1889 or corpus["qrels_status"] != "JUDGMENT_READINESS_ONLY":
        raise AssertionError("R3A identity or qrels status changed")
    paths = document["paths"]
    if not paths["same_admission"] or not paths["same_query_contract"]:
        raise AssertionError("comparison paths must share admission and query rules")
    names = {scale["name"] for scale in document["scale_ladder"]}
    if names != {"smoke", "small", "medium", "r3a_replay"}:
        raise AssertionError("scale ladder is incomplete")
    if set(document["lifecycle_operations"]) != OPERATIONS:
        raise AssertionError("lifecycle operations are incomplete")
    if not {"median_ms", "p95_ms", "rows_read", "bytes_written"}.issubset(document["metrics"]):
        raise AssertionError("required cost metrics are missing")
    if set(document["field_contract"]) != FIELDS:
        raise AssertionError("multi-field contract is incomplete")
    if "stale_projection" not in document["negative_cases"]:
        raise AssertionError("stale derived data must be rejected")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    args = parser.parse_args()
    document = json.loads(args.fixture.read_text(encoding="utf-8"))
    validate(document)
    if args.self_test:
        mutated = json.loads(args.fixture.read_text(encoding="utf-8"))
        mutated["paths"]["same_admission"] = False
        try:
            validate(mutated)
        except AssertionError:
            pass
        else:
            raise AssertionError("mutation must invalidate feasibility fixture")
    print("lexical_product_path_feasibility=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
