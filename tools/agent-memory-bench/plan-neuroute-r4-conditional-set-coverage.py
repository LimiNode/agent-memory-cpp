#!/usr/bin/env python3
"""Validate and summarize the frozen-K32 conditional set-coverage study."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def treatments(contract: dict[str, Any]) -> list[str]:
    return [row["id"] for row in contract["selection_recipes"]]


def learned_treatments(contract: dict[str, Any]) -> list[str]:
    frozen = contract["evaluation"]["frozen_parent_control"]
    return [value for value in treatments(contract) if value != frozen]


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1
            and value.get("family") == "neuroute_r4_conditional_set_coverage",
            "R4 conditional-coverage contract identity differs")
    require(value["route"]["dataset"] == "de-1m"
            and value["route"]["documents"] == 1000000
            and len(value["route"]["seeds"]) == 3,
            "R4 conditional-coverage route differs")
    require(value["representatives"]["selected_k"] == 32
            and value["representatives"]["saturation_parent_selected_k"] == 32
            and value["representatives"]["query_independent"] is True,
            "R4 conditional-coverage K differs")
    expected = [
        ("ff32", 32, False),
        ("coverage32_empty", 0, True),
        ("centroid1_coverage31", 1, True),
        ("ff8_coverage24", 8, True),
        ("ff16_coverage16", 16, True),
        ("independent_wins32", 0, False),
    ]
    actual = [(row["id"], row["farthest_first_anchor_count"],
               row["conditional_coverage_fill"])
              for row in value["selection_recipes"]]
    require(actual == expected,
            "R4 conditional-coverage treatment ladder differs")
    require(value["facility_location"]["initial_uncovered_cosine"] == -1.0
            and value["facility_location"]["training_queries"] == 8141
            and value["facility_location"]["configuration_or_internal_labels"]
            == 0
            and value["facility_location"]["tie_break"]
            == "lowest_global_document_position",
            "R4 conditional-coverage objective differs")
    require(value["model"]["architecture"] == "actual_k32_max"
            and value["model"]["local_input_dimensions"] == 23
            and value["model"]["parameter_count"] == 21837,
            "R4 conditional-coverage model differs")
    require(value["evaluation"]["candidate_fraction_budgets"]
            == [0.003, 0.004, 0.005]
            and value["evaluation"]["strict_prefix"] is True
            and value["decision"]["production_selection_forbidden"] is True,
            "R4 conditional-coverage frontier differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    seeds = len(contract["route"]["seeds"])
    variants = treatments(contract)
    learned = learned_treatments(contract)
    return {
        "dataset": contract["route"]["dataset"],
        "seed_count": seeds,
        "training_query_count": contract["query_partitions"][
            "training_queries"],
        "configuration_query_count": contract["query_partitions"][
            "configuration_queries"],
        "internal_query_count": contract["query_partitions"][
            "internal_evaluation_queries"],
        "treatments": variants,
        "learned_treatments": learned,
        "conditional_materializations": seeds * 4,
        "matched_independent_win_materializations": seeds,
        "frozen_ff32_materializations": seeds,
        "model_fits": seeds * len(learned),
        "frozen_ff32_parent_models": seeds,
        "configuration_rows": seeds * (len(variants) + 2),
        "internal_rows": seeds * (len(variants) + 2),
        "candidate_fraction_budgets": contract["evaluation"][
            "candidate_fraction_budgets"],
        "parameter_count": contract["model"]["parameter_count"],
    }


def self_test(path: Path) -> None:
    value = plan(load_contract(path))
    require(value["model_fits"] == 15
            and value["conditional_materializations"] == 12
            and value["configuration_rows"] == 24
            and value["internal_rows"] == 24,
            "R4 conditional-coverage planner self-test differs")
    print("NeuRoute R4 conditional set-coverage planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).resolve().parent /
                        "neuroute-r4-conditional-set-coverage.example.json")
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
        print(f"plan-neuroute-r4-conditional-set-coverage: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
