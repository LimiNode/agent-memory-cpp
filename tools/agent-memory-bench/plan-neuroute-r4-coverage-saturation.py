#!/usr/bin/env python3
"""Validate and summarize the R4 representative-coverage saturation study."""

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
            and value.get("family") == "neuroute_r4_coverage_saturation",
            "R4 coverage-saturation contract identity differs")
    require(value["route"]["dataset"] == "de-1m"
            and value["route"]["documents"] == 1000000
            and len(value["route"]["seeds"]) == 3,
            "R4 coverage-saturation route differs")
    require(value["representatives"]["prefixes"] == [8, 16, 24, 32, 48, 64]
            and value["representatives"]["maximum"] == 64
            and value["representatives"]["strict_prefixes"] is True
            and value["representatives"]["teacher_blind"] is True,
            "R4 coverage-saturation prefix ladder differs")
    require(value["model"]["parent_architecture"] == "actual_k32_max"
            and value["model"]["parameter_count"] == 21837
            and value["model"]["same_optimizer_and_model_seed_for_every_k"]
            is True,
            "R4 coverage-saturation model differs")
    require(value["evaluation"]["candidate_fraction_budgets"]
            == [0.003, 0.004, 0.005]
            and value["configuration_selection"]["ceiling_k"] == 64
            and value["configuration_selection"]["fallback_k"] == 64,
            "R4 coverage-saturation frontier differs")
    require(value["physical_footprints_bytes_per_representative"] == {
        "fp32": 1536, "fp16": 768, "int8_with_scale": 388,
        "int5_simdcomp_with_scale": 244},
            "R4 coverage-saturation footprint assumptions differ")
    return value


def treatments(contract: dict[str, Any]) -> list[str]:
    return [f"actual_k{value}_max" for value in contract["representatives"][
        "prefixes"]]


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    seed_count = len(contract["route"]["seeds"])
    variants = treatments(contract)
    return {
        "dataset": contract["route"]["dataset"],
        "seed_count": seed_count,
        "training_query_count": contract["query_partitions"]["training_queries"],
        "configuration_query_count": contract["query_partitions"][
            "configuration_queries"],
        "internal_query_count": contract["query_partitions"][
            "internal_evaluation_queries"],
        "prefixes": contract["representatives"]["prefixes"],
        "treatments": variants,
        "materialized_seed_artifact_count": seed_count * 3,
        "model_fits": seed_count * (len(variants) - 1),
        "frozen_k32_parent_models": seed_count,
        "configuration_rows": seed_count * (len(variants) + 2),
        "internal_rows": seed_count * (len(variants) + 2),
        "candidate_fraction_budgets": contract["evaluation"][
            "candidate_fraction_budgets"],
        "parameter_count": contract["model"]["parameter_count"],
    }


def self_test(path: Path) -> None:
    value = plan(load_contract(path))
    require(value["model_fits"] == 15
            and value["frozen_k32_parent_models"] == 3
            and value["configuration_rows"] == 24
            and value["internal_rows"] == 24
            and value["materialized_seed_artifact_count"] == 9,
            "R4 coverage-saturation planner self-test differs")
    print("NeuRoute R4 coverage-saturation planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).resolve().parent /
                        "neuroute-r4-coverage-saturation.example.json")
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
        print(f"plan-neuroute-r4-coverage-saturation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
