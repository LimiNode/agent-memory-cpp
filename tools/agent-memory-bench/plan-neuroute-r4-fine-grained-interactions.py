#!/usr/bin/env python3
"""Validate and summarize the R4 fine-grained interaction ladder."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def parameter_count(local: int, hidden: int) -> int:
    return 12353 + 32 * local + 162 * hidden


def parameter_counts(contract: dict[str, Any]) -> dict[str, int]:
    return {variant: parameter_count(
        int(contract["models"]["local_input_dimensions"][variant]),
        int(contract["models"]["score_hidden_dimensions"][variant]))
            for variant in contract["representations"]["variants"]}


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1
            and value.get("family") == "neuroute_r4_fine_grained_interactions",
            "R4 interaction contract identity differs")
    require(value["route"]["dataset"] == "de-1m"
            and value["route"]["documents"] == 1000000
            and len(value["route"]["seeds"]) == 3,
            "R4 interaction route differs")
    require(value["evaluation"]["candidate_fraction_budgets"]
            == [0.003, 0.004, 0.005]
            and value["evaluation"]["strict_prefix"] is True
            and value["shortlist"]["must_not_change_by_treatment"] is True,
            "R4 interaction frontier differs")
    variants = value["representations"]["variants"]
    require(len(variants) == 7 and variants[0] == "r0_scalar"
            and value["representations"][
                "teacher_trained_representative_selection_forbidden"] is True,
            "R4 interaction variants differ")
    counts = parameter_counts(value)
    require(max(counts.values()) / min(counts.values())
            <= value["models"]["maximum_parameter_count_ratio"],
            "R4 interaction parameter matching differs")
    require(value["decision"][
        "teacher_trained_representative_study_required_after_measurement"] is True
            and value["decision"]["production_selection_forbidden"] is True,
            "R4 interaction decision boundary differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset": contract["route"]["dataset"],
        "seed_count": len(contract["route"]["seeds"]),
        "training_query_count": contract["query_partitions"]["training_queries"],
        "configuration_query_count": contract["query_partitions"][
            "configuration_queries"],
        "internal_query_count": contract["query_partitions"][
            "internal_evaluation_queries"],
        "variants": contract["representations"]["variants"],
        "model_fits": len(contract["route"]["seeds"])
        * len(contract["representations"]["variants"]),
        "candidate_fraction_budgets": contract["evaluation"][
            "candidate_fraction_budgets"],
        "parameter_counts": parameter_counts(contract),
        "teacher_trained_representative_fits": 0,
    }


def self_test(path: Path) -> None:
    value = plan(load_contract(path))
    require(value["model_fits"] == 21
            and value["candidate_fraction_budgets"] == [0.003, 0.004, 0.005]
            and value["teacher_trained_representative_fits"] == 0,
            "R4 interaction planner self-test differs")
    print("NeuRoute R4 fine-grained interaction planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).resolve().parent /
                        "neuroute-r4-fine-grained-interactions.example.json")
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
        print(f"plan-neuroute-r4-fine-grained-interactions: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
