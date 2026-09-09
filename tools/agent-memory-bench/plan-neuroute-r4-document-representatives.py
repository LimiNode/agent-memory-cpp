#!/usr/bin/env python3
"""Validate and summarize the frozen R4 document-representative protocol."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1
            and value.get("family") == "neuroute_r4_document_representatives",
            "R4 representative contract identity differs")
    require(value["route"]["dataset"] == "de-1m"
            and value["route"]["documents"] == 1000000
            and value["route"]["width"] == 16
            and len(value["route"]["seeds"]) == 3,
            "R4 representative route differs")
    representatives = value["representatives"]
    require(representatives["maximum_actual_documents_per_address"] == 32
            and representatives["reported_prefixes"] == [8, 16, 32]
            and representatives["query_independent"] is True
            and representatives["teacher_blind"] is True,
            "R4 representative ladder differs")
    require(value["controls"]["replication_forbidden"] is True
            and value["decision"]["teacher_trained_selection_forbidden"] is True
            and value["decision"]["production_selection_forbidden"] is True,
            "R4 representative boundary differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset": contract["route"]["dataset"],
        "seed_count": len(contract["route"]["seeds"]),
        "document_count": contract["route"]["documents"],
        "representative_prefixes": contract["representatives"]["reported_prefixes"],
        "materialized_seed_artifact_count": len(contract["route"]["seeds"]) * 4,
        "learned_model_fits": 0,
        "teacher_queries_read": 0,
        "replication_factor": 1.0,
    }


def self_test(path: Path) -> None:
    value = plan(load_contract(path))
    require(value["materialized_seed_artifact_count"] == 12
            and value["learned_model_fits"] == 0
            and value["teacher_queries_read"] == 0,
            "R4 representative planner self-test differs")
    print("NeuRoute R4 document-representative planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).resolve().parent /
                        "neuroute-r4-document-representatives.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test(args.contract)
        else:
            print(json.dumps(plan(load_contract(args.contract)), indent=2,
                             sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-r4-document-representatives: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
