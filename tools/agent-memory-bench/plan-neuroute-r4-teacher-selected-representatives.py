#!/usr/bin/env python3
"""Validate and summarize the R4 teacher-selected representative study."""

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
            and value.get("family")
            == "neuroute_r4_teacher_selected_representatives",
            "R4 teacher-selection contract identity differs")
    require(value["route"]["dataset"] == "de-1m"
            and value["route"]["documents"] == 1000000
            and len(value["route"]["seeds"]) == 3,
            "R4 teacher-selection route differs")
    selection = value["teacher_selection"]
    require(selection["local_documents_per_positive_address"] == 4
            and selection["representatives_per_address"] == 32
            and selection["configuration_or_internal_labels_forbidden"] is True
            and selection["runtime_query_dependent_selection_forbidden"] is True,
            "R4 teacher-selection boundary differs")
    require(value["model"]["frozen_architecture"]
            == "actual_k32_learned_top8"
            and value["model"]["parameter_count"] == 21737,
            "R4 teacher-selection model differs")
    require(value["evaluation"]["candidate_fraction_budgets"]
            == [0.003, 0.004, 0.005]
            and value["evaluation"]["strict_prefix"] is True
            and value["decision"]["production_selection_forbidden"] is True,
            "R4 teacher-selection frontier differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    seeds = len(contract["route"]["seeds"])
    return {
        "dataset": contract["route"]["dataset"],
        "seed_count": seeds,
        "training_query_count": contract["query_partitions"]["training_queries"],
        "configuration_query_count": contract["query_partitions"][
            "configuration_queries"],
        "internal_query_count": contract["query_partitions"][
            "internal_evaluation_queries"],
        "teacher_selected_materializations": seeds,
        "model_fits": seeds,
        "candidate_fraction_budgets": contract["evaluation"][
            "candidate_fraction_budgets"],
        "configuration_or_internal_selection_queries": 0,
    }


def self_test(path: Path) -> None:
    value = plan(load_contract(path))
    require(value["teacher_selected_materializations"] == 3
            and value["model_fits"] == 3
            and value["configuration_or_internal_selection_queries"] == 0,
            "R4 teacher-selection planner self-test differs")
    print("NeuRoute R4 teacher-selected representative planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).resolve().parent /
                        "neuroute-r4-teacher-selected-representatives.example.json")
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
        print(f"plan-neuroute-r4-teacher-selected-representatives: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
