#!/usr/bin/env python3
"""Validate and summarize the sequential-oracle diagnostic contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
TREATMENTS = [
    "occupied_logit",
    "direct_id",
    "centroid_initialized_id",
    "static_target_gain",
    "static_target_gain_density",
    "cascade_marginal_gain_density",
]


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    require(contract.get("schema_version") == 1
            and contract.get("family") == "neuroute_sequential_oracle_diagnostic",
            "sequential-oracle contract family differs")
    require(set(contract) == {
        "schema_version", "family", "claim_scope", "activation", "route",
        "partition", "evaluation", "cascade", "work_accounting", "decision",
    }, "sequential-oracle contract members differ")
    require(contract["route"]["width"] == 16
            and len(contract["route"]["seeds"]) == 3,
            "sequential-oracle route differs")
    require(contract["partition"] == {
        "source": "german_configuration_selection_query_ids",
        "queries": 76,
        "student_evaluation_partition_forbidden": True,
    }, "sequential-oracle partition differs")
    require(contract["evaluation"]["treatments"] == TREATMENTS
            and contract["evaluation"]["coverage_targets"] == [0.5, 0.75, 0.9, 0.95]
            and contract["evaluation"]["candidate_mass_target"] == 0.1,
            "sequential-oracle evaluation matrix differs")
    require(contract["cascade"] == {
        "oracle_k": 10, "hamming_limit": 768, "adc_limit": 64, "result_k": 10,
    }, "sequential-oracle cascade differs")
    require(contract["decision"]["student_followup_only_if_passed"] is True
            and contract["decision"]["production_selection_forbidden"] is True,
            "sequential-oracle decision differs")
    require(all(isinstance(value, str) and len(value) == 64
                for value in contract["activation"].values()),
            "sequential-oracle activation hashes differ")
    return contract


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    datasets = len(contract["evaluation"]["scales"])
    seeds = len(contract["route"]["seeds"])
    treatments = len(contract["evaluation"]["treatments"])
    return {
        "dataset_count": datasets,
        "seeds_per_dataset": seeds,
        "treatments_per_seed": treatments,
        "rows_per_dataset": seeds * treatments,
        "total_rows": datasets * seeds * treatments,
        "queries_per_row": contract["partition"]["queries"],
        "coverage_targets": contract["evaluation"]["coverage_targets"],
        "student_measurement_expected": False,
    }


def self_test() -> None:
    contract = load_contract(THIS / "neuroute-sequential-oracle-diagnostic.example.json")
    require(plan(contract) == {
        "dataset_count": 3, "seeds_per_dataset": 3, "treatments_per_seed": 6,
        "rows_per_dataset": 18, "total_rows": 54, "queries_per_row": 76,
        "coverage_targets": [0.5, 0.75, 0.9, 0.95],
        "student_measurement_expected": False,
    }, "sequential-oracle plan self-test differs")
    print("NeuRoute sequential-oracle planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-sequential-oracle-diagnostic.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        if args.self_test:
            self_test()
        else:
            print(json.dumps(plan(contract), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-sequential-oracle-diagnostic: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
