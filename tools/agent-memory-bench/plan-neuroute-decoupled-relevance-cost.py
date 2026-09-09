#!/usr/bin/env python3
"""Validate the frozen R3c decoupled relevance and cost protocol."""

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
            and value.get("family") == "neuroute_decoupled_relevance_cost",
            "decoupled relevance/cost family differs")
    require(value["route"] == {
        "dataset": "de-1m", "documents": 1000000, "width": 16,
        "seeds": [2026082701, 2026082702, 2026082703],
        "prototypes_per_address": 8, "address_shortlist": 1024},
        "decoupled relevance/cost route differs")
    require(value["targets"] == [
        "gain_density_listnet", "cascade_useful_probability",
        "expected_actionable_gain", "graded_top100_top10_cascade",
        "lambda_candidate_boundary"],
        "decoupled relevance target matrix differs")
    require(value["policies"]["variants"] == [
        "predicted_gain", "predicted_gain_per_cost",
        "predicted_gain_minus_lambda_cost",
        "useful_logit_minus_lambda_log_cost"]
            and value["policies"]["hard_unique_candidate_fraction"] == 0.005
            and value["policies"]["skip_non_fitting_addresses"] is True
            and value["policies"]["calibration_and_lambda_configuration_only"]
            is True, "decoupled cost policy differs")
    require(value["target_definition"]["exact_teacher_top_k"] == 100
            and value["target_definition"][
                "posting_size_importance_weighted_bce"] is False,
            "decoupled target definition differs")
    require(value["query_partitions"] == {
        "training_queries": 8141, "configuration_queries": 76,
        "internal_evaluation_queries": 76,
        "training_then_configuration_then_internal_required": True},
        "decoupled partition discipline differs")
    require(value["decision"]["native_confirmation_forbidden"] is True
            and value["decision"]["production_selection_forbidden"] is True,
            "decoupled activation boundary differs")
    require(all(isinstance(digest, str) and len(digest) == 64
                for digest in value["activation"].values()),
            "decoupled activation hashes differ")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    seeds = contract["route"]["seeds"]
    targets = contract["targets"]
    policies = contract["policies"]["variants"]
    lambdas = contract["policies"]["lambda_grid"]
    policy_candidates = (2 + 2 * len(lambdas))
    return {
        "dataset": "de-1m",
        "seed_count": len(seeds),
        "targets": targets,
        "policies": policies,
        "model_fits": len(seeds) * len(targets),
        "configuration_policy_candidates_per_model": policy_candidates,
        "configuration_rows": len(seeds) * len(targets) * policy_candidates,
        "selected_internal_rows": len(seeds) * len(targets),
        "exact_teacher_top_k": 100,
        "hard_unique_candidate_fraction": 0.005,
        "internal_opened_after_selection": True,
    }


def self_test() -> None:
    current = plan(load_contract(Path(__file__).with_name(
        "neuroute-decoupled-relevance-cost.example.json")))
    require(current["model_fits"] == 15
            and current["configuration_policy_candidates_per_model"] == 16
            and current["configuration_rows"] == 240
            and current["selected_internal_rows"] == 15,
            "decoupled relevance/cost planner self-test differs")
    print("NeuRoute decoupled relevance/cost planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).with_name(
        "neuroute-decoupled-relevance-cost.example.json"))
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
        print(f"plan-neuroute-decoupled-relevance-cost: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
