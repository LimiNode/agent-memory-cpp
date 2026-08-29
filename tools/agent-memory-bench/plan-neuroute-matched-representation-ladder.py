#!/usr/bin/env python3
"""Validate and print the frozen matched R0/R1/R2 ladder."""

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
            and value.get("family") == "neuroute_matched_representation_ladder",
            "matched-representation contract family differs")
    require(all(isinstance(digest, str) and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
                for digest in value["activation"].values()),
            "matched-representation activation hashes differ")
    require(value["route"] == {
        "dataset": "de-1m", "documents": 1000000, "width": 16,
        "seeds": [2026082701, 2026082702, 2026082703]},
        "matched-representation route differs")
    require(value["representations"]["variants"] == [
        "r0_scalar", "r1_invariant_raw_k8", "r2_query_gated_raw_k8"]
        and value["representations"]["shared_projection"]["dimensions"] == 64
        and value["representations"]["shared_projection"]["teacher_blind"] is True,
        "matched-representation ladder differs")
    require(value["query_partitions"] == {
        "training_queries": 8141, "configuration_queries": 76,
        "internal_evaluation_queries": 76,
        "training_then_configuration_then_internal_required": True},
        "matched-representation partitions differ")
    require(value["prototype_shortlist"]["requested_prototypes_per_address"] == 8
            and value["prototype_shortlist"]["address_shortlist"] == 1024
            and value["prototype_shortlist"]["shortlist_frozen_before_reranking"]
            is True, "matched-representation shortlist differs")
    require(value["training"][
        "same_teacher_data_optimizer_seeds_for_every_variant"] is True
        and value["evaluation"]["address_budgets"] == [128, 256, 512]
        and value["evaluation"]["headline_address_budget"] == 256,
        "matched-representation comparison differs")
    require(value["decision"]["stateful_policy_forbidden_in_this_batch"] is True
            and value["decision"]["native_confirmation_forbidden"] is True
            and value["decision"]["production_selection_forbidden"] is True,
            "matched-representation activation boundary differs")
    return value


def parameter_counts(contract: dict[str, Any]) -> dict[str, int]:
    query_parameters = 384 * 32 + 32
    joined = 32 * 5
    local_dimensions = {
        "r0_scalar": 22,
        "r1_invariant_raw_k8": 64 * 3 + 3,
        "r2_query_gated_raw_k8": 64 * 3 + 3,
    }
    result = {}
    for variant in contract["representations"]["variants"]:
        score_hidden = int(contract["models"]["score_hidden_dimensions"][variant])
        attention = 64 if variant == "r2_query_gated_raw_k8" else 0
        result[variant] = (query_parameters + local_dimensions[variant] * 32 + 32
                           + joined * score_hidden + score_hidden
                           + score_hidden + 1 + attention)
    return result


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    counts = parameter_counts(contract)
    ratio = max(counts.values()) / min(counts.values())
    require(ratio <= contract["models"]["maximum_parameter_count_ratio"],
            "matched-representation parameter budget differs")
    return {
        "dataset": contract["route"]["dataset"],
        "seed_count": len(contract["route"]["seeds"]),
        "variants": contract["representations"]["variants"],
        "model_fits": len(contract["route"]["seeds"])
        * len(contract["representations"]["variants"]),
        "configuration_rows": 9,
        "internal_rows": 15,
        "parameter_counts": counts,
        "parameter_count_ratio": ratio,
        "address_budgets": contract["evaluation"]["address_budgets"],
        "r3_outside_batch": True,
        "native_confirmation_forbidden": True,
    }


def self_test() -> None:
    current = plan(load_contract(Path(__file__).with_name(
        "neuroute-matched-representation-ladder.example.json")))
    require(current["model_fits"] == 9 and current["internal_rows"] == 15
            and current["parameter_count_ratio"] < 1.02,
            "matched-representation planner self-test differs")
    print("NeuRoute matched-representation planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).with_name(
        "neuroute-matched-representation-ladder.example.json"))
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
        print(f"plan-neuroute-matched-representation-ladder: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
