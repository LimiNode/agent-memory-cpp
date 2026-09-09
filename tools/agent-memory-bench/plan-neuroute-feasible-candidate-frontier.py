#!/usr/bin/env python3
"""Validate the frozen strict-prefix NeuRoute candidate-work frontier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1
            and value.get("family") == "neuroute_feasible_candidate_frontier",
            "feasible-frontier family differs")
    require(value["route"] == {
        "dataset": "de-1m", "documents": 1000000, "width": 16,
        "seeds": [2026082701, 2026082702, 2026082703],
        "prototypes_per_address": 8, "address_shortlist": 1024},
        "feasible-frontier route differs")
    require(value["evaluation"]["candidate_fraction_budgets"] == [
        0.002, 0.0025, 0.003, 0.0035, 0.004, 0.005, 0.00625]
            and value["evaluation"]["strict_prefix"] is True
            and value["evaluation"]["report_last_feasible_and_first_crossing"]
            is True
            and value["evaluation"]["interpolation_is_descriptive_only"] is True,
            "feasible-frontier budget protocol differs")
    require(value["treatments"] == [
        "prototype_order", "r0_scalar", "r3a_occupancy",
        "r3b_residual_mean", "r3c_residual_shape",
        "privileged_gain_density", "privileged_budget_aware_marginal"],
        "feasible-frontier treatment matrix differs")
    require(value["decision"]["retraining_forbidden"] is True
            and value["decision"]["native_confirmation_forbidden"] is True
            and value["decision"]["production_selection_forbidden"] is True,
            "feasible-frontier activation boundary differs")
    require(all(isinstance(digest, str) and len(digest) == 64
                for digest in value["activation"].values()),
            "feasible-frontier activation hashes differ")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    seeds = contract["route"]["seeds"]
    treatments = contract["treatments"]
    budgets = contract["evaluation"]["candidate_fraction_budgets"]
    return {
        "dataset": "de-1m",
        "seed_count": len(seeds),
        "query_count_per_seed": 76,
        "treatments": treatments,
        "budget_fractions": budgets,
        "aggregate_rows": len(seeds) * len(treatments),
        "query_budget_rows": len(seeds) * len(treatments) * 76 * len(budgets),
        "frozen_model_count": len(seeds) * 4,
        "model_fits": 0,
        "strict_prefix": True,
        "interpolation_deployable": False,
    }


def self_test() -> None:
    current = plan(load_contract(Path(__file__).with_name(
        "neuroute-feasible-candidate-frontier.example.json")))
    require(current["aggregate_rows"] == 21
            and current["query_budget_rows"] == 11172
            and current["model_fits"] == 0,
            "feasible-frontier planner self-test differs")
    print("NeuRoute feasible candidate frontier planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).with_name(
        "neuroute-feasible-candidate-frontier.example.json"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
        else:
            print(json.dumps(plan(load_contract(args.contract)), indent=2,
                             sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-feasible-candidate-frontier: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
